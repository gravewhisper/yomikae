from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .model import Box, TextBlock
from .tiles import owns, tiles

INPUT_SIZE = 1024
CONF_THRESH = 0.4
NMS_THRESH = 0.35
SEG_THRESH = 0.3
LINE_THRESH = 0.3
MASK_PAD_FRAC = 0.25
CONTAINED_FRAC = 0.9
LIGHT = 200
DARK = 100
LOBE_PROMINENCE = 0.1
LOBE_LEVELS = 40
LOBE_SCAN = 64
LOBE_MIN_PEAK = 0.4
MIN_PART_SHARE = 0.1
STRADDLE = 0.1
OVERLAP_FRAC = 0.3
BUBBLE_REACH = 4
LINE_MIN_PIXELS = 20
LINE_GROUP_PIXELS = 40
LINE_SEG_SUPPORT = 0.2
LINE_SCORE = 0.6
LINE_UNCLIP = 1.5
LINE_BLOCK_FLOOR = 0.04
LINE_BLOCK_IOU = 0.3
INVERTED_IOU = 0.1
INVERTED_DARK = 0.4
JOIN_WIDTH_RATIO = 1.3
JOIN_SINGLE = 1.5
JOIN_GAP = 0.25
JOIN_ALONG = 0.7
HEAD_COVER = 0.5
GPU_MEM_LIMIT = 1536 * 2**20


def load_session(path: Path, device: str = "cpu"):
    import onnxruntime as ort

    if device == "cpu":
        options = ort.SessionOptions()
        options.enable_cpu_mem_arena = False
        return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
    import torch  # noqa: F401

    ort.preload_dlls()
    cuda = ("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested", "gpu_mem_limit": GPU_MEM_LIMIT})
    return ort.InferenceSession(str(path), providers=[cuda, "CPUExecutionProvider"])


def letterbox(img: np.ndarray) -> tuple[np.ndarray, float, int, int]:
    h, w = img.shape[:2]
    r = min(INPUT_SIZE / h, INPUT_SIZE / w)
    nw, nh = round(w * r), round(h * r)
    out = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), np.uint8)
    out[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    return out, r, nw, nh


def candidates(blk: np.ndarray, floor: float) -> np.ndarray:
    scores = (blk[:, 4:5] * blk[:, 5:]).max(1)
    b = blk[scores >= floor]
    return np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2], 1)


def iou(box: Box, others: np.ndarray) -> np.ndarray:
    ix = np.clip(np.minimum(others[:, 2], box.x1) - np.maximum(others[:, 0], box.x0), 0, None)
    iy = np.clip(np.minimum(others[:, 3], box.y1) - np.maximum(others[:, 1], box.y0), 0, None)
    inter = ix * iy
    return inter / (box.w * box.h + (others[:, 2] - others[:, 0]) * (others[:, 3] - others[:, 1]) - inter)


def decode_blocks(blk: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    scores_all = blk[:, 4:5] * blk[:, 5:]
    scores = scores_all.max(1)
    keep = scores > CONF_THRESH
    b, scores = blk[keep], scores[keep]
    if len(b) == 0:
        return np.zeros((0, 4), np.float32), scores
    xywh = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 2], b[:, 3]], 1)
    idx = np.array(cv2.dnn.NMSBoxes(xywh.tolist(), scores.tolist(), CONF_THRESH, NMS_THRESH)).reshape(-1)
    xywh, scores = xywh[idx], scores[idx]
    xyxy = np.concatenate([xywh[:, :2], xywh[:, :2] + xywh[:, 2:]], 1)
    return xyxy, scores


def is_vertical(lines: np.ndarray, box: Box) -> bool:
    region = lines[box.y0 : box.y1, box.x0 : box.x1]
    _, _, stats, _ = cv2.connectedComponentsWithStats(region, connectivity=8)
    tall = sum(int(s[4]) for s in stats[1:] if s[3] > 1.5 * s[2])
    wide = sum(int(s[4]) for s in stats[1:] if s[2] > 1.5 * s[3])
    if tall == wide:
        return box.h > box.w
    return tall > wide


def drop_merged_blocks(boxes: list[Box]) -> list[Box]:

    def contains(outer: Box, inner: Box) -> bool:
        ix = max(0, min(outer.x1, inner.x1) - max(outer.x0, inner.x0))
        iy = max(0, min(outer.y1, inner.y1) - max(outer.y0, inner.y0))
        return ix * iy >= CONTAINED_FRAC * inner.w * inner.h

    return [b for b in boxes if sum(o is not b and contains(b, o) for o in boxes) < 2]


def lobes(interior: np.ndarray) -> np.ndarray:
    d = cv2.distanceTransform(np.pad(interior, 1).astype(np.uint8), cv2.DIST_L2, 3)[1:-1, 1:-1]
    step = max(1, -(-max(d.shape) // LOBE_SCAN))
    small = d[::step, ::step]
    top = float(small.max())
    seeds, count = None, 1
    for k in range(1, LOBE_LEVELS):
        level = top * (1 - k / LOBE_LEVELS)
        n, lab = cv2.connectedComponents((small > level).astype(np.uint8), connectivity=8)
        peaks = [
            i for i in range(1, n)
            if (peak := float(small[lab == i].max())) >= LOBE_MIN_PEAK * top and peak - level >= LOBE_PROMINENCE * peak
        ]
        if len(peaks) > count:
            seeds, count = [lab == i for i in peaks], len(peaks)
    if seeds is None:
        return np.zeros(interior.shape, np.int32)
    marks = np.zeros(small.shape, np.uint8)
    for i, seed in enumerate(seeds):
        marks[seed] = i + 1
    marks = cv2.resize(marks, (d.shape[1], d.shape[0]), interpolation=cv2.INTER_NEAREST) * interior
    _, near = cv2.distanceTransformWithLabels((marks == 0).astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(marks)
    owner = np.concatenate([[0], marks[ys, xs].astype(np.int32) - 1])
    return owner[near]


def column_width(mask: np.ndarray, vertical: bool) -> int:
    profile = mask.sum(0) if vertical else mask.sum(1)
    on = np.concatenate([[0], (profile > 0.1 * profile.max()).astype(int), [0]])
    widths = np.nonzero(np.diff(on) == -1)[0] - np.nonzero(np.diff(on) == 1)[0]
    return max(3, int(np.median(widths))) if len(widths) else 3


def split_by_bubble(gray: np.ndarray, block: TextBlock) -> list[TextBlock]:
    h, w = gray.shape
    box = block.box
    if not block.mask.any() or float(np.median(gray[box.y0 : box.y1, box.x0 : box.x1])) < DARK:
        return [block]
    search = box.expand(BUBBLE_REACH * max(box.w, box.h), w, h)
    mb = block.mask_box
    ty, tx = np.nonzero(block.mask)
    ty, tx = ty + mb.y0 - search.y0, tx + mb.x0 - search.x0
    light = np.where(gray[search.y0 : search.y1, search.x0 : search.x1] >= LIGHT, 255, 0).astype(np.uint8)
    seen = (ty >= 0) & (tx >= 0) & (ty < light.shape[0]) & (tx < light.shape[1])
    light[ty[seen], tx[seen]] = 255
    light = cv2.erode(light, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(light, connectivity=4)
    sh, sw = light.shape
    part = np.full(light.shape, -1, np.int32)
    parts = 0
    for i in np.flatnonzero(stats[1:, 4] >= MIN_PART_SHARE * len(ty)) + 1:
        x, y, cw, ch, _ = (int(v) for v in stats[i])
        if (x == 0 and search.x0 > 0) or (y == 0 and search.y0 > 0) or (x + cw == sw and search.x1 < w) or (y + ch == sh and search.y1 < h):
            continue
        here = (tx >= x) & (tx < x + cw) & (ty >= y) & (ty < y + ch)
        if not here.any():
            continue
        inside = np.zeros((ch, cw), np.uint8)
        contours, _ = cv2.findContours((labels[y : y + ch, x : x + cw] == i).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(inside, contours, -1, 1, -1)
        inside = inside > 0
        if not inside[ty[here] - y, tx[here] - x].any():
            continue
        sub = lobes(inside)
        part[y : y + ch, x : x + cw][inside] = parts + sub[inside]
        parts += int(sub.max()) + 1
    owner = part[ty, tx]
    if parts < 2 or (owner >= 0).mean() < 0.5:
        return [block]
    text = np.zeros(light.shape, np.uint8)
    text[ty, tx] = 1
    reach = 2 * column_width(block.mask > 0, block.vertical)
    kernel = np.ones((reach, 1) if block.vertical else (1, reach), np.uint8)
    columns = cv2.morphologyEx(cv2.morphologyEx(text, cv2.MORPH_CLOSE, kernel), cv2.MORPH_OPEN, kernel) * (part >= 0)
    count, col = cv2.connectedComponents(columns.astype(np.uint8), connectivity=8)
    group = list(range(parts))

    def root(p: int) -> int:
        while group[p] != p:
            p = group[p]
        return p

    for c in range(1, count):
        held = np.bincount(part[(col == c) & (part >= 0)], minlength=parts)
        crossed = [root(p) for p in np.flatnonzero(held >= STRADDLE * held.sum())]
        for p in crossed[1:]:
            group[p] = crossed[0]
    owner = np.array([root(p) if p >= 0 else -1 for p in range(-1, parts)])[owner + 1]
    shares = np.bincount(owner[owner >= 0], minlength=parts) / len(owner)
    kept = [p for p in range(parts) if shares[p] >= MIN_PART_SHARE]
    if len(kept) < 2:
        return [block]
    boxes = []
    for p in kept:
        py, px = ty[owner == p] + search.y0, tx[owner == p] + search.x0
        boxes.append(Box(int(px.min()), int(py.min()), int(px.max()) + 1, int(py.max()) + 1))
    for a in boxes:
        for b in boxes:
            if a is b:
                continue
            across = min(a.x1, b.x1) - max(a.x0, b.x0) if block.vertical else min(a.y1, b.y1) - max(a.y0, b.y0)
            gap = b.y0 - a.y1 if block.vertical else b.x0 - a.x1
            if across >= 0.5 * min((a.w, b.w) if block.vertical else (a.h, b.h)) and -reach < gap < reach // 2:
                return [block]
    full = np.zeros((h, w), np.uint8)
    full[mb.y0 : mb.y1, mb.x0 : mb.x1] = block.mask
    out = []
    for p, found in zip(kept, boxes):
        mine = owner == p
        box = found.expand(2, w, h)
        mask_box = box.expand(int(min(box.w, box.h) * MASK_PAD_FRAC) + 4, w, h)
        own = full.copy()
        others = (owner >= 0) & ~mine
        own[ty[others] + search.y0, tx[others] + search.x0] = 0
        out.append(TextBlock(box=box, vertical=block.vertical, mask_box=mask_box, mask=own[mask_box.y0 : mask_box.y1, mask_box.x0 : mask_box.x1].copy(), recovered=block.recovered))
    return out


def merge_overlapping(boxes: list[Box]) -> list[Box]:
    out = list(boxes)
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                ix = max(0, min(a.x1, b.x1) - max(a.x0, b.x0))
                iy = max(0, min(a.y1, b.y1) - max(a.y0, b.y0))
                if ix * iy >= OVERLAP_FRAC * min(a.w * a.h, b.w * b.h):
                    out[i] = Box(min(a.x0, b.x0), min(a.y0, b.y0), max(a.x1, b.x1), max(a.y1, b.y1))
                    del out[j]
                    merged = True
                    break
            if merged:
                break
    return out


def join_split_lines(boxes: list[Box], lines: np.ndarray) -> list[Box]:

    def single_line(box: Box) -> tuple[bool, int] | None:
        vertical = is_vertical(lines, box)
        region = lines[box.y0 : box.y1, box.x0 : box.x1]
        if not region.any():
            return None
        across = box.w if vertical else box.h
        return (vertical, across) if across <= JOIN_SINGLE * column_width(region, vertical) else None

    out = list(boxes)
    joined = True
    while joined:
        joined = False
        for i, a in enumerate(out):
            for j, b in enumerate(out[:i]):
                la, lb = single_line(a), single_line(b)
                if not la or not lb or la[0] != lb[0]:
                    continue
                vertical, wa, wb = la[0], la[1], lb[1]
                gap = max(a.x0, b.x0) - min(a.x1, b.x1) if vertical else max(a.y0, b.y0) - min(a.y1, b.y1)
                along = min(a.y1, b.y1) - max(a.y0, b.y0) if vertical else min(a.x1, b.x1) - max(a.x0, b.x0)
                if max(wa, wb) <= JOIN_WIDTH_RATIO * min(wa, wb) and gap <= JOIN_GAP * min(wa, wb) and along >= JOIN_ALONG * min((a.h, b.h) if vertical else (a.w, b.w)):
                    out[j] = Box(min(a.x0, b.x0), min(a.y0, b.y0), max(a.x1, b.x1), max(a.y1, b.y1))
                    del out[i]
                    joined = True
                    break
            if joined:
                break
    return out


def uncovered_lines(prob: np.ndarray, seg: np.ndarray, boxes: list[tuple[float, float, float, float]], weak: np.ndarray) -> list[tuple[int, int, int, int]]:
    h, w = prob.shape
    weak = weak.reshape(-1, 4)
    covered = np.zeros(prob.shape, bool)
    for x0, y0, x1, y1 in boxes:
        covered[max(0, int(y0)) : int(y1) + 1, max(0, int(x0)) : int(x1) + 1] = True
    n, labels, stats, _ = cv2.connectedComponentsWithStats((prob > LINE_THRESH).astype(np.uint8), connectivity=8)
    parts = []
    for i in range(1, n):
        x, y, cw, ch, area = (int(v) for v in stats[i])
        if area < LINE_MIN_PIXELS or covered[labels == i].mean() >= 0.5:
            continue
        grow = round(cw * ch * LINE_UNCLIP / (2 * (cw + ch)))
        parts.append((Box(x, y, x + cw, y + ch).expand(grow, w, h), area, float(prob[labels == i].sum()), min(cw, ch)))
    group = list(range(len(parts)))

    def root(p: int) -> int:
        while group[p] != p:
            p = group[p]
        return p

    for i, (a, _, _, ta) in enumerate(parts):
        for j, (b, _, _, tb) in enumerate(parts[:i]):
            reach = min(ta, tb)
            if a.x0 - reach < b.x1 and b.x0 - reach < a.x1 and a.y0 - reach < b.y1 and b.y0 - reach < a.y1:
                group[root(i)] = root(j)
    out = []
    for g in {root(i) for i in range(len(parts))}:
        members = [p for i, p in enumerate(parts) if root(i) == g]
        box = Box(min(m[0].x0 for m in members), min(m[0].y0 for m in members), max(m[0].x1 for m in members), max(m[0].y1 for m in members))
        area = sum(m[1] for m in members)
        score = sum(m[2] for m in members) / area
        support = float(seg[box.y0 : box.y1, box.x0 : box.x1].mean())
        if area >= LINE_GROUP_PIXELS and score >= LINE_SCORE and support >= LINE_SEG_SUPPORT and (iou(box, weak) > LINE_BLOCK_IOU).any():
            out.append((box.x0, box.y0, box.x1, box.y1))
    return out


def reading_order(blocks: list[TextBlock]) -> list[TextBlock]:
    if not blocks:
        return blocks
    band = max(1, int(np.median([b.box.h for b in blocks])))
    return sorted(blocks, key=lambda b: (b.box.y0 // band, -b.box.x1))


def infer(session, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, int, int]:
    h, w = image.shape[:2]
    cover = tiles(w, h)
    r = INPUT_SIZE / max(cover[0].window.w, cover[0].window.h)
    nw, nh = round(w * r), round(h * r)
    blks, seg, det = [], np.zeros((nh, nw), np.float32), np.zeros((nh, nw), np.float32)
    for tile in cover:
        win, own = tile.window, tile.owned
        padded, _, tw, th = letterbox(image[win.y0 : win.y1, win.x0 : win.x1])
        blk, tile_seg, tile_det = session.run(None, {"images": padded.transpose(2, 0, 1)[None].astype(np.float32) / 255.0})
        dx, dy = round(win.x0 * r), round(win.y0 * r)
        x0, y0 = round(own.x0 * r), round(own.y0 * r)
        x1, y1 = min(round(own.x1 * r), dx + tw), min(round(own.y1 * r), dy + th)
        seg[y0:y1, x0:x1] = tile_seg[0, 0, y0 - dy : y1 - dy, x0 - dx : x1 - dx]
        det[y0:y1, x0:x1] = tile_det[0, 0, y0 - dy : y1 - dy, x0 - dx : x1 - dx]
        b = blk[0].copy()
        b[:, 0] += dx
        b[:, 1] += dy
        blks.append(b[owns(tile, b[:, 0] / r, b[:, 1] / r, w, h)])
    return np.concatenate(blks), seg, det, r, nw, nh


def detect(session, image: np.ndarray) -> list[TextBlock]:
    h, w = image.shape[:2]
    blk, seg, det, r, nw, nh = infer(session, image)
    inv_blk, inv_seg, inv_det, _, _, _ = infer(session, 255 - image)
    seg, lines = seg > SEG_THRESH, det > LINE_THRESH

    xyxy, _ = decode_blocks(blk)
    found = [xyxy, np.array(uncovered_lines(det, seg, xyxy.tolist(), candidates(blk, LINE_BLOCK_FLOOR)), np.float32).reshape(-1, 4)]
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (nw, nh), interpolation=cv2.INTER_AREA)
    inv_text = (inv_seg > SEG_THRESH).astype(np.uint8)
    _, inv_labels, inv_stats, _ = cv2.connectedComponentsWithStats(inv_text, connectivity=8)
    for x0, y0, x1, y1 in decode_blocks(inv_blk)[0]:
        box = Box(int(max(0, x0)), int(max(0, y0)), int(min(nw, x1)), int(min(nh, y1)))
        if not box.w or not box.h or (small[box.y0 : box.y1, box.x0 : box.x1] < 128).mean() < INVERTED_DARK:
            continue
        if len(xyxy) and (iou(box, xyxy) > INVERTED_IOU).any():
            continue
        for i in set(np.unique(inv_labels[box.y0 : box.y1, box.x0 : box.x1])) - {0}:
            cx, cy, cw, ch, _ = (int(v) for v in inv_stats[i])
            box = Box(min(box.x0, cx), min(box.y0, cy), max(box.x1, cx + cw), max(box.y1, cy + ch))
        found.append(np.array([box.as_list()], np.float32))
        seg[box.y0 : box.y1, box.x0 : box.x1] |= inv_seg[box.y0 : box.y1, box.x0 : box.x1] > SEG_THRESH
        lines[box.y0 : box.y1, box.x0 : box.x1] |= inv_det[box.y0 : box.y1, box.x0 : box.x1] > LINE_THRESH

    seg_mask = cv2.resize(seg.astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_NEAREST)
    line_map = cv2.resize(lines.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
    boxes = [Box(int(max(0, x0)), int(max(0, y0)), int(min(w, x1)), int(min(h, y1))) for x0, y0, x1, y1 in np.concatenate(found) / r]
    heads = np.zeros((h, w), bool)
    for x0, y0, x1, y1 in xyxy / r:
        heads[int(max(0, y0)) : int(max(0, y1)), int(max(0, x0)) : int(max(0, x1))] = True
    blocks = []
    for box in join_split_lines(merge_overlapping(drop_merged_blocks([b for b in boxes if b.w >= 6 and b.h >= 6])), line_map):
        mask_box = box.expand(int(min(box.w, box.h) * MASK_PAD_FRAC) + 4, w, h)
        mask = seg_mask[mask_box.y0 : mask_box.y1, mask_box.x0 : mask_box.x1].copy()
        recovered = heads[box.y0 : box.y1, box.x0 : box.x1].mean() < HEAD_COVER
        blocks.append(TextBlock(box=box, vertical=is_vertical(line_map, box), mask_box=mask_box, mask=mask, recovered=bool(recovered)))
    return reading_order([part for block in blocks for part in split_by_bubble(gray, block)])
