from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from . import lama
from .config import LAMA_PATH
from .detect import column_width
from .model import Bubble

LAMA_MAX_SIDE = 1024
FLAT_STD = 8.0
INK_DEV = 32
MAX_CROSSINGS = 4
MAX_OUTLINE = 0.15
DILATION_OFFSET = 20
SNAP_FRAC = 0.15
CRF_SCALE = 2 / 3


Fill = Literal["flat", "textured"]


@dataclass(frozen=True)
class Inpainted:
    image: np.ndarray
    fills: list[Fill]


def load_model(device: str):
    return lama.load(LAMA_PATH, device)


def crf_refine(rgb: np.ndarray, raw: np.ndarray) -> np.ndarray:
    import pydensecrf.densecrf as dcrf
    from pydensecrf.utils import unary_from_softmax

    h, w = raw.shape
    ph, pw = (-h) % 8, (-w) % 8
    rgb = np.pad(rgb, ((0, ph), (0, pw), (0, 0)), mode="edge")
    raw = np.pad(raw, ((0, ph), (0, pw)))
    prob = np.stack([255 - raw, raw]).astype(np.float32) / 255.0
    d = dcrf.DenseCRF2D(w + pw, h + ph, 2)
    d.setUnaryEnergy(np.ascontiguousarray(unary_from_softmax(prob.reshape(2, -1))))
    d.addPairwiseGaussian(sxy=1, compat=3, kernel=dcrf.DIAG_KERNEL, normalization=dcrf.NO_NORMALIZATION)
    d.addPairwiseBilateral(sxy=23, srgb=7, rgbim=np.ascontiguousarray(rgb), compat=20, kernel=dcrf.DIAG_KERNEL, normalization=dcrf.NO_NORMALIZATION)
    q = np.array(d.inference(5))
    return (q.argmax(0).reshape(raw.shape)[:h, :w] * 255).astype(np.uint8)


def glyph_size(seg: np.ndarray, vertical: bool, cells: int) -> int:
    ys, xs = np.nonzero(seg)
    bw, bh = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
    return max(3, int(min(column_width(seg, vertical), bw, bh, (bw * bh / max(1, cells)) ** 0.5)))


def block_mask(image: np.ndarray, bubble: Bubble, core: np.ndarray, out: np.ndarray) -> None:
    h, w = out.shape
    block = bubble.block
    mb = block.mask_box
    seg = (block.mask > 0).astype(np.uint8)
    if not seg.any():
        return
    text_size = glyph_size(seg > 0, block.vertical, len("".join(bubble.source.split())))
    ys, xs = np.nonzero(seg)
    grow = int(text_size * 0.1)
    x0, y0 = max(0, mb.x0 + int(xs.min()) - grow), max(0, mb.y0 + int(ys.min()) - grow)
    x1, y1 = min(w, mb.x0 + int(xs.max()) + 1 + grow), min(h, mb.y0 + int(ys.max()) + 1 + grow)
    raw = np.zeros((y1 - y0, x1 - x0), np.uint8)
    sx0, sy0, sx1, sy1 = max(x0, mb.x0), max(y0, mb.y0), min(x1, mb.x1), min(y1, mb.y1)
    raw[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = 255 * seg[sy0 - mb.y0 : sy1 - mb.y0, sx0 - mb.x0 : sx1 - mb.x0]
    small = (max(1, round((x1 - x0) * CRF_SCALE)), max(1, round((y1 - y0) * CRF_SCALE)))
    raw_s = np.where(cv2.resize(raw, small, interpolation=cv2.INTER_LINEAR) > 0, 255, 0).astype(np.uint8)
    rgb_s = cv2.resize(image[y0:y1, x0:x1], small, interpolation=cv2.INTER_LINEAR)
    snap = 2 * max(2, round(text_size * CRF_SCALE * SNAP_FRAC)) + 1
    near = cv2.dilate(raw_s, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (snap, snap)))
    refined = cv2.resize(crf_refine(cv2.bilateralFilter(rgb_s, 17, 80, 80), raw_s) & near, (x1 - x0, y1 - y0), interpolation=cv2.INTER_LINEAR)
    text = np.where((refined > 0) | (raw > 0), 255, 0).astype(np.uint8)
    core[y0:y1, x0:x1] |= text
    dilate = max((int((text_size * CRF_SCALE + DILATION_OFFSET) * 0.3) // 2) * 2 + 1, 3)
    reach = round((dilate // 2) / CRF_SCALE)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * reach + 1, 2 * reach + 1))
    grown = cv2.dilate(np.pad(text, reach), kernel)
    cx0, cy0, cx1, cy1 = max(0, x0 - reach), max(0, y0 - reach), min(w, x1 + reach), min(h, y1 + reach)
    out[cy0:cy1, cx0:cx1] |= grown[cy0 - y0 + reach : cy1 - y0 + reach, cx0 - x0 + reach : cx1 - x0 + reach]


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
