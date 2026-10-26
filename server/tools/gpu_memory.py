from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from make_test_page import FONT_PATH, JP_FONT_INDEX, LINES, draw_screentone, draw_vertical_text, make_page, vertical_text_size  # noqa: E402

MIB = 2**20


def heavy_page(seed: int, width: int = 1280, height: int = 1816) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height), (255, 255, 255))
    for r in range(3):
        for c in range(2):
            draw_screentone(img, (40 + c * 610, 40 + r * 590, 630 + c * 610, 610 + r * 590), rng)
    font = ImageFont.truetype(FONT_PATH, 34, index=JP_FONT_INDEX)
    for i in range(12):
        text = LINES[i % len(LINES)]
        _, _, cols = vertical_text_size(text, font, 5)
        draw_vertical_text(img, (90 + (i % 4) * 300, 90 + (i // 4) * 580), cols, font)
    return img


def pages() -> dict[str, Image.Image]:
    out = {f"synthetic{seed}": make_page(seed)[0] for seed in (0, 1, 2)}
    out["heavy"] = heavy_page(0)
    return out


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class Gpu:
    def __init__(self, floor_mib: int):
        self.floor = floor_mib * MIB
        self.peak = 0
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def process_mib(self) -> int:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            pid, used = (s.strip() for s in line.split(","))
            if int(pid) == os.getpid():
                return int(used)
        return 0

    def guard(self) -> None:
        import torch

        if torch.cuda.is_initialized():
            free, _ = torch.cuda.mem_get_info()
            if free < self.floor:
                print(json.dumps({"abort": f"device free {free // MIB} MiB under floor {self.floor // MIB} MiB"}), flush=True)
                os._exit(3)

    def start(self) -> None:
        def loop():
            while not self.stop.wait(0.05):
                self.guard()
                self.peak = max(self.peak, self.process_mib())

        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()

    def snap(self, label: str, **extra) -> dict:
        import torch

        row = {"label": label, **extra, "process_mib": self.process_mib(), "peak_process_mib": self.peak}
        if torch.cuda.is_initialized():
            torch.cuda.synchronize()
            free, _ = torch.cuda.mem_get_info()
            row |= {
                "allocated_mib": round(torch.cuda.memory_allocated() / MIB),
                "reserved_mib": round(torch.cuda.memory_reserved() / MIB),
                "max_allocated_mib": round(torch.cuda.max_memory_allocated() / MIB),
                "device_free_mib": free // MIB,
            }
        print(json.dumps(row), flush=True)
        return row


def cap_torch(cap_mib: int) -> None:
    import torch

    torch.zeros(1, device="cuda")
    total = torch.cuda.get_device_properties(0).total_memory
    torch.cuda.set_per_process_memory_fraction(cap_mib * MIB / total)


def onnx_session(path: Path, device: str, cap_mib: int):
    import onnxruntime as ort

    ort.preload_dlls()
    if device == "cpu":
        return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    cuda = ("CUDAExecutionProvider", {"gpu_mem_limit": cap_mib * MIB})
    return ort.InferenceSession(str(path), providers=[cuda, "CPUExecutionProvider"])


def timed(fn, *args):
    t = time.perf_counter()
    out = fn(*args)
    return out, round((time.perf_counter() - t) * 1000, 1)


def probe_onnx(gpu: Gpu, args) -> None:
    from yomikae import balloon, detect
    from yomikae.config import BUBBLE_MODEL_PATH, DETECTOR_PATH

    import torch

    torch.zeros(1, device="cuda")
    gpu.snap("context")
    det = onnx_session(DETECTOR_PATH, "cuda", args.cap)
    gpu.snap("detector loaded")
    imgs = {k: np.asarray(v) for k, v in pages().items()}
    for name, image in imgs.items():
        _, ms = timed(detect.detect, det, image)
        gpu.snap(f"detect {name}", ms=ms)
    seg = onnx_session(BUBBLE_MODEL_PATH, "cuda", args.cap)
    gpu.snap("segmenter loaded")
    for name, image in imgs.items():
        _, ms = timed(balloon.segment, seg, image)
        gpu.snap(f"segment {name}", ms=ms)
    for _ in range(args.repeat):
        for name, image in imgs.items():
            _, d = timed(detect.detect, det, image)
            _, s = timed(balloon.segment, seg, image)
            gpu.snap(f"repeat {name}", detect_ms=d, segment_ms=s)


def probe_ocr(gpu: Gpu, args) -> None:
    from yomikae import balloon, detect, ocr
    from yomikae.config import BUBBLE_MODEL_PATH, DETECTOR_PATH

    cap_torch(args.cap)
    gpu.snap("context")
    det = onnx_session(DETECTOR_PATH, "cpu", 0)
    seg = onnx_session(BUBBLE_MODEL_PATH, "cpu", 0)
    reader = ocr.load_ocr("cuda")
    gpu.snap("ocr loaded", device=str(reader.model.device))
    for _ in range(args.repeat):
        for name, img in pages().items():
            image = np.asarray(img)
            blocks, balloons = detect.detect(det, image), balloon.segment(seg, image)
            (bubbles, _), ms = timed(ocr.read, reader, image, blocks, balloons)
            gpu.snap(f"ocr {name}", ms=ms, bubbles=len(bubbles))


def probe_lama(gpu: Gpu, args) -> None:
    import torch

    from yomikae import inpaint

    cap_torch(args.cap)
    gpu.snap("context")
    model = inpaint.load_model("cuda")
    gpu.snap("lama loaded")
    rng = np.random.default_rng(0)

    def crop(h: int, w: int):
        img = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
        mask = np.zeros((h, w), np.uint8)
        mask[h // 4 : 3 * h // 4, w // 3 : 2 * w // 3] = 255
        return img, mask

    for side in args.sides:
        torch.cuda.reset_peak_memory_stats()
        _, ms = timed(inpaint.run_lama, model, *crop(side, side))
        gpu.snap(f"lama {side}x{side}", ms=ms)
    shapes = [(int(rng.integers(200, 1024)), int(rng.integers(200, 1024))) for _ in range(args.growth)]
    for flush in (False, True):
        torch.cuda.empty_cache()
        gpu.snap(f"growth start flush={flush}")
        for i, (h, w) in enumerate(shapes):
            inpaint.run_lama(model, *crop(min(h, args.sides[-1]), min(w, args.sides[-1])))
            if (i + 1) % 5 == 0:
                if flush:
                    torch.cuda.empty_cache()
                gpu.snap(f"growth flush={flush} after {i + 1}")


def probe_engine(gpu: Gpu, args) -> None:
    import torch

    from yomikae import pipeline
    from yomikae.config import Settings

    settings = replace(Settings.from_env(), translator="fake", cache_dir=Path(tempfile.mkdtemp()))
    if torch.cuda.is_available() and not settings.low_vram:
        cap_torch(args.cap)
        gpu.snap("context")
    t = time.perf_counter()
    engine = pipeline.Engine(settings)
    gpu.snap("engine loaded", ms=round((time.perf_counter() - t) * 1000))
    engine.warmup()
    gpu.snap("after warmup")
    fills = {}
    original = pipeline.inpaint.inpaint

    def counting(model, image, bubbles):
        out = original(model, image, bubbles)
        fills[key] = out.fills
        return out

    pipeline.inpaint.inpaint = counting
    data = {name: png(img) for name, img in pages().items()}
    results = {}
    for r in range(args.repeat):
        for key, blob in data.items():
            res = asyncio.run(engine._run(hashlib.sha256(blob).hexdigest(), blob, None, None))
            results[key] = res
            gpu.snap(f"page {key} run {r}", timings=res["timings_ms"], bubbles=len(res["bubbles"]), textured=fills[key].count("textured"), flat=fills[key].count("flat"))
    time.sleep(1)
    gpu.snap("idle after pages")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for key, res in results.items():
            (args.out / f"{key}.json").write_text(json.dumps({k: res[k] for k in ("bubbles", "skipped", "font_sizes")}, ensure_ascii=False, indent=1))
            name = res["rendered_url"].rsplit("/", 1)[1]
            (args.out / f"{key}.{name.split('.')[-1]}").write_bytes(engine.cache.root.joinpath(res["page_id"], name).read_bytes())


PROBES = {"onnx": probe_onnx, "ocr": probe_ocr, "lama": probe_lama, "engine": probe_engine}


def main() -> None:
    ap = argparse.ArgumentParser(description="Measure GPU memory per pipeline component, with a hard torch cap and a device free-memory floor.")
    ap.add_argument("probe", choices=PROBES)
    ap.add_argument("--cap", type=int, default=900, help="MiB cap for the torch allocator and each ONNX Runtime CUDA arena")
    ap.add_argument("--floor", type=int, default=900, help="exit at once when device free memory drops under this many MiB")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--sides", type=int, nargs="+", default=[256, 512, 768, 1024])
    ap.add_argument("--growth", type=int, default=30)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    gpu = Gpu(args.floor)
    gpu.start()
    PROBES[args.probe](gpu, args)


if __name__ == "__main__":
    main()
