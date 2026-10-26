from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

IMAGE_EXTS = {".webp", ".jpg", ".jpeg", ".png"}
PANEL_W = 800
INK_DIFF = 40
OFF_FRAC = 0.03
LIGHT = 200
PHANTOM_INK = 0.08
CATEGORIES = {
    "missed": "Missed text. A dialogue bubble whose Japanese is still legible in the render, in whole or in part, or that got no English at all. Partial erasure counts. One per bubble. Small moan balloons go under missed_small instead.",
    "missed_small": "Missed small balloon. A balloon that holds only hand-drawn kana (a moan, gasp or breath, about 1 to 4 characters, often with hearts) left untranslated or half erased. One per balloon. Kept apart from missed because these are a detector blind spot, not a placement failure.",
    "outside": "Text outside its bubble. Any English glyph that crosses or touches the bubble outline, sits in the art or page outside the bubble, or lands in a neighbouring bubble. One per bubble.",
    "size": "Badly sized. Text inside its bubble but clearly too small (cap height under half the source glyph height, or visibly smaller than the page's other dialogue while the bubble has room), or crowding the outline (margin under about half a cap height) without crossing it. One per bubble.",
    "placement": "Badly placed. Text visibly off-centre in its bubble while room exists (centre off by more than about 15% of the bubble's size), lines staircased or skewed to follow a false shape, or one lobe's text set across two lobes. One per bubble.",
    "garbage": "Garbage translated. English drawn where there was no dialogue: SFX, hatching, screentone, art, or print that is part of the drawing, set as if it were dialogue. One per drawn block. Not tied to a dialogue bubble, so it adds to the numerator only.",
    "duplicate": "Duplicate text. The same English set twice, in one bubble or across two blocks of one bubble. One per extra copy.",
    "smudge": "Smudge or erased outline. Inpainting damage a reader notices: grey or dark smudges, a broken or erased bubble outline, erased art, or a white patch on art. One per damaged spot.",
}
RUBRIC = f"""## Rubric

Review each page from its sheet: the original with the pipeline's blocks boxed (green translated, orange skipped as unsure, red where Japanese was still detected on the render) next to the render. Read at sheet scale ({PANEL_W} px per panel) and zoom only to settle a call.

**Bubbles (the denominator).** Every text unit a reader expects translated: speech and thought balloons, narration boxes, free-floating dialogue or monologue text, and handwritten asides. A double or touching balloon counts once per lobe that holds its own text. Any balloon outline that holds text counts, even with hand-drawn text in it. Outside a balloon, text set in a typeface counts (a moan lettered in the dialogue font is dialogue) and hand-drawn lettering does not (that is SFX). Signs, screens, logos and other print that is part of the art are not bubbles. A balloon or box holding only punctuation (…, ！, ？) is not a bubble. An afterword or colophon page counts as one bubble. Count bubbles on the original, whether or not the pipeline found them.

**Problems.** A bubble can carry several categories at once. Count each category at most once per bubble unless its definition says otherwise.

""" + "\n".join(f"- **{k}.** {v}" for k, v in CATEGORIES.items()) + """

The headline is problems per 100 bubbles, per category and in total.
"""


def natural_key(path: Path):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", path.stem)]


def holdout_pages(root: Path) -> list[tuple[str, list[Path]]]:
    works = [d for d in sorted(root.iterdir()) if d.is_dir()] or [root]
    return [(d.name, sorted((p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXTS), key=natural_key)) for d in works]


def overlap(a: list[int], b: list[int]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / max(1, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))


def glyph_px(block) -> float:
    mask = block.mask > 0
    if not mask.any():
        return 0.0
    profile = mask.sum(0) if block.vertical else mask.sum(1)
    on = np.concatenate([[0], (profile > 0.1 * profile.max()).astype(int), [0]])
    widths = np.nonzero(np.diff(on) == -1)[0] - np.nonzero(np.diff(on) == 1)[0]
    return float(np.median(widths[widths >= 0.3 * widths.max()]))


def ink_metrics(cv2, typeset, inpainted: np.ndarray, rendered: np.ndarray, bubbles) -> list[dict]:
    gray = cv2.cvtColor(inpainted, cv2.COLOR_RGB2GRAY)
    ink = np.abs(cv2.cvtColor(rendered, cv2.COLOR_RGB2GRAY).astype(int) - gray.astype(int)) > INK_DIFF
    light = (gray >= LIGHT).astype(np.uint8)
    _, light_labels = cv2.connectedComponents(light, connectivity=4)
    _, dark_labels = cv2.connectedComponents((gray < 255 - LIGHT).astype(np.uint8), connectivity=4)
    h, w = gray.shape
    boxes = [b.block.box for b in bubbles]
    n, comp, stats, cents = cv2.connectedComponentsWithStats(cv2.dilate(ink.astype(np.uint8), np.ones((7, 7), np.uint8)), connectivity=8)
    owner = np.full(n, -1)
    for i in range(1, n):
        cx, cy = cents[i]
        d = [max(b.x0 - cx, 0, cx - b.x1) + max(b.y0 - cy, 0, cy - b.y1) for b in boxes]
        owner[i] = int(np.argmin(d)) if d else -1
    ink_owner = np.where(ink, owner[comp], -1)
    out = []
    for i, b in enumerate(bubbles):
        region = typeset.find_region(gray, b.block)
        ys, xs = np.nonzero(ink_owner == i)
        tb = b.block
        my, mx = np.nonzero(tb.mask)
        my, mx = my + tb.mask_box.y0, mx + tb.mask_box.x0
        keep = (my < h) & (mx < w)
        labels = dark_labels if region.dark else light_labels
        home_votes = labels[my[keep], mx[keep]]
        home_votes = home_votes[home_votes > 0]
        home = int(np.bincount(home_votes).argmax()) if home_votes.size else 0
        off = int(np.count_nonzero(labels[ys, xs] != home)) if home else len(ys)
        out.append({
            "on_art": bool(region.on_art),
            "dark": bool(region.dark),
            "region_box": region.box.as_list(),
            "ink_px": int(len(ys)),
            "ink_off_px": off,
            "ink_off_frac": round(off / len(ys), 4) if len(ys) else 0.0,
            "outside": bool(not region.on_art and len(ys) and off / len(ys) > OFF_FRAC),
            "glyph_px": round(glyph_px(tb), 1),
        })
    return out


def leftovers(detect, ocr, engine, rendered: np.ndarray, ink: np.ndarray, result: dict) -> list[dict]:
    blocks = detect.detect(engine.detector, rendered)
    found, _ = ocr.read(engine.ocr, rendered, blocks)
    out = []
    for b in found:
        box = b.block.box.as_list()
        out.append({"box": box, "source": b.source, "bucket": bucket_of(box, ink, result["bubbles"], result["skipped"])})
    return out


def bucket_of(box: list[int], ink: np.ndarray, blocks: list[dict], skipped: list[dict]) -> str:
    if ink[box[1] : box[3], box[0] : box[2]].mean() > PHANTOM_INK:
        return "english_read_as_japanese"
    for kind, items in (("translated", [x for x in blocks if x["translation"]]), ("blank", [x for x in blocks if not x["translation"]]), ("skipped", skipped)):
        if any(overlap(box, x["box"]) > 0.3 for x in items):
            return kind
    return "unmatched"


def rebucket(out: Path, page: dict) -> None:
    stem = out / "pages" / f"{page['work']}__{Path(page['file']).stem}"
    inpainted, rendered = Path(f"{stem}.inpainted.webp"), Path(f"{stem}.rendered.webp")
    if not page["leftover"] or not inpainted.exists():
        return
    gray = lambda p: np.asarray(Image.open(p).convert("L")).astype(int)  # noqa: E731
    ink = np.abs(gray(rendered) - gray(inpainted)) > INK_DIFF
    for r in page["leftover"]:
        r["bucket"] = bucket_of(r["box"], ink, page["blocks"], page["skipped"])


def sheet(original: Image.Image, rendered: Image.Image, page: dict) -> Image.Image:
    left = original.convert("RGB")
    draw = ImageDraw.Draw(left)
    lw = max(2, left.width // 400)
    for i, b in enumerate(page["blocks"]):
        draw.rectangle(b["box"], outline=(0, 170, 0), width=lw)
        draw.text((b["box"][0] + 3, b["box"][1] + 2), str(i), fill=(0, 140, 0), stroke_width=2, stroke_fill="white", font_size=max(14, left.width // 60))
    for s in page["skipped"]:
        draw.rectangle(s["box"], outline=(255, 140, 0), width=lw)
    for r in page["leftover"]:
        draw.rectangle(r["box"], outline=(220, 0, 0), width=lw)
    panels = [p.resize((PANEL_W, round(p.height * PANEL_W / p.width)), Image.LANCZOS) for p in (left, rendered.convert("RGB"))]
    out = Image.new("RGB", (PANEL_W * 2 + 8, max(p.height for p in panels)), "gray")
    out.paste(panels[0], (0, 0))
    out.paste(panels[1], (PANEL_W + 8, 0))
    return out


class Replay:

    def __init__(self, inner, known: dict[str, str]):
        self.inner = inner
        self.name = f"replay+{inner.name}"
        self.known = known

    async def translate(self, lines, context):
        missing = [line for line in lines if line not in self.known]
        fresh = dict(zip(missing, await self.inner.translate(missing, context))) if missing else {}
        return [self.known[line] if line in self.known else fresh[line] for line in lines]

    async def health(self):
        return await self.inner.health()


def git_sha(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


async def run(args) -> None:
    if args.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    import cv2
    import yomikae
    import torch
    from yomikae import detect, ocr, typeset
    from yomikae.config import Settings
    from yomikae.pipeline import Engine

    out = args.out_root / args.label
    (out / "pages").mkdir(parents=True, exist_ok=True)
    source_dir = Path(yomikae.__file__).resolve().parent
    print(f"yomikae from {source_dir} at {git_sha(source_dir)}", flush=True)

    captured: dict = {}
    original_typeset = typeset.typeset

    def capturing(lettering, inpainted, bubbles):
        rendered, sizes = original_typeset(lettering, inpainted, bubbles)
        captured.update(inpainted=inpainted, bubbles=bubbles, rendered=rendered)
        return rendered, sizes

    typeset.typeset = capturing
    settings = replace(Settings.from_env(), cache_dir=Path(tempfile.mkdtemp(prefix="yomikae-bench-")))
    engine = Engine(settings)
    engine.warmup()
    if args.replay:
        known = {b["source"]: b["translation"] for f in (args.replay / "pages").glob("*.json") for b in json.loads(f.read_text())["blocks"]}
        engine.translator = Replay(engine.translator, known)

    pages_out = []
    for work, paths in holdout_pages(args.holdout):
        if args.limit:
            paths = paths[: args.limit]
        for index, path in enumerate(paths):
            key = f"{work}/{path.stem}"
            captured.clear()
            result = await engine.process(path.read_bytes(), work, index)
            rendered = np.asarray(Image.open(engine.cache.rendered_path(result["page_id"])).convert("RGB"))
            image = np.asarray(Image.open(path).convert("RGB"))
            t = time.perf_counter()
            blocks = [dict(b, font_size=s) for b, s in zip(result["bubbles"], result["font_sizes"])]
            ink = np.zeros(image.shape[:2], bool)
            if captured:
                inpainted = captured["inpainted"]
                ink = np.abs(cv2.cvtColor(captured["rendered"], cv2.COLOR_RGB2GRAY).astype(int) - cv2.cvtColor(inpainted, cv2.COLOR_RGB2GRAY).astype(int)) > INK_DIFF
                for b, m in zip(blocks, ink_metrics(cv2, typeset, inpainted, captured["rendered"], captured["bubbles"])):
                    b.update(m)
                    cap = engine.lettering.dialogue.face.ink(b["font_size"], "H")[0] if b["font_size"] else 0
                    b["cap_px"] = cap
                    b["height_ratio"] = round(cap / m["glyph_px"], 3) if m["glyph_px"] and cap else None
                Image.fromarray(inpainted).save(out / "pages" / f"{work}__{path.stem}.inpainted.webp", quality=90)
            with engine.gpu_lock:
                left = leftovers(detect, ocr, engine, rendered, ink, result)
            page = {
                "key": key,
                "work": work,
                "file": path.name,
                "size": [result["width"], result["height"]],
                "blocks": blocks,
                "skipped": result["skipped"],
                "leftover": left,
                "timings_ms": {**result["timings_ms"], "metrics": round((time.perf_counter() - t) * 1000)},
            }
            stem = out / "pages" / f"{work}__{path.stem}"
            Path(f"{stem}.rendered.webp").write_bytes(engine.cache.rendered_path(result["page_id"]).read_bytes())
            Path(f"{stem}.json").write_text(json.dumps(page, ensure_ascii=False, indent=1))
            sheet(Image.open(path), Image.fromarray(rendered), page).save(f"{stem}.sheet.jpg", quality=88)
            pages_out.append(page)
            print(f"{key}: {len(blocks)} blocks, {len(page['skipped'])} skipped, {len(left)} leftover, {json.dumps(page['timings_ms'])}", flush=True)

    meta = {
        "label": args.label,
        "sha": git_sha(source_dir),
        "yomikae": str(source_dir),
        "device": engine.device,
        "gpu_peak_reserved_mb": round(torch.cuda.max_memory_reserved() / 2**20) if engine.device == "cuda" else 0,
        "translator": engine.translator.name,
        "dialogue_font": str(engine.lettering.dialogue.face.paths[0].name),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    review_path = out / "review.json"
    if not review_path.exists():
        template = {p["key"]: {"bubbles": None, **{c: 0 for c in CATEGORIES}, "notes": ""} for p in pages_out}
        review_path.write_text(json.dumps(template, ensure_ascii=False, indent=1))
    score(out)


def automated(pages: list[dict]) -> dict:
    blocks = [b for p in pages for b in p["blocks"]]
    buckets: dict[str, int] = {}
    for p in pages:
        for r in p["leftover"]:
            buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1
    in_bubble = [b for b in blocks if "on_art" in b and not b["on_art"]]
    ratios = [b["height_ratio"] for b in blocks if b.get("height_ratio")]
    timings = {}
    for stage in ("detect", "balloons", "ocr", "translate", "inpaint", "typeset", "total", "metrics"):
        values = [p["timings_ms"].get(stage, 0) for p in pages]
        timings[stage] = {"median": statistics.median(values), "p90": float(np.percentile(values, 90)), "sum": sum(values)} if values else {}
    return {
        "pages": len(pages),
        "blocks": len(blocks),
        "blocks_per_page": round(len(blocks) / max(1, len(pages)), 2),
        "skipped": sum(len(p["skipped"]) for p in pages),
        "blank_translations": sum(1 for b in blocks if not b["translation"]),
        "on_art_blocks": sum(1 for b in blocks if b.get("on_art")),
        "leftover_japanese": buckets,
        "outside_ink_blocks": sum(1 for b in in_bubble if b["outside"]),
        "outside_ink_frac_of_bubble_blocks": round(sum(1 for b in in_bubble if b["outside"]) / max(1, len(in_bubble)), 4),
        "height_ratio": {
            "median": round(statistics.median(ratios), 3) if ratios else None,
            "p10": round(float(np.percentile(ratios, 10)), 3) if ratios else None,
            "p90": round(float(np.percentile(ratios, 90)), 3) if ratios else None,
            "under_0.5": sum(1 for r in ratios if r < 0.5),
        },
        "timings_ms": timings,
    }


def reviewed(review: dict) -> dict | None:
    done = {k: v for k, v in review.items() if v.get("bubbles") is not None}
    if not done:
        return None
    bubbles = sum(v["bubbles"] for v in done.values())
    counts = {c: sum(v.get(c, 0) for v in done.values()) for c in CATEGORIES}
    per_100 = {c: round(100 * n / max(1, bubbles), 1) for c, n in counts.items()}
    total = sum(counts.values())
    by_work: dict[str, dict] = {}
    for k, v in done.items():
        w = by_work.setdefault(k.split("/")[0], {"bubbles": 0, "problems": 0})
        w["bubbles"] += v["bubbles"]
        w["problems"] += sum(v.get(c, 0) for c in CATEGORIES)
    for w in by_work.values():
        w["per_100"] = round(100 * w["problems"] / max(1, w["bubbles"]), 1)
    return {
        "pages_reviewed": len(done),
        "pages_total": len(review),
        "bubbles": bubbles,
        "counts": counts,
        "per_100": per_100,
        "total": total,
        "total_per_100": round(100 * total / max(1, bubbles), 1),
        "by_work": by_work,
    }


def score(out: Path) -> None:
    pages = [json.loads(f.read_text()) for f in sorted((out / "pages").glob("*.json"))]
    for page in pages:
        rebucket(out, page)
    meta = json.loads((out / "meta.json").read_text())
    review_path = out / "review.json"
    review = json.loads(review_path.read_text()) if review_path.exists() else {}
    auto = automated(pages)
    rev = reviewed(review)
    summary = {"meta": meta, "automated": auto, "reviewed": rev}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))

    lines = [f"# Benchmark {meta['label']}", ""]
    lines.append(f"Commit `{meta['sha']}`, {auto['pages']} pages, device `{meta['device']}`, translator `{meta['translator']}`, dialogue font `{meta['dialogue_font']}`.")
    lines.append("")
    if rev:
        lines += [f"## Problems per 100 bubbles", "", f"{rev['bubbles']} bubbles on {rev['pages_reviewed']} of {rev['pages_total']} reviewed pages. **Total: {rev['total_per_100']} per 100** ({rev['total']} problems).", "", "| Category | Count | Per 100 bubbles |", "| --- | ---: | ---: |"]
        lines += [f"| {c} | {rev['counts'][c]} | {rev['per_100'][c]} |" for c in CATEGORIES]
        lines += ["", "| Work | Bubbles | Problems | Per 100 |", "| --- | ---: | ---: | ---: |"]
        lines += [f"| {w} | {v['bubbles']} | {v['problems']} | {v['per_100']} |" for w, v in rev["by_work"].items()]
        lines.append("")
    else:
        lines += ["## Problems per 100 bubbles", "", f"Not reviewed yet. Fill `{review_path}` from the sheets in `pages/`, then rerun with `--score`.", ""]
    hr = auto["height_ratio"]
    lines += [
        "## Automated metrics",
        "",
        f"- Blocks: {auto['blocks']} ({auto['blocks_per_page']} per page), {auto['skipped']} skipped as unsure, {auto['blank_translations']} blank translations, {auto['on_art_blocks']} set on art (no bubble found).",
        f"- Japanese detected and read again on the render, by what the original had there: {json.dumps(auto['leftover_japanese'])}. `translated` means a partial or missed erase inside a translated block, `blank` a failed translation, `skipped` an unsure block left alone, `unmatched` text the pipeline dropped (OCR filters), `english_read_as_japanese` manga-ocr reading our own lettering.",
        f"- English ink off its bubble (more than {OFF_FRAC:.0%} of a block's ink on outline, art, or another light region): {auto['outside_ink_blocks']} blocks, {auto['outside_ink_frac_of_bubble_blocks']:.1%} of blocks set in a bubble.",
        f"- Cap height over source glyph size: median {hr['median']}, p10 {hr['p10']}, p90 {hr['p90']}, {hr['under_0.5']} blocks under 0.5.",
        "",
        "| Stage | Median ms | p90 ms | Sum ms |",
        "| --- | ---: | ---: | ---: |",
    ]
    lines += [f"| {s} | {t['median']} | {t['p90']:.0f} | {t['sum']} |" for s, t in auto["timings_ms"].items() if t]
    if rev:
        notes = [(k, v["notes"]) for k, v in review.items() if v.get("notes")]
        if notes:
            lines += ["", "## Review notes", ""] + [f"- `{k}`: {n}" for k, n in notes]
    lines += ["", RUBRIC]
    (out / "scorecard.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {out / 'scorecard.md'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("holdout", type=Path, nargs="?", help="directory of pages, or of one subdirectory of pages per work")
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--device", choices=("cpu", "auto"), default="auto")
    ap.add_argument("--replay", type=Path, help="earlier run directory whose translations to reuse")
    ap.add_argument("--limit", type=int, default=0, help="pages per work, for a smoke run")
    ap.add_argument("--score", action="store_true", help="only rescore an existing run from its review.json")
    args = ap.parse_args()
    if args.score:
        score(args.out_root / args.label)
        return
    if args.holdout is None:
        sys.exit("holdout directory required unless --score")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
