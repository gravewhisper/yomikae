from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from . import lama
from .config import LAMA_PATH
from .detect import column_width
from .model import Box, Bubble

LAMA_MAX_SIDE = 1024
FLAT_STD = 8.0
INK_DEV = 32
MAX_CROSSINGS = 4
MAX_OUTLINE = 0.15
MASK_SCALE = 2 / 3


Fill = Literal["flat", "textured"]


@dataclass(frozen=True)
class Inpainted:
    image: np.ndarray
    fills: list[Fill]


def load_model(device: str):
    return lama.load(LAMA_PATH, device)


def glyph_size(seg: np.ndarray, vertical: bool, cells: int) -> int:
    ys, xs = np.nonzero(seg)
    bw, bh = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
    return max(3, int(min(column_width(seg, vertical), bw, bh, (bw * bh / max(1, cells)) ** 0.5)))


def block_mask(image: np.ndarray, bubble: Bubble, core: np.ndarray, out: np.ndarray) -> None:
    from pydensecrf import densecrf
    from pydensecrf.utils import unary_from_softmax

    block = bubble.block
    seg = block.mask > 0
    ys, xs = np.nonzero(seg)
    if not len(ys):
        return
    h, w = image.shape[:2]
    mb = block.mask_box
    g = glyph_size(seg, block.vertical, sum(not ch.isspace() for ch in bubble.source))
    win = Box(int(xs.min()) + mb.x0, int(ys.min()) + mb.y0, int(xs.max()) + 1 + mb.x0, int(ys.max()) + 1 + mb.y0).expand(int(0.1 * g), w, h)
    raw = np.zeros((win.h, win.w), np.uint8)
    raw[ys + mb.y0 - win.y0, xs + mb.x0 - win.x0] = 255

    size = (max(1, round(win.w * MASK_SCALE)), max(1, round(win.h * MASK_SCALE)))
    small = np.where(cv2.resize(raw, size, interpolation=cv2.INTER_LINEAR) > 0, 255, 0).astype(np.uint8)
    crop = cv2.resize(image[win.y0 : win.y1, win.x0 : win.x1], size, interpolation=cv2.INTER_LINEAR)
    crop = cv2.bilateralFilter(crop, 17, 80, 80)

    sh, sw = small.shape
    ph, pw = (-sh) % 8, (-sw) % 8
    rgb = np.ascontiguousarray(np.pad(crop, ((0, ph), (0, pw), (0, 0)), mode="edge"))
    m = np.pad(small, ((0, ph), (0, pw))).astype(np.float32) / 255.0
    crf = densecrf.DenseCRF2D(sw + pw, sh + ph, 2)
    crf.setUnaryEnergy(unary_from_softmax(np.stack([1 - m, m]).astype(np.float32)))
    crf.addPairwiseGaussian(sxy=1, compat=3, kernel=densecrf.DIAG_KERNEL, normalization=densecrf.NO_NORMALIZATION)
    crf.addPairwiseBilateral(sxy=23, srgb=7, rgbim=rgb, compat=20, kernel=densecrf.DIAG_KERNEL, normalization=densecrf.NO_NORMALIZATION)
    labels = np.argmax(np.array(crf.inference(5)), 0).reshape(sh + ph, sw + pw)[:sh, :sw]
    refined = np.where(labels == 1, 255, 0).astype(np.uint8)

    near = 2 * max(2, round(g * MASK_SCALE * 0.15)) + 1
    refined &= cv2.dilate(small, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (near, near)))
    back = cv2.resize(refined, (win.w, win.h), interpolation=cv2.INTER_LINEAR)
    tight = np.where((back > 0) | (raw > 0), 255, 0).astype(np.uint8)
    core[win.y0 : win.y1, win.x0 : win.x1] |= tight

    k = max(3, (int((g * MASK_SCALE + 20) * 0.3) // 2) * 2 + 1)
    r = round((k // 2) / MASK_SCALE)
    grown = cv2.dilate(np.pad(tight, r), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    reach = win.expand(r, w, h)
    oy, ox = reach.y0 - win.y0 + r, reach.x0 - win.x0 + r
    out[reach.y0 : reach.y1, reach.x0 : reach.x1] |= grown[oy : oy + reach.h, ox : ox + reach.w]


def text_mask(image: np.ndarray, bubbles: list[Bubble]) -> tuple[np.ndarray, np.ndarray]:
    core = np.zeros(image.shape[:2], np.uint8)
    out = np.zeros(image.shape[:2], np.uint8)
    for b in bubbles:
        block_mask(image, b, core, out)
    return core, cv2.dilate(out, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))


def flat_fill(crop: np.ndarray, sel: np.ndarray, core: np.ndarray, ring: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    ring_px = crop[ring]
    if not len(ring_px):
        return None
    median = np.median(ring_px, 0)
    ink = (np.abs(crop.astype(np.int16) - median.astype(np.int16)).max(2) > INK_DEV) & (ring | (sel & ~core))
    _, ink_labels = cv2.connectedComponents(ink.astype(np.uint8), connectivity=8)
    crossings = np.unique(ink_labels[ring & ink])
    if len(crossings) > MAX_CROSSINGS:
        return None
    outline = np.isin(ink_labels, crossings) & ink
    if outline[ring].mean() > MAX_OUTLINE or crop[ring & ~outline].std(0).max() >= FLAT_STD:
        return None
    return median.astype(np.uint8), sel & ~outline


def run_lama(model, crop: np.ndarray, mask: np.ndarray) -> np.ndarray:
    import torch

    h, w = crop.shape[:2]
    scale = min(1.0, LAMA_MAX_SIDE / max(h, w))
    if scale < 1.0:
        crop_s = cv2.resize(crop, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        mask_s = cv2.resize(mask, (crop_s.shape[1], crop_s.shape[0]), interpolation=cv2.INTER_LINEAR)
    else:
        crop_s, mask_s = crop, mask
    sh, sw = crop_s.shape[:2]
    ph, pw = (-sh) % 8, (-sw) % 8
    img_t = np.pad(crop_s, ((0, ph), (0, pw), (0, 0)), mode="reflect")
    mask_t = np.pad(mask_s, ((0, ph), (0, pw)), mode="constant")
    device = next(model.parameters()).device
    x = torch.from_numpy(img_t).to(device).permute(2, 0, 1)[None].float() / 255.0
    m = (torch.from_numpy(mask_t).to(device) > 0).float()[None, None]
    with torch.inference_mode(), torch.autocast(device.type, dtype=torch.bfloat16, enabled=device.type == "cuda"):
        y = model(x, m)
    out = (y[0].float().permute(1, 2, 0)[:sh, :sw].clamp(0, 1) * 255).round().byte().cpu().numpy()
    if scale < 1.0:
        out = cv2.resize(out, (w, h), interpolation=cv2.INTER_CUBIC)
    return out


def inpaint(model, image: np.ndarray, bubbles: list[Bubble]) -> Inpainted:
    if not bubbles:
        return Inpainted(image, [])
    h, w = image.shape[:2]
    core, mask = text_mask(image, bubbles)
    result = image.copy()
    fills: list[Fill] = []
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(mask, np.ones((25, 25), np.uint8)), connectivity=8)
    ring_k = np.ones((9, 9), np.uint8)
    for i in range(1, n):
        x, y, cw, ch = stats[i, :4]
        side = max(cw, ch)
        margin = max(32, int(0.4 * side), min(side, (LAMA_MAX_SIDE - side) // 2))
        x0, y0, x1, y1 = max(0, x - margin), max(0, y - margin), min(w, x + cw + margin), min(h, y + ch + margin)
        region_mask = mask[y0:y1, x0:x1] & np.where(labels[y0:y1, x0:x1] == i, 255, 0).astype(np.uint8)
        crop = result[y0:y1, x0:x1]
        ring = (cv2.dilate(region_mask, ring_k) > 0) & (region_mask == 0)
        sel = region_mask > 0
        flat = flat_fill(crop, sel, core[y0:y1, x0:x1] > 0, ring)
        if flat is not None:
            color, fill = flat
            crop[fill] = color
            fills.append("flat")
        else:
            crop[sel] = run_lama(model, crop, region_mask)[sel]
            fills.append("textured")
    return Inpainted(result, fills)
