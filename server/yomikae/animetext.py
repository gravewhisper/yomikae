from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .config import ANIMETEXT_PATH
from .detect import MASK_PAD_FRAC, ModelMissing, load_session, run_session
from .model import Box, TextBlock
from .tiles import owns, tiles

SIZE = 640
THRESHOLD = 0.299
PADDING = 114
TILE_NMS = 0.45
PAGE_NMS = 0.5
CONTAINED = 0.85
MIN_SEG_COVER = 0.5
FRAME_SPAN = 0.9
FRAME_FILL = 0.15


class AnimeText:
    def __init__(self, device: str, path: Path = ANIMETEXT_PATH):
        if not path.exists():
            raise ModelMissing(f"Korean pages need {path.name} in server/models; download AnimeText as the README says")
        self.session = load_session(path, device)

    def tile_boxes(self, crop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h, w = crop.shape[:2]
        r = SIZE / max(h, w)
        canvas = np.full((SIZE, SIZE, 3), PADDING, np.uint8)
        canvas[: round(h * r), : round(w * r)] = cv2.resize(crop, (round(w * r), round(h * r)), interpolation=cv2.INTER_LINEAR)
        out = run_session(self.session, {"images": canvas.transpose(2, 0, 1)[None].astype(np.float32) / 255.0})[0][0].T
        out = out[out[:, 4] >= THRESHOLD]
        boxes = np.stack([out[:, 0] - out[:, 2] / 2, out[:, 1] - out[:, 3] / 2, out[:, 0] + out[:, 2] / 2, out[:, 1] + out[:, 3] / 2], 1) / r
        keep = nms(boxes, out[:, 4], TILE_NMS)
        return boxes[keep].reshape(-1, 4), out[keep, 4]

    def boxes(self, image: np.ndarray) -> list[Box]:
        h, w = image.shape[:2]
        found, scores = [np.zeros((0, 4), np.float32)], [np.zeros(0, np.float32)]
        for tile in tiles(w, h):
            win = tile.window
            boxes, score = self.tile_boxes(image[win.y0 : win.y1, win.x0 : win.x1])
            boxes = boxes + np.array([win.x0, win.y0, win.x0, win.y0], np.float32)
            mine = owns(tile, (boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2, w, h)
            found.append(boxes[mine])
            scores.append(score[mine])
        boxes = np.concatenate(found).clip(0, [w, h, w, h])
        return [Box(*(int(round(v)) for v in b)) for b in dedupe(boxes, np.concatenate(scores))]


def nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    if not len(boxes):
        return []
    xywh = np.concatenate([boxes[:, :2], boxes[:, 2:] - boxes[:, :2]], 1)
    return [int(i) for i in np.array(cv2.dnn.NMSBoxes(xywh.tolist(), scores.tolist(), 0.0, threshold)).reshape(-1)]


def dedupe(boxes: np.ndarray, scores: np.ndarray) -> np.ndarray:
    boxes = boxes[nms(boxes, scores, PAGE_NMS)]
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    dropped: set[int] = set()
    for i in range(len(boxes)):
        for j in range(len(boxes)):
            if i == j or j in dropped or area[i] > area[j]:
                continue
            ix = max(0, min(boxes[i, 2], boxes[j, 2]) - max(boxes[i, 0], boxes[j, 0]))
            iy = max(0, min(boxes[i, 3], boxes[j, 3]) - max(boxes[i, 1], boxes[j, 1]))
            if ix * iy >= CONTAINED * area[i]:
                dropped.add(i)
                break
    return boxes[[i for i in range(len(boxes)) if i not in dropped]]


def ink(gray: np.ndarray) -> np.ndarray:
    _, dark = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    text = dark if (dark > 0).mean() <= 0.5 else 255 - dark
    h, w = text.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(text, connectivity=8)
    frames = [i for i in range(1, n) if stats[i, 2] >= FRAME_SPAN * w and stats[i, 3] >= FRAME_SPAN * h and stats[i, 4] < FRAME_FILL * stats[i, 2] * stats[i, 3]]
    text[np.isin(labels, frames)] = 0
    return text


def blocks(boxes: list[Box], seg: np.ndarray, image: np.ndarray, vertical_text: bool) -> list[TextBlock]:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    out = []
    for box in boxes:
        if box.w <= 0 or box.h <= 0:
            continue
        mask_box = box.expand(int(min(box.w, box.h) * MASK_PAD_FRAC) + 4, w, h)
        mask = seg[mask_box.y0 : mask_box.y1, mask_box.x0 : mask_box.x1].copy()
        inner = (slice(box.y0 - mask_box.y0, box.y1 - mask_box.y0), slice(box.x0 - mask_box.x0, box.x1 - mask_box.x0))
        text = ink(gray[box.y0 : box.y1, box.x0 : box.x1]) > 0
        if text.any() and (mask[inner][text] > 0).mean() < MIN_SEG_COVER:
            mask[inner] |= np.where(text, 255, 0).astype(np.uint8)
        out.append(TextBlock(box=box, vertical=vertical_text and box.h > box.w, mask_box=mask_box, mask=mask))
    return out
