from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_test_page import make_page  # noqa: E402

STRIPS = {
    "page": (1, 1280, 1816, "v"),
    "strip_6000": (5, 800, 1200, "v"),
    "strip_10000": (7, 720, 1430, "v"),
    "strip_20000": (14, 720, 1430, "v"),
    "spread": (3, 1280, 1816, "h"),
}


def build(pages: int, width: int, height: int, axis: str, seed: int) -> tuple[Image.Image, list[dict]]:
    size = (width, height * pages) if axis == "v" else (width * pages, height)
    strip = Image.new("RGB", size, "white")
    truth = []
    for i in range(pages):
        img, bubbles = make_page(seed + i, width, height)
        dx, dy = (0, i * height) if axis == "v" else (i * width, 0)
        strip.paste(img, (dx, dy))
        for b in bubbles:
            x0, y0, x1, y1 = b.text_box
            truth.append({"text_box": [x0 + dx, y0 + dy, x1 + dx, y1 + dy], "text": b.text})
    return strip, truth


def iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score(truth: list[dict], bubbles: list[dict]) -> dict:
    found = read = 0
    for t in truth:
        hits = [b for b in bubbles if iou(b["box"], t["text_box"]) > 0.5]
        found += bool(hits)
        read += any(edit_distance(b["source"], t["text"]) <= 1 for b in hits)
    matched = sum(any(iou(b["box"], t["text_box"]) > 0.5 for t in truth) for b in bubbles)
    return {"truth": len(truth), "found": found, "read": read, "returned": len(bubbles), "unmatched": len(bubbles) - matched}


class GpuPeak:
    def __init__(self):
        self.peak = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.poll, daemon=True)

    def poll(self):
        pid = str(os.getpid())
        while not self.stop.is_set():
            out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
            for line in out.splitlines():
                p, mem = (s.strip() for s in line.split(","))
                if p == pid:
                    self.peak = max(self.peak, int(mem))
            time.sleep(0.05)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strips", nargs="*", default=list(STRIPS))
    ap.add_argument("--seed", type=int, default=500)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    import torch

    from yomikae import pipeline
    from yomikae.config import Settings

    settings = replace(Settings.from_env(), translator="fake", cache_dir=Path(tempfile.mkdtemp()))
    engine = pipeline.Engine(settings)
    engine.warmup()
    results = {}
    for name in args.strips:
        image, truth = build(*STRIPS[name], args.seed)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        with GpuPeak() as gpu:
            try:
                page = asyncio.run(engine.process(buf.getvalue(), None, None))
                error = None
            except Exception as e:
                page, error = None, repr(e)
        row = {"size": list(image.size), "gpu_process_peak_mib": gpu.peak, "torch_peak_mib": round(torch.cuda.max_memory_allocated() / 2**20) if torch.cuda.is_available() else None}
        if page is None:
            row["error"] = error
        else:
            row |= score(truth, page["bubbles"]) | {"timings_ms": page["timings_ms"]}
            rendered = Image.open(engine.cache.rendered_path(page["page_id"]))
            row["rendered"] = [rendered.format, *rendered.size, engine.cache.rendered_path(page["page_id"]).stat().st_size]
        results[name] = row
        print(name, json.dumps(row), flush=True)
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
