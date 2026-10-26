from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
COMET_PYTHON = SERVER_DIR / ".venv-comet" / "bin" / "python"
COMET_MODEL = "Unbabel/wmt22-comet-da"
BOOTSTRAP = 1000
SAMPLES = 30
SUFFIXES = {"さん": "san", "ちゃん": "chan", "くん": "kun", "君": "kun", "さま": "sama", "様": "sama", "先輩": "senpai", "先生": "sensei", "殿": "dono"}
NOT_NAMES = ("父", "母", "兄", "姉", "娘", "息子", "客", "店長", "葬儀屋", "皆", "貴", "昨日", "許", "見", "婆", "爺", "ボク", "カエル")
KATAKANA = re.compile(r"[\u30a1-\u30fa\u30fc]")
KANJI = re.compile(r"[\u4e00-\u9fff\u3005]")
SUFFIX = re.compile("|".join(SUFFIXES))
WORDY = re.compile(r"\w")
ROMAJI_SUFFIX = re.compile(r"-\s?(" + "|".join(sorted(set(SUFFIXES.values()))) + r")\b", re.I)


def load_pages(annotation: Path, series: list[str]) -> tuple[list[dict], int]:
    pages, dropped = [], 0
    for book in json.loads(annotation.read_text()):
        if series and book["book_title"] not in series:
            continue
        for page in sorted(book["pages"], key=lambda p: p["page_index"]):
            kept = [t for t in page["text"] if t["text_ja"].strip()]
            lines = [{"ja": t["text_ja"].replace("\n", ""), "ref": t["text_en"]} for t in kept]
            boxes = [(t["x"], t["y"], t["x"] + t["w"], t["y"] + t["h"]) for t in kept]
            dropped += len(page["text"]) - len(lines)
            image = annotation.parent / page["image_paths"]["ja"]
            pages.append({"series": book["book_title"], "page_index": page["page_index"], "lines": lines, "boxes": boxes, "image": image})
    return pages, dropped


def page_path(out: Path, series: str, page_index: int) -> Path:
    return out / "pages" / f"{series}__{page_index:03d}.json"


def git_sha(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


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
    translator = make_translator(settings)
    problem = await translator.health()
    if problem:
        sys.exit(problem)
    pages, dropped = load_pages(args.annotation, args.series)
    if args.limit:
        pages = [p for p in pages if sum(q["series"] == p["series"] and q["page_index"] <= p["page_index"] for q in pages) <= args.limit]
    meta = {
        "label": args.label,
        "sha": git_sha(source_dir),
        "yomikae": str(source_dir),
        "translator": translator.name,
        "base_url": settings.openai_base_url,
        "reasoning_effort": settings.openai_reasoning_effort,
        "image": args.image,
        "image_side": args.image_side,
        "dataset": str(args.annotation),
        "dataset_sha": git_sha(args.annotation.parent),
        "blank_source_lines_dropped": dropped,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"yomikae from {source_dir} at {meta['sha']}, {translator.name} at {settings.openai_base_url}, {len(pages)} pages", flush=True)

    stores: dict[str, ContextStore] = {}
    todo = [p for p in pages if not done(page_path(out, p["series"], p["page_index"]))]
    started, finished = time.perf_counter(), 0
    for page in pages:
        series, index = page["series"], page["page_index"]
        store = stores.setdefault(series, ContextStore())
        path = page_path(out, series, index)
        if done(path):
            saved = json.loads(path.read_text())
            store.add(series, index, [ContextLine(line["ja"], line["hyp"]) for line in saved["lines"]])
            continue
        sources = [line["ja"] for line in page["lines"]]
        recalled = store.recall(series, sources)
        error = None
        page_img = None
        if args.image != "off" and sources:
            with Image.open(page["image"]) as im:
                page_img = page_image(im, [Box(*b) for b in page["boxes"]], args.image == "tagged", args.image_side)
        tokens_before = translator.prompt_tokens
        t = time.perf_counter()
        try:
            hyps = await translate_page(translator, store, sources, series, index, page_img) if sources else []
        except (TranslatorUnavailable, TranslatorFailed) as e:
            hyps, error = [""] * len(sources), str(e)
        ms = round((time.perf_counter() - t) * 1000)
        if error is None:
            store.add(series, index, [ContextLine(ja, hyp) for ja, hyp in zip(sources, hyps)])
        record = {
            "series": series,
            "page_index": index,
            "ms": ms,
            "prompt_tokens": translator.prompt_tokens - tokens_before,
            "error": error,
            "lines": [{"line_index": i, **line, "hyp": hyp, "recalled": known is not None} for i, (line, hyp, known) in enumerate(zip(page["lines"], hyps, recalled))],
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1))
        finished += 1
        per_page = (time.perf_counter() - started) / finished
        print(f"{series} p{index}: {len(sources)} lines in {ms} ms{' ERROR ' + error if error else ''}, {len(todo) - finished} pages left, about {per_page * (len(todo) - finished) / 60:.1f} min", flush=True)
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


def comet(out: Path, model_name: str) -> None:
    from comet import download_model, load_from_checkpoint

    _, lines = read_lines(out)
    scored = [line for line in lines if line["ref"].strip()]
    model = load_from_checkpoint(download_model(model_name))
    data = [{"src": line["ja"], "mt": clean(line["hyp"]), "ref": clean(line["ref"])} for line in scored]
    result = model.predict(data, batch_size=32, gpus=0, progress_bar=False)
    keys = [f"{line['series']}/{line['page_index']}/{line['line_index']}" for line in scored]
    (out / "comet.json").write_text(json.dumps({"model": model_name, "scores": dict(zip(keys, result.scores))}, indent=1))


def comet_scores(out: Path, args, keys: list[str]) -> dict[str, float]:
    path = out / "comet.json"
    if path.exists():
        cached = json.loads(path.read_text())
        if cached["model"] == args.comet_model and set(cached["scores"]) == set(keys):
            return cached["scores"]
    python = args.comet_python or (COMET_PYTHON if COMET_PYTHON.exists() else Path(sys.executable))
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "" if args.comet_device == "cpu" else os.environ.get("CUDA_VISIBLE_DEVICES", "")}
    print(f"scoring COMET with {args.comet_model} under {python} on {args.comet_device}", flush=True)
    t = time.perf_counter()
    subprocess.run([str(python), "-I", str(Path(__file__).resolve()), "--comet-model", args.comet_model, "--comet-only", "--out-root", str(out.parent), "--label", out.name], env=env, check=True)
    print(f"COMET took {time.perf_counter() - t:.0f} s", flush=True)
    return json.loads(path.read_text())["scores"]


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
    keys = [f"{line['series']}/{line['page_index']}/{line['line_index']}" for line in lines if line["ref"].strip()]
    scores = comet_scores(out, args, keys)
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
    summary = {"meta": meta, "comet_model": args.comet_model, "system": system, "by_series": by_series, "honorifics": {k: v for k, v in hon.items() if k != "cases"}, "timing": timing, "failed_pages": failed}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))

    s = system
    md = [f"# Translation benchmark {meta['label']}", ""]
    md.append(f"OpenMantra, translation only: gold Japanese per bubble through `yomikae.pipeline.translate_page`, one page per call in reading order, one context key per series. Commit `{meta['sha']}`, translator `{meta['translator']}` at `{meta['base_url']}`, reasoning_effort `{meta['reasoning_effort'] or 'unset'}`, {timing['pages']} pages, {s['lines']} lines ({meta['blank_source_lines_dropped']} lines with blank Japanese dropped).")
    md += ["", "## Scores", ""]
    md.append(f"COMET `{args.comet_model}` with a 95% bootstrap interval over lines ({BOOTSTRAP} resamples), chrF case-insensitive and cased. {s['scored']} lines have a reference; blank output counts as an empty hypothesis.")
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
        f"Per page, translation only: median {timing['median_ms']} ms, p90 {timing['p90_ms']} ms, {timing['total_s']} s over {timing['pages']} pages, {timing['ms_per_line']} ms per translated line, median {timing['median_prompt_tokens']} prompt tokens. Page image sent: {meta.get('image', 'off')}.",
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("annotation", type=Path, nargs="?", help="OpenMantra annotation.json")
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--base-url", default="http://localhost:1234/v1")
    ap.add_argument("--model", default="google/gemma-4-12b")
    ap.add_argument("--reasoning-effort", default="none", help="empty string to leave it unset")
    ap.add_argument("--image", choices=("off", "page", "tagged"), default="off", help="send each page's image, plain or with line ids tagged at their boxes")
    ap.add_argument("--image-side", type=int, default=1024, help="long side in px of the image sent")
    ap.add_argument("--series", action="append", default=[], help="only this book_title, repeatable")
    ap.add_argument("--limit", type=int, default=0, help="pages per series, for a smoke run")
    ap.add_argument("--score", action="store_true", help="only rescore an existing run")
    ap.add_argument("--comet-model", default=COMET_MODEL)
    ap.add_argument("--comet-python", type=Path, help=f"interpreter with unbabel-comet, default {COMET_PYTHON}")
    ap.add_argument("--comet-device", choices=("cpu", "auto"), default="cpu")
    ap.add_argument("--comet-only", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    out = args.out_root / args.label
    if args.comet_only:
        comet(out, args.comet_model)
        return
    if not args.score:
        if args.annotation is None:
            sys.exit("annotation.json required unless --score")
        asyncio.run(run(args))
    score(out, args)


if __name__ == "__main__":
    main()
