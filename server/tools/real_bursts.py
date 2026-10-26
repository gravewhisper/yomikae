from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

LABELS = Path(__file__).resolve().parent / "openmantra_bursts.json"
CLASSES = ("burst", "flash", "cloud", "plain", "art", "unsure", "rest")


def overlap(a: list[int], b: list[int]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])  # noqa: E731
    return w * h / (area(a) + area(b) - w * h)


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from yomikae import balloon, detect, typeset
    from yomikae.balloon import claims
    from yomikae.config import BUBBLE_MODEL_PATH, DETECTOR_PATH
    from yomikae.model import Bubble

    root = Path(sys.argv[1])
    data = json.loads((Path(sys.argv[2]) if len(sys.argv) > 2 else LABELS).read_text())
    labels: dict[str, list[tuple[list[int], str]]] = {}
    for item in data["labels"]:
        labels.setdefault(item["page"], []).append((item["block"], item["label"]))
    detector, segmenter = detect.load_session(DETECTOR_PATH), detect.load_session(BUBBLE_MODEL_PATH)
    called: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for path in sorted(root.glob("images/*/ja/*.jpg")):
        image = np.asarray(Image.open(path).convert("RGB"))
        blocks, balloons = detect.detect(detector, image), balloon.segment(segmenter, image)
        picks = claims(blocks, balloons)
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        for b in blocks:
            text = cv2.dilate((b.mask > 0).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            gray[b.mask_box.y0 : b.mask_box.y1, b.mask_box.x0 : b.mask_box.x1][text] = 255
        known = labels.get(str(path.relative_to(root)), [])
        for b, pick in zip(blocks, picks):
            home = balloons[pick] if pick is not None else None
            burst = typeset.bubble_region(gray, Bubble(b, "", "x", home, pick is not None and picks.count(pick) > 1)).burst
            label = max(known, key=lambda k: overlap(k[0], b.box.as_list()), default=([0, 0, 0, 0], "rest"))
            kind = label[1] if overlap(label[0], b.box.as_list()) >= 0.5 else "rest"
            total[kind] += 1
            called[kind] += burst
    print("class   blocks  called burst")
    for kind in CLASSES:
        print(f"{kind:<8}{total[kind]:>6}{called[kind]:>14}")
    hits = called["burst"]
    wrong = sum(called[k] for k in CLASSES if k not in ("burst", "unsure"))
    print(f"precision {hits / max(1, hits + wrong):.2f}, recall {hits / max(1, total['burst']):.2f} (unsure blocks are left out)")


if __name__ == "__main__":
    main()
