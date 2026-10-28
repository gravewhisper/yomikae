from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
COMET_PYTHON = SERVER_DIR / ".venv-comet" / "bin" / "python"
COMET_MODEL = "Unbabel/wmt22-comet-da"
COMET_GPU_MIN_FREE = 4 * 1024**3
BOOTSTRAP = 1000
SAMPLES = 30
SUFFIXES = {"さん": "san", "ちゃん": "chan", "くん": "kun", "君": "kun", "さま": "sama", "様": "sama", "先輩": "senpai", "先生": "sensei", "殿": "dono"}
NOT_NAMES = ("父", "母", "兄", "姉", "娘", "息子", "客", "店長", "葬儀屋", "皆", "貴", "昨日", "許", "見", "婆", "爺", "ボク", "カエル")
KATAKANA = re.compile(r"[\u30a1-\u30fa\u30fc]")
KANJI = re.compile(r"[\u4e00-\u9fff\u3005]")
SUFFIX = re.compile("|".join(SUFFIXES))
WORDY = re.compile(r"\w")
JAPANESE_TEXT = re.compile(r"[\u3040-\u309f\u30a0-\u30fa\u3400-\u9fff\uff66-\uff9f]")
TITLE_GLYPH = 0.065
LOOKAHEAD_REACH = 3
OPENERS = 3
FINGERPRINT_KEYS = ("sha", "translator", "base_url", "reasoning_effort", "image", "image_side", "lookahead", "context_skip_filler", "context_stage", "stage_dir", "dataset_hash", "series", "limit", "pages", "chapter_starts")
ROMAJI_SUFFIX = re.compile(r"-\s?(" + "|".join(sorted(set(SUFFIXES.values()))) + r")\b", re.I)


def load_pages(annotation: Path, series: list[str]) -> tuple[list[dict], int]:
    pages, dropped = [], 0
    for book in json.loads(annotation.read_text()):
        if series and book["book_title"] not in series:
            continue
        for page in sorted(book["pages"], key=lambda p: p["page_index"]):
            kept = [t for t in page["text"] if t["text_ja"].strip()]
            lines = [{"ja": t["text_ja"].replace("\n", ""), "ref": t["text_en"], **({"ruby": t["ruby"]} if t.get("ruby") else {})} for t in kept]
            boxes = [(t["x"], t["y"], t["x"] + t["w"], t["y"] + t["h"]) for t in kept]
            dropped += len(page["text"]) - len(lines)
            image = annotation.parent / page["image_paths"]["ja"]
            pages.append({"series": book["book_title"], "page_index": page["page_index"], "lines": lines, "boxes": boxes, "image": image})
    return pages, dropped


def filler(blocks: list[tuple[tuple[int, int, int, int], str, bool | None]], height: int) -> bool:
    found = [(box, len(JAPANESE_TEXT.findall(text)), inside) for box, text, inside in blocks if JAPANESE_TEXT.search(text)]
    if not found:
        return True
    wide = [(x1 - x0) > 1.3 * (y1 - y0) and n >= 3 for (x0, y0, x1, y1), n, _ in found]
    glyph = [math.sqrt((x1 - x0) * (y1 - y0) / n) / height for (x0, y0, x1, y1), n, _ in found]
    if all(inside is False for _, _, inside in found) and (len(found) <= 4 or any(wide)):
        return True
    if len(found) <= 3 and any(g >= TITLE_GLYPH and (w or inside is False) for g, w, (_, _, inside) in zip(glyph, wide, found)):
        return True
    return len(found) <= 2 and sum(n for _, n, _ in found) <= 12


def outline(annotation: Path, stage_dir: Path | None) -> dict[str, list[dict]]:
    from PIL import Image

    books = json.loads(annotation.read_text())
    pages: dict[str, list[dict]] = {}
    if stage_dir:
        series = [b["book_title"] for b in books]
        if len(series) != 1:
            sys.exit("--stage-dir needs an annotation with one book")
        for f in sorted(stage_dir.glob("p*.json")):
            stage = json.loads(f.read_text())
            with Image.open(stage["image"]) as im:
                height = im.height
            blocks = [(tuple(b["box"]), b["text"], b["balloon"] is not None) for b in stage["blocks"]]
            pages.setdefault(series[0], []).append({"page_index": int(f.stem[1:]), "lines": [b[1] for b in blocks if JAPANESE_TEXT.search(b[1])], "filler": filler(blocks, height)})
        return pages
    for book in books:
        for page in sorted(book["pages"], key=lambda p: p["page_index"]):
            with Image.open(annotation.parent / page["image_paths"]["ja"]) as im:
                height = im.height
            blocks = [((t["x"], t["y"], t["x"] + t["w"], t["y"] + t["h"]), t["text_ja"].replace("\n", ""), None) for t in page["text"]]
            pages.setdefault(book["book_title"], []).append({"page_index": page["page_index"], "lines": [b[1] for b in blocks if JAPANESE_TEXT.search(b[1])], "filler": filler(blocks, height)})
    return pages


def upcoming(pages: list[dict], page_index: int, count: int) -> list[dict]:
    after = [p for p in pages if page_index < p["page_index"] <= page_index + LOOKAHEAD_REACH + count - 1 and not p["filler"]]
    return after[:count]


def pick_pages(pages: list[dict], ahead: dict[str, list[dict]], spec: str, starts: list[int]) -> list[dict]:
    if spec != "openers":
        wanted = [item.rpartition(":") for item in spec.split(",")]
        return [p for p in pages if any(str(p["page_index"]) == index and series in ("", p["series"]) for series, _, index in wanted)]
    keep = set()
    for series, outlined in ahead.items():
        fill = {p["page_index"]: p["filler"] for p in outlined}
        have = [p["page_index"] for p in pages if p["series"] == series]
        marks = starts or [min(fill, default=0)]
        for i, start in enumerate(marks):
            end = marks[i + 1] if i + 1 < len(marks) else math.inf
            keep |= {(series, index) for index in [index for index in have if start <= index < end and not fill.get(index, False)][:OPENERS]}
    return [p for p in pages if (p["series"], p["page_index"]) in keep]


def fingerprint(meta: dict) -> str | None:
    if meta["dirty"] or meta["sha"] == "unknown":
        return None
    return hashlib.sha256(json.dumps({k: meta[k] for k in FINGERPRINT_KEYS}, sort_keys=True).encode()).hexdigest()[:16]


def finished_twin(out_root: Path, label: str, wanted: str, pages: list[dict]) -> Path | None:
    for meta_path in sorted(out_root.glob("*/meta.json")):
        run_dir = meta_path.parent
        if run_dir.name == label:
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except ValueError:
            continue
        if meta.get("fingerprint") == wanted and meta.get("finished") and all(done(page_path(run_dir, p["series"], p["page_index"])) for p in pages if not p.get("context_only")):
            return run_dir
    return None


def page_path(out: Path, series: str, page_index: int) -> Path:
    return out / "pages" / f"{series}__{page_index:03d}.json"


def git_sha(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def git_dirty(path: Path) -> bool:
    try:
        return bool(subprocess.run(["git", "-C", str(path), "status", "--porcelain", "--", "."], capture_output=True, text=True, check=True).stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return True


async def run(args) -> None:
    import yomikae
    from yomikae.cache import ContextStore
    from yomikae.config import Settings
    from PIL import Image

    from yomikae.model import Box, ContextLine
    from yomikae.pipeline import translate_page
    from yomikae.translate import TranslatorFailed, TranslatorUnavailable, make_translator, page_image

    out = args.out_root / args.label
    (out / "pages").mkdir(parents=True, exist_ok=True)
    source_dir = Path(yomikae.__file__).resolve().parent
    settings = replace(Settings.from_env(), translator="openai", openai_base_url=args.base_url.rstrip("/"), openai_model=args.model, openai_reasoning_effort=args.reasoning_effort)
    probe = make_translator(settings)
    problem = await probe.health()
    if problem:
        sys.exit(problem)
    if args.chapter_starts and args.pages != "openers":
        sys.exit("--chapter-starts needs --pages openers")
    if args.pages and args.context_stage:
        sys.exit("--pages and --context-stage do not go together")
    pages, dropped = load_pages(args.annotation, args.series)
    if args.limit:
        pages = [p for p in pages if sum(q["series"] == p["series"] and q["page_index"] <= p["page_index"] for q in pages) <= args.limit]
    ahead = outline(args.annotation, args.stage_dir) if args.lookahead or args.context_skip_filler or args.context_stage or args.pages == "openers" else {}
    if args.chapter_starts and len(ahead) != 1:
        sys.exit("--chapter-starts needs an annotation with one book")
    if args.pages:
        pages = pick_pages(pages, ahead, args.pages, args.chapter_starts)
    meta = {
        "label": args.label,
        "sha": git_sha(source_dir),
        "dirty": git_dirty(source_dir.parent),
        "yomikae": str(source_dir),
        "translator": probe.name,
        "base_url": settings.openai_base_url,
        "reasoning_effort": settings.openai_reasoning_effort,
        "image": args.image,
        "image_side": args.image_side,
        "lookahead": args.lookahead,
        "context_skip_filler": args.context_skip_filler,
        "context_stage": args.context_stage,
        "stage_dir": str(args.stage_dir) if args.stage_dir else None,
        "dataset": str(args.annotation),
        "dataset_sha": git_sha(args.annotation.parent),
        "dataset_hash": hashlib.sha256(args.annotation.read_bytes()).hexdigest()[:16],
        "series": args.series,
        "limit": args.limit,
        "pages": args.pages,
        "chapter_starts": args.chapter_starts,
        "jobs": args.jobs,
        "blank_source_lines_dropped": dropped,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    meta["fingerprint"] = fingerprint(meta)
    twin = finished_twin(args.out_root, args.label, meta["fingerprint"], pages) if args.reuse and meta["fingerprint"] else None
    if twin:
        shutil.copytree(twin / "pages", out / "pages", dirs_exist_ok=True)
        for name in ("names.json", "comet.json"):
            if (twin / name).exists():
                shutil.copy(twin / name, out / name)
        meta.update(reused_from=twin.name, finished=meta["started"], wall_s_this_session=0.0)
        (out / "meta.json").write_text(json.dumps(meta, indent=1))
        print(f"reusing {twin.name}: finished run with fingerprint {meta['fingerprint']}, nothing to translate", flush=True)
        return
    if args.reuse:
        print(f"no finished run in {args.out_root} with fingerprint {meta['fingerprint']}{' (uncommitted changes, never reused)' if meta['dirty'] else ''}, translating", flush=True)
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"yomikae from {source_dir} at {meta['sha']}, {probe.name} at {settings.openai_base_url}, {len(pages)} pages, {args.jobs} at once", flush=True)

    fill = {(series, p["page_index"]): p["filler"] for series, ps in ahead.items() for p in ps} if args.context_skip_filler else {}
    if args.context_stage:
        if not args.stage_dir:
            sys.exit("--context-stage needs --stage-dir")
        known = {(p["series"], p["page_index"]) for p in pages}
        extra = [{"series": series, "page_index": p["page_index"], "lines": [{"ja": line, "ref": ""} for line in p["lines"]], "boxes": [], "image": None, "context_only": True} for series, ps in ahead.items() for p in ps if (series, p["page_index"]) not in known]
        pages = sorted(pages + extra, key=lambda p: (p["series"], p["page_index"]))
        (out / "context" / "pages").mkdir(parents=True, exist_ok=True)
    works = {series: [p for p in pages if p["series"] == series] for series in dict.fromkeys(p["series"] for p in pages)}
    stores = {series: ContextStore() for series in works}
    todo = [p for p in pages if not done(page_path(out / "context" if p.get("context_only") else out, p["series"], p["page_index"]))]
    queue = sorted(works, key=lambda series: -len(works[series]))
    started, finished = time.perf_counter(), 0

    async def translate_work(series: str, work: list[dict]) -> None:
        nonlocal finished
        translator = make_translator(settings)
        store = stores[series]
        for page in work:
            index = page["page_index"]
            path = page_path(out / "context" if page.get("context_only") else out, series, index)
            if done(path):
                saved = json.loads(path.read_text())
                store.add(series, str(index), index, [ContextLine(line["ja"], line["hyp"]) for line in saved["lines"]], filler=fill.get((series, index), False))
                continue
            sources = [line["ja"] for line in page["lines"]]
            recalled = store.recall(series, sources)
            error = None
            page_img = None
            if args.image != "off" and sources:
                with Image.open(page["image"]) as im:
                    page_img = page_image(im, [Box(*b) for b in page["boxes"]], args.image == "tagged", args.image_side)
            nexts = upcoming(ahead.get(series, []), index, args.lookahead) if args.lookahead else []
            tokens_before = translator.prompt_tokens
            t = time.perf_counter()
            try:
                ruby = [tuple(pair) for line in page["lines"] for pair in line.get("ruby", [])]
                hyps = await translate_page(translator, store, sources, series, index, page_img, ruby, upcoming=[p["lines"] for p in nexts]) if sources else []
            except (TranslatorUnavailable, TranslatorFailed) as e:
                hyps, error = [""] * len(sources), str(e)
            ms = round((time.perf_counter() - t) * 1000)
            if error is None:
                store.add(series, str(index), index, [ContextLine(ja, hyp) for ja, hyp in zip(sources, hyps)], filler=fill.get((series, index), False))
            record = {
                "series": series,
                "page_index": index,
                "ms": ms,
                "prompt_tokens": translator.prompt_tokens - tokens_before,
                "error": error,
                "upcoming": [p["page_index"] for p in nexts],
                "lines": [{"line_index": i, **line, "hyp": hyp, "recalled": known is not None} for i, (line, hyp, known) in enumerate(zip(page["lines"], hyps, recalled))],
            }
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1))
            finished += 1
            per_page = (time.perf_counter() - started) / finished
            print(f"{series} p{index}: {len(sources)} lines in {ms} ms{' ERROR ' + error if error else ''}, {len(todo) - finished} pages left, about {per_page * (len(todo) - finished) / 60:.1f} min", flush=True)

    async def worker() -> None:
        while queue:
            series = queue.pop(0)
            await translate_work(series, works[series])

    await asyncio.gather(*(worker() for _ in range(args.jobs)))
    learned = {series: [asdict(e) for e in store.saved_names(series)] for series, store in stores.items() if store.saved_names(series)}
    if learned:
        (out / "names.json").write_text(json.dumps(learned, ensure_ascii=False, indent=1))
    meta["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta["wall_s_this_session"] = round(time.perf_counter() - started, 1)
    (out / "meta.json").write_text(json.dumps(meta, indent=1))


def done(path: Path) -> bool:
    return path.exists() and json.loads(path.read_text())["error"] is None


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", ROMAJI_SUFFIX.sub(lambda m: "-" + m.group(1), text)).strip()


def read_lines(out: Path) -> tuple[list[dict], list[dict]]:
    pages = [json.loads(f.read_text()) for f in sorted((out / "pages").glob("*.json"))]
    lines = [{"series": p["series"], "page_index": p["page_index"], **line} for p in pages for line in p["lines"]]
    return pages, lines


def comet_gpus(device: str) -> int:
    if device == "cpu":
        return 0
    import torch

    if not torch.cuda.is_available():
        print(f"COMET on cpu: {'this torch build has no CUDA' if torch.version.cuda is None else 'CUDA is not available'}", flush=True)
        return 0
    free, _ = torch.cuda.mem_get_info()
    if free < COMET_GPU_MIN_FREE:
        print(f"COMET on cpu: the GPU has {free / 2**30:.1f} GiB free, under the {COMET_GPU_MIN_FREE / 2**30:.0f} GiB it needs", flush=True)
        return 0
    print("COMET on cuda", flush=True)
    return 1


def comet(out: Path, model_name: str, device: str = "cpu") -> None:
    from comet import download_model, load_from_checkpoint

    triples = json.loads((out / "comet-todo.json").read_text())
    model = load_from_checkpoint(download_model(model_name))
    data = [{"src": src, "mt": mt, "ref": ref} for src, mt, ref in triples]
    gpus = comet_gpus(device)
    try:
        result = model.predict(data, batch_size=32, gpus=gpus, progress_bar=False)
    except RuntimeError as e:
        if not gpus or "out of memory" not in str(e):
            raise
        print("COMET ran out of GPU memory, scoring on cpu", flush=True)
        result = model.predict(data, batch_size=32, gpus=0, progress_bar=False)
    (out / "comet-done.json").write_text(json.dumps(result.scores))


def triple(line: dict) -> str:
    return json.dumps([line["ja"], clean(line["hyp"]), clean(line["ref"])], ensure_ascii=False)


def earlier_scores(out: Path, model_name: str) -> dict[str, float]:
    known = {}
    for summary in out.parent.glob("*/summary.json"):
        if summary.parent == out:
            continue
        try:
            if json.loads(summary.read_text()).get("comet_model") != model_name:
                continue
            rows = [json.loads(row) for row in (summary.parent / "lines.jsonl").read_text().splitlines()]
        except (OSError, ValueError):
            continue
        known.update((triple(row), row["comet"]) for row in rows if row.get("comet") is not None)
    return known


def comet_scores(out: Path, args, lines: list[dict]) -> tuple[dict[str, float], int]:
    scored = {f"{line['series']}/{line['page_index']}/{line['line_index']}": triple(line) for line in lines if line["ref"].strip()}
    path = out / "comet.json"
    if path.exists():
        cached = json.loads(path.read_text())
        if cached["model"] == args.comet_model and set(cached["scores"]) == set(scored) and cached.get("triples", scored) == scored:
            return cached["scores"], cached.get("reused", 0)
    known = {} if args.comet_fresh else earlier_scores(out, args.comet_model)
    todo = sorted(set(scored.values()) - set(known))
    reused = sum(t in known for t in scored.values())
    print(f"COMET: {reused} of {len(scored)} lines already scored in earlier runs with the same source, output and reference", flush=True)
    if todo:
        python = args.comet_python or (COMET_PYTHON if COMET_PYTHON.exists() else Path(sys.executable))
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""} if args.comet_device == "cpu" else dict(os.environ)
        print(f"scoring {len(todo)} lines with {args.comet_model} under {python} on {args.comet_device}", flush=True)
        clock = time.perf_counter()
        (out / "comet-todo.json").write_text(json.dumps([json.loads(t) for t in todo], ensure_ascii=False))
        subprocess.run([str(python), "-I", str(Path(__file__).resolve()), "--comet-model", args.comet_model, "--comet-device", args.comet_device, "--comet-only", "--out-root", str(out.parent), "--label", out.name], env=env, check=True)
        known.update(zip(todo, json.loads((out / "comet-done.json").read_text())))
        (out / "comet-todo.json").unlink()
        (out / "comet-done.json").unlink()
        print(f"COMET took {time.perf_counter() - clock:.0f} s", flush=True)
    scores = {key: known[t] for key, t in scored.items()}
    path.write_text(json.dumps({"model": args.comet_model, "scores": scores, "triples": scored, "reused": reused}, ensure_ascii=False, indent=1))
    return scores, reused


def bootstrap(values: list[float], seed: int = 0) -> tuple[float, float]:
    import numpy as np

    rng = np.random.default_rng(seed)
    arr = np.asarray(values)
    means = arr[rng.integers(0, len(arr), (BOOTSTRAP, len(arr)))].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def name_honorifics(ja: str) -> list[tuple[str, str]]:
    found = []
    for m in SUFFIX.finditer(ja):
        start = m.start()
        for script in (KATAKANA, KANJI):
            while start > 0 and script.fullmatch(ja[start - 1]):
                start -= 1
            if start < m.start():
                break
        name = ja[start : m.start()]
        if not name or (start > 0 and ja[start - 1] in "おご"):
            continue
        if KATAKANA.fullmatch(name[0]) and len(name) < 2:
            continue
        if name.endswith(NOT_NAMES):
            continue
        found.append((name, SUFFIXES[m.group(0)]))
    return found


def keeps(text: str, suffixes: set[str]) -> bool:
    return suffixes <= {m.group(1).lower() for m in ROMAJI_SUFFIX.finditer(text)}


def chrf(hyps: list[str], refs: list[str], lowercase: bool) -> float:
    from sacrebleu.metrics import CHRF

    return round(CHRF(lowercase=lowercase).corpus_score(hyps, [refs]).score, 2)


def summarize(lines: list[dict], scores: dict[str, float]) -> dict:
    scored = [line for line in lines if line["ref"].strip()]
    hyps, refs = [clean(line["hyp"]) for line in scored], [clean(line["ref"]) for line in scored]
    values = [scores[f"{line['series']}/{line['page_index']}/{line['line_index']}"] for line in scored]
    low, high = bootstrap(values)
    return {
        "lines": len(lines),
        "scored": len(scored),
        "comet": round(statistics.fmean(values), 4),
        "comet_ci95": [round(low, 4), round(high, 4)],
        "chrf": chrf(hyps, refs, lowercase=True),
        "chrf_cased": chrf(hyps, refs, lowercase=False),
        "blank": sum(not line["hyp"].strip() for line in lines),
        "blank_punctuation": sum(not line["hyp"].strip() and not WORDY.search(line["ja"]) for line in lines),
        "recalled": sum(line["recalled"] for line in lines),
    }


def honorific_report(lines: list[dict]) -> dict:
    cases = []
    for line in lines:
        found = name_honorifics(line["ja"])
        if found:
            suffixes = {s for _, s in found}
            cases.append({
                "series": line["series"], "page_index": line["page_index"], "ja": line["ja"], "names": ["-".join(f) for f in found],
                "ref": line["ref"], "hyp": line["hyp"], "hyp_keeps": keeps(line["hyp"], suffixes), "ref_keeps": keeps(line["ref"], suffixes),
            })
    return {
        "lines": len(cases),
        "hyp_keeps": sum(c["hyp_keeps"] for c in cases),
        "ref_keeps": sum(c["ref_keeps"] for c in cases),
        "cases": cases,
    }


def pct(n: int, d: int) -> str:
    return f"{n / d:.0%}" if d else "n/a"


def score(out: Path, args) -> None:
    meta = json.loads((out / "meta.json").read_text())
    pages, lines = read_lines(out)
    scores, reused = comet_scores(out, args, lines)
    with (out / "lines.jsonl").open("w") as f:
        for line in lines:
            key = f"{line['series']}/{line['page_index']}/{line['line_index']}"
            f.write(json.dumps({**line, "comet": scores.get(key)}, ensure_ascii=False) + "\n")
    series = sorted({line["series"] for line in lines})
    system = summarize(lines, scores)
    by_series = {s: summarize([line for line in lines if line["series"] == s], scores) for s in series}
    hon = honorific_report(lines)
    (out / "honorifics.json").write_text(json.dumps(hon, ensure_ascii=False, indent=1))
    ms = sorted(p["ms"] for p in pages)
    failed = [f"{p['series']} p{p['page_index']}: {p['error']}" for p in pages if p["error"]]
    timing = {
        "pages": len(pages),
        "median_ms": round(statistics.median(ms)),
        "p90_ms": ms[int(0.9 * (len(ms) - 1))],
        "total_s": round(sum(ms) / 1000),
        "ms_per_line": round(sum(ms) / max(1, system["lines"] - system["recalled"])),
        "median_prompt_tokens": round(statistics.median(p.get("prompt_tokens", 0) for p in pages)),
    }
    summary = {"meta": meta, "comet_model": args.comet_model, "comet_reused_lines": reused, "system": system, "by_series": by_series, "honorifics": {k: v for k, v in hon.items() if k != "cases"}, "timing": timing, "failed_pages": failed}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))

    s = system
    md = [f"# Translation benchmark {meta['label']}", ""]
    md.append(f"OpenMantra, translation only: gold Japanese per bubble through `yomikae.pipeline.translate_page`, one page per call in reading order, one context key per series. Commit `{meta['sha']}`, translator `{meta['translator']}` at `{meta['base_url']}`, reasoning_effort `{meta['reasoning_effort'] or 'unset'}`, {timing['pages']} pages, {s['lines']} lines ({meta['blank_source_lines_dropped']} lines with blank Japanese dropped).")
    if meta.get("pages"):
        md += ["", f"Pages: only `{meta['pages']}`{' with chapters starting at ' + ', '.join(map(str, meta['chapter_starts'])) if meta.get('chapter_starts') else ''}. Skipped pages never entered context, so compare these scores only with runs of the same pages."]
    if meta.get("reused_from"):
        md += ["", f"Translations reused from run `{meta['reused_from']}`, a finished run with the same fingerprint `{meta['fingerprint']}` (commit, translator, settings, page selection and dataset). Nothing was translated again; the timing below is that run's."]
    md += ["", "## Scores", ""]
    md.append(f"COMET `{args.comet_model}` with a 95% bootstrap interval over lines ({BOOTSTRAP} resamples), chrF case-insensitive and cased. {s['scored']} lines have a reference; blank output counts as an empty hypothesis. {reused} of them took their score from an earlier run in the same folder with the same source, output and reference.")
    md += ["", "| Series | Lines | COMET | 95% CI | chrF | chrF cased | Blank |", "| --- | ---: | ---: | --- | ---: | ---: | ---: |"]
    for name, v in [("**all**", s)] + list(by_series.items()):
        md.append(f"| {name} | {v['scored']} | {v['comet']:.4f} | {v['comet_ci95'][0]:.4f} to {v['comet_ci95'][1]:.4f} | {v['chrf']} | {v['chrf_cased']} | {v['blank']} |")
    md += [
        "",
        "## Rule checks",
        "",
        f"- Honorifics on names: {hon['lines']} source lines attach さん, ちゃん, くん, 様, 先輩, 先生 or 殿 to a name. The output keeps the romanized form on {hon['hyp_keeps']} ({pct(hon['hyp_keeps'], hon['lines'])}); the professional reference keeps it on {hon['ref_keeps']} ({pct(hon['ref_keeps'], hon['lines'])}). Every counted line is in `honorifics.json`.",
        f"- Blank or failed lines: {s['blank']} of {s['lines']}, of which {s['blank_punctuation']} have a source of only punctuation such as ？ or …, which the echo check in `translate_lines` blanks. Pages where the translator was unreachable: {len(failed)}." + "".join(f"\n  - {f}" for f in failed),
        f"- Lines answered from series memory without a model call: {s['recalled']}.",
        "",
        "## Timing",
        "",
        f"Per page, translation only, with up to {meta.get('jobs', 1)} pages from different series in flight at once: median {timing['median_ms']} ms, p90 {timing['p90_ms']} ms, {timing['total_s']} s over {timing['pages']} pages, {timing['ms_per_line']} ms per translated line, median {timing['median_prompt_tokens']} prompt tokens. Page image sent: {meta.get('image', 'off')}.",
        "",
        "## Method",
        "",
        "- Japanese has its newlines removed, as the server's OCR would give one string per bubble. References and output have whitespace collapsed and `- san` joined to `-san` before scoring.",
        "- Each series gets a fresh context store, fed page by page with the model's own output, so previous pages, the name glossary and memory recall work as in a first read on the server.",
        "- A name is a katakana run of two or more, or a kanji run directly before the suffix and not after お or ご, minus a stoplist of common nouns. Names written in hiragana and kinship terms like お姉ちゃん are not counted.",
    ]
    (out / "scorecard.md").write_text("\n".join(md) + "\n")

    picks = random.Random(0).sample(lines, min(SAMPLES, len(lines)))
    sm = [f"# Samples from {meta['label']}", "", f"{len(picks)} random lines (seed 0), in dataset order, for human review.", ""]
    for line in sorted(picks, key=lambda x: (x["series"], x["page_index"], x["line_index"])):
        sm += [f"**{line['series']} p{line['page_index']} #{line['line_index']}**", "", f"- ja: {line['ja']}", f"- ref: {clean(line['ref'])}", f"- hyp: {clean(line['hyp']) or '(blank)'}", ""]
    (out / "samples.md").write_text("\n".join(sm))
    print(f"wrote {out / 'scorecard.md'}")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("annotation", type=Path, nargs="?", help="OpenMantra annotation.json")
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--base-url", default="http://localhost:1234/v1")
    ap.add_argument("--model", default="google/gemma-4-12b")
    ap.add_argument("--reasoning-effort", default="none", help="empty string to leave it unset")
    ap.add_argument("--image", choices=("off", "page", "tagged"), default="off", help="send each page's image, plain or with line ids tagged at their boxes")
    ap.add_argument("--image-side", type=int, default=1024, help="long side in px of the image sent")
    ap.add_argument("--lookahead", type=int, default=0, help="also send the Japanese of this many following pages, skipping filler pages, as context only")
    ap.add_argument("--stage-dir", type=Path, help="per-page OCR output (p000.json with blocks) to take following pages and filler checks from, instead of the annotation")
    ap.add_argument("--context-skip-filler", action="store_true", help="leave filler pages out of the earlier pages sent as context")
    ap.add_argument("--context-stage", action="store_true", help="also translate --stage-dir pages missing from the annotation, unscored, so they enter context as on a real read")
    ap.add_argument("--series", action="append", default=[], help="only this book_title, repeatable")
    ap.add_argument("--limit", type=int, default=0, help="pages per series, for a smoke run")
    ap.add_argument("--pages", help="only these pages: 'openers' for the first 3 non-filler pages of each series, or page indexes like 4,9 or SERIES:4; skipped pages never enter context")
    ap.add_argument("--chapter-starts", type=lambda v: [int(x) for x in v.split(",")], default=[], help="page indexes where chapters start, so --pages openers takes 3 from each chapter of a one-book annotation")
    ap.add_argument("--jobs", type=int, default=1, help="series translated at once; pages within a series stay in reading order")
    ap.add_argument("--reuse", action="store_true", help="copy a finished run in --out-root with the same commit, translator, settings and dataset instead of translating")
    ap.add_argument("--score", action="store_true", help="only rescore an existing run")
    ap.add_argument("--comet-model", default=COMET_MODEL)
    ap.add_argument("--comet-python", type=Path, help=f"interpreter with unbabel-comet, default {COMET_PYTHON}")
    ap.add_argument("--comet-device", choices=("cpu", "auto"), default="cpu", help="auto uses the GPU when it has 4 GiB free, else the CPU")
    ap.add_argument("--comet-fresh", action="store_true", help="score every line with COMET, without taking scores of identical lines from other runs in --out-root")
    ap.add_argument("--comet-only", action="store_true", help=argparse.SUPPRESS)
    return ap


def main() -> None:
    args = parser().parse_args()
    out = args.out_root / args.label
    if args.comet_only:
        comet(out, args.comet_model, args.comet_device)
        return
    if not args.score:
        if args.annotation is None:
            sys.exit("annotation.json required unless --score")
        asyncio.run(run(args))
    score(out, args)


if __name__ == "__main__":
    main()
