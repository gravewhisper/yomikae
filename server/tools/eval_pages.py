from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import tempfile
from dataclasses import replace
from pathlib import Path

from PIL import Image

from yomikae.config import Settings
from yomikae.pipeline import Engine

PANEL_W = 600
IMAGE_EXTS = {".webp", ".jpg", ".jpeg", ".png"}


def natural_key(path: Path):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", path.stem)]


class Replay:

    def __init__(self, inner, summary: dict):
        self.inner = inner
        self.name = f"replay+{inner.name}"
        self.known = {b["source"]: b["translation"] for p in summary["pages"] for b in p["bubbles"]}

    async def translate(self, lines, context):
        missing = [line for line in lines if line not in self.known]
        fresh = dict(zip(missing, await self.inner.translate(missing, context))) if missing else {}
        return [self.known[line] if line in self.known else fresh[line] for line in lines]

    async def health(self):
        return await self.inner.health()


def contact_sheet(panels: list[Image.Image]) -> Image.Image:
    scaled = [p.convert("RGB").resize((PANEL_W, round(p.height * PANEL_W / p.width)), Image.LANCZOS) for p in panels]
    sheet = Image.new("RGB", (PANEL_W * len(scaled) + 8 * (len(scaled) - 1), max(p.height for p in scaled)), "gray")
    for i, p in enumerate(scaled):
        sheet.paste(p, (i * (PANEL_W + 8), 0))
    return sheet


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pages_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--baseline", type=Path, help="earlier OUT_DIR whose renders go in the middle column")
    ap.add_argument("--replay", type=Path, help="earlier summary.json whose translations to reuse")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    settings = replace(Settings.from_env(), cache_dir=Path(tempfile.mkdtemp(prefix="yomikae-eval-")))
    engine = Engine(settings)
    engine.warmup()
    if args.replay:
        engine.translator = Replay(engine.translator, json.loads(args.replay.read_text()))

    pages = sorted((p for p in args.pages_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS), key=natural_key)
    summary = {"translator": engine.translator.name, "pages": []}
    for i, path in enumerate(pages):
        result = await engine.process(path.read_bytes(), "eval", i)
        rendered_path = args.out_dir / f"{path.stem}.rendered.webp"
        rendered_path.write_bytes(engine.cache.rendered_path(result["page_id"]).read_bytes())
        panels = [Image.open(path)]
        baseline = args.baseline / rendered_path.name if args.baseline else None
        if baseline and baseline.exists():
            panels.append(Image.open(baseline))
        panels.append(Image.open(rendered_path))
        contact_sheet(panels).save(args.out_dir / f"{path.stem}.sheet.jpg", quality=88)

        bubbles = [{**b, "font_size": s} for b, s in zip(result["bubbles"], result["font_sizes"])]
        sizes = [b["font_size"] for b in bubbles if b["font_size"]]
        page = {
            "page": path.name,
            "bubbles": bubbles,
            "skipped": result.get("skipped", []),
            "font_sizes": sizes,
            "font_size_spread": max(sizes) - min(sizes) if sizes else 0,
            "timings_ms": result["timings_ms"],
        }
        summary["pages"].append(page)
        print(f"{path.name}: {len(bubbles)} bubbles, {len(page['skipped'])} skipped, sizes {sizes}, {json.dumps(result['timings_ms'])}")

    for stage in ("detect", "balloons", "ocr", "translate", "inpaint", "typeset", "total"):
        values = [p["timings_ms"].get(stage, 0) for p in summary["pages"]]
        summary.setdefault("timings_median_ms", {})[stage] = statistics.median(values) if values else 0
    (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print("median timings", json.dumps(summary["timings_median_ms"]))


if __name__ == "__main__":
    asyncio.run(main())
