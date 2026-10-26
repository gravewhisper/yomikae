from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

IMAGE_EXTS = {".webp", ".jpg", ".jpeg", ".png"}
PANEL_W = 700
COLORS = [(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48), (145, 30, 180), (70, 240, 240), (240, 50, 230), (128, 128, 0), (0, 128, 128), (170, 110, 40)]
VERDICTS = ("correct", "leak", "partial", "miss", "merged")
TYPES = ("plain", "double", "burst", "see-through", "stepped", "tall-narrow", "narration", "no-bubble")


@dataclass(frozen=True)
class Model:
    name: str
    path: Path
    size: int
    conf: float = 0.25
    iou: float = 0.7


def parse_model(spec: str) -> Model:
    name, rest = spec.split("=", 1)
    path, size = rest.rsplit(":", 1)
    return Model(name, Path(path), int(size))


def letterbox(cv2, img: np.ndarray, size: int):
    h, w = img.shape[:2]
    r = min(size / h, size / w)
    nh, nw = round(h * r), round(w * r)
    top, left = (size - nh) // 2, (size - nw) // 2
    canvas = np.full((size, size, 3), 114, np.uint8)
    canvas[top : top + nh, left : left + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    return canvas, r, left, top


def segment(cv2, session, model: Model, rgb: np.ndarray) -> list[dict]:
    H, W = rgb.shape[:2]
    lb, r, left, top = letterbox(cv2, rgb, model.size)
    x = lb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    pred, proto = session.run(None, {session.get_inputs()[0].name: x})
    pred = pred[0].T
    nc = pred.shape[1] - 4 - proto.shape[1]
    scores = pred[:, 4 : 4 + nc].max(1)
    keep = scores > model.conf
    pred, scores = pred[keep], scores[keep]
    if not len(pred):
        return []
    boxes = np.stack([pred[:, 0] - pred[:, 2] / 2, pred[:, 1] - pred[:, 3] / 2, pred[:, 2], pred[:, 3]], 1)
    idx = np.array(cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), model.conf, model.iou)).reshape(-1)
    _, c, ph, pw = proto.shape
    coeffs = pred[idx, 4 + nc :]
    masks = 1 / (1 + np.exp(-(coeffs @ proto[0].reshape(c, -1)))).reshape(-1, ph, pw)
    out = []
    for k, i in enumerate(idx):
        m = cv2.resize(masks[k], (model.size, model.size), interpolation=cv2.INTER_LINEAR)
        x0, y0, bw, bh = boxes[i]
        crop = np.zeros_like(m)
        crop[int(max(y0, 0)) : int(y0 + bh), int(max(x0, 0)) : int(x0 + bw)] = 1
        m = ((m * crop) > 0.5)[top : top + round(H * r), left : left + round(W * r)].astype(np.uint8)
        m = cv2.resize(m, (W, H), interpolation=cv2.INTER_NEAREST) > 0
        if m.any():
            out.append({"mask": m, "score": float(scores[i])})
    return out


def page_paths(inputs: list[Path]) -> list[Path]:
    out = []
    for p in inputs:
        if p.is_dir():
            out += sorted((q for q in p.rglob("*") if q.suffix.lower() in IMAGE_EXTS and ".rendered" not in q.name and ".inpainted" not in q.name), key=lambda q: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(q))])
        else:
            out.append(p)
    return out


def page_key(path: Path) -> str:
    return f"{path.parent.name}__{path.stem}"


def full_mask(region, shape) -> np.ndarray:
    m = np.zeros(shape, bool)
    b = region.box
    m[b.y0 : b.y1, b.x0 : b.x1] = region.mask
    return m


def text_pixels(block, shape) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.nonzero(block.mask)
    ys, xs = ys + block.mask_box.y0, xs + block.mask_box.x0
    keep = (ys < shape[0]) & (xs < shape[1])
    return ys[keep], xs[keep]


def assign(instances: list[dict], ys: np.ndarray, xs: np.ndarray) -> int | None:
    if not len(ys) or not instances:
        return None
    shares = [inst["mask"][ys, xs].mean() for inst in instances]
    best = int(np.argmax(shares))
    return best if shares[best] >= 0.5 else None


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.count_nonzero(a | b)
    return np.count_nonzero(a & b) / union if union else 0.0


def overlay(cv2, Image, ImageDraw, rgb: np.ndarray, masks: list[np.ndarray | None], boxes: list, title: str) -> "Image.Image":
    base = rgb.astype(np.float32).copy()
    edges = np.zeros(rgb.shape[:2], bool)
    for i, m in enumerate(masks):
        if m is None:
            continue
        color = np.array(COLORS[i % len(COLORS)], np.float32)
        base[m] = base[m] * 0.55 + color * 0.45
        edges |= cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((5, 5), np.uint8)) > 0
    base[edges] = (255, 0, 0)
    im = Image.fromarray(base.astype(np.uint8))
    draw = ImageDraw.Draw(im)
    fs = max(18, rgb.shape[1] // 45)
    for i, b in enumerate(boxes):
        draw.rectangle(b.as_list(), outline=COLORS[i % len(COLORS)], width=3)
        tag = str(i) if masks[i] is not None else f"{i}x"
        draw.text((b.x0, max(0, b.y0 - fs)), tag, fill=(0, 0, 0), font_size=fs, stroke_width=3, stroke_fill=(255, 255, 0))
    draw.text((8, 8), title, fill=(255, 255, 255), font_size=fs, stroke_width=3, stroke_fill=(0, 0, 0))
    return im.resize((PANEL_W, round(im.height * PANEL_W / im.width)), Image.LANCZOS)


def sessions(models: list[Model], device: str):
    import torch  # noqa: F401
    import onnxruntime as ort

    ort.preload_dlls()
    providers = ["CPUExecutionProvider"]
    if device == "cuda":
        providers = [("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested", "gpu_mem_limit": 1536 * 2**20}), "CPUExecutionProvider"]
    return {m.name: ort.InferenceSession(str(m.path), providers=providers) for m in models}


def compare(args) -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    import cv2
    from PIL import Image, ImageDraw

    from yomikae import detect, inpaint, ocr, typeset
    from yomikae.config import DETECTOR_PATH
    from yomikae.pipeline import decode_image

    models = [parse_model(s) for s in args.model]
    sess = sessions(models, "cpu")
    detector = detect.load_session(DETECTOR_PATH)
    reader = ocr.load_ocr()
    lama = inpaint.load_model("cpu")
    args.out.mkdir(parents=True, exist_ok=True)
    records = []
    for path in page_paths(args.inputs):
        key = page_key(path)
        rgb = decode_image(path.read_bytes())
        h, w = rgb.shape[:2]
        bubbles, _ = ocr.read(reader, rgb, detect.detect(detector, rgb))
        if not bubbles:
            print(f"{key}: no blocks", flush=True)
            continue
        gray = cv2.cvtColor(inpaint.inpaint(lama, rgb, bubbles).image, cv2.COLOR_RGB2GRAY)
        blocks = [b.block.box for b in bubbles]
        grown = [typeset.find_region(gray, b.block) for b in bubbles]
        separated = typeset.separate(blocks, [(g, typeset.art_region(box, w, h, g.dark)) for g, box in zip(grown, blocks)])
        ours = []
        for (g, _), raw in zip(separated, grown):
            if g.on_art:
                ours.append(None)
                continue
            m = full_mask(g, (h, w))
            ours.append(cv2.dilate(m.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * raw.pad + 1, 2 * raw.pad + 1))) > 0)
        panels = [overlay(cv2, Image, ImageDraw, rgb, ours, blocks, "ours (find_region)")]
        per_model = {}
        for m in models:
            instances = segment(cv2, sess[m.name], m, rgb)
            picks = [assign(instances, *text_pixels(b.block, (h, w))) for b in bubbles]
            per_model[m.name] = {"instances": len(instances), "picks": picks, "scores": [instances[p]["score"] if p is not None else None for p in picks]}
            masks = [instances[p]["mask"] if p is not None else None for p in picks]
            per_model[m.name]["masks"] = masks
            panels.append(overlay(cv2, Image, ImageDraw, rgb, masks, blocks, f"{m.name} ({len(instances)} found)"))
        sheet = Image.new("RGB", (PANEL_W * len(panels) + 8 * (len(panels) - 1), max(p.height for p in panels)), "gray")
        for i, p in enumerate(panels):
            sheet.paste(p, (i * (PANEL_W + 8), 0))
        sheet.save(args.out / f"{key}.overlay.jpg", quality=85)
        for i, b in enumerate(bubbles):
            rec = {"page": key, "block": i, "box": blocks[i].as_list(), "source": b.source, "ours_on_art": ours[i] is None}
            for name, pm in per_model.items():
                mm = pm["masks"][i]
                shared = sum(1 for p in pm["picks"] if p is not None and p == pm["picks"][i])
                rec[name] = {"found": mm is not None, "score": pm["scores"][i], "shared_with": shared, "iou_vs_ours": round(iou(mm, ours[i]), 3) if mm is not None and ours[i] is not None else None}
            records.append(rec)
        print(f"{key}: {len(bubbles)} blocks, ours on art {sum(o is None for o in ours)}, " + ", ".join(f"{n} {pm['instances']} inst / {sum(p is not None for p in pm['picks'])} hit" for n, pm in per_model.items()), flush=True)
    (args.out / "blocks.json").write_text(json.dumps(records, ensure_ascii=False, indent=1))
    judge = args.out / "judgments.json"
    if not judge.exists():
        template = {f"{r['page']}#{r['block']}": {"type": None, "ours": None, **{m.name: None for m in models}} for r in records}
        judge.write_text(json.dumps(template, indent=1))


def gpu_used_mb(pid: int) -> int:
    out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
    return sum(int(m) for p, m in (line.split(", ") for line in out.strip().splitlines() if line) if int(p) == pid)


def timing(args) -> None:
    import cv2

    models = [parse_model(s) for s in args.model]
    pages = page_paths(args.inputs)[: args.pages]
    images = [cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB) for p in pages]
    result = {}
    for m in models:
        before = gpu_used_mb(os.getpid())
        session = sessions([m], args.device)[m.name]
        segment(cv2, session, m, images[0])
        times, peak = [], 0
        for _ in range(args.repeat):
            for img in images:
                t = time.perf_counter()
                segment(cv2, session, m, img)
                times.append((time.perf_counter() - t) * 1000)
                peak = max(peak, gpu_used_mb(os.getpid()))
        result[m.name] = {"device": args.device, "runs": len(times), "median_ms": round(statistics.median(times), 1), "p90_ms": round(float(np.percentile(times, 90)), 1), "min_ms": round(min(times), 1), "max_ms": round(max(times), 1), "gpu_mb_before": before, "gpu_mb_peak": peak}
        del session
        print(m.name, json.dumps(result[m.name]), flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"timing-{args.device}.json").write_text(json.dumps(result, indent=1))


def tally(args) -> None:
    judged = json.loads((args.out / "judgments.json").read_text())
    rows = [v for v in judged.values() if v.get("type")]
    models = [k for k in rows[0] if k not in ("type", "ours", "note")] if rows else []

    def hybrid(v: dict, m: str) -> str:
        return v["ours"] if v[m] == "merged" else v[m]

    contenders = ["ours", *models, *(f"{m}+ours" for m in models)]
    verdict = lambda v, c: hybrid(v, c[: -len("+ours")]) if c.endswith("+ours") else v[c]  # noqa: E731
    table: dict[str, dict] = {}
    for v in rows:
        t = table.setdefault(v["type"], {"n": 0, **{c: {x: 0 for x in VERDICTS} for c in contenders}, "vs_ours": {m: [0, 0] for m in models}})
        t["n"] += 1
        for c in contenders:
            t[c][verdict(v, c)] += 1
        for m in models:
            ours_ok, model_ok = v["ours"] == "correct", v[m] == "correct"
            t["vs_ours"][m][0] += ours_ok and not model_ok
            t["vs_ours"][m][1] += model_ok and not ours_ok
    names = [n for n in TYPES if n in table]
    total = {"n": sum(table[n]["n"] for n in names), **{c: {x: sum(table[n][c][x] for n in names) for x in VERDICTS} for c in contenders}, "vs_ours": {m: [sum(table[n]["vs_ours"][m][i] for n in names) for i in (0, 1)] for m in models}}
    (args.out / "tally.json").write_text(json.dumps({"by_type": table, "total": total}, indent=1))
    lines = [
        "| Type | Bubbles | " + " | ".join(f"{c} correct" for c in contenders) + " | " + " | ".join(f"ours / {m} only" for m in models) + " |",
        "| --- | ---: | " + " | ".join("---:" for _ in contenders) + " | " + " | ".join("---:" for _ in models) + " |",
    ]
    for n, t in [*((n, table[n]) for n in names), ("all", total)]:
        lines.append(f"| {n} | {t['n']} | " + " | ".join(str(t[c]["correct"]) for c in contenders) + " | " + " | ".join(f"{t['vs_ours'][m][0]} / {t['vs_ours'][m][1]}" for m in models) + " |")
    lines += ["", "`<model>+ours` takes the model's mask, and ours where the model merged this block's bubble with another's.", "", "| Contender | " + " | ".join(VERDICTS) + " |", "| --- | " + " | ".join("---:" for _ in VERDICTS) + " |"]
    lines += [f"| {c} | " + " | ".join(str(total[c][x]) for x in VERDICTS) + " |" for c in contenders]
    (args.out / "tally.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("compare", "time", "tally"))
    ap.add_argument("inputs", type=Path, nargs="*")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", action="append", default=[], help="name=path.onnx:imgsz")
    ap.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()
    {"compare": compare, "time": timing, "tally": tally}[args.command](args)


if __name__ == "__main__":
    main()
