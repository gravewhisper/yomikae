from __future__ import annotations

import ast

import cv2
import numpy as np

from .model import Balloon, Box, TextBlock
from .tiles import owns, tiles

CONF_THRESH = 0.25
NMS_THRESH = 0.7
CLAIM = 0.5
TOUCH = 0.1


def input_size(session) -> int:
    meta = session.get_modelmeta().custom_metadata_map
    if "imgsz" in meta:
        return int(ast.literal_eval(meta["imgsz"])[0])
    return int(session.get_inputs()[0].shape[2])


def letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, float, int, int]:
    h, w = image.shape[:2]
    r = min(size / h, size / w)
    nh, nw = round(h * r), round(w * r)
    top, left = (size - nh) // 2, (size - nw) // 2
    canvas = np.full((size, size, 3), 114, np.uint8)
    canvas[top : top + nh, left : left + nw] = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    return canvas, r, left, top


def infer(session, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, int, int]:
    size = input_size(session)
    lb, r, left, top = letterbox(image, size)
    x = lb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    pred, proto = session.run(None, {session.get_inputs()[0].name: x})
    return pred[0].T, proto[0], r, left, top


def segment(session, image: np.ndarray) -> list[Balloon]:
    h, w = image.shape[:2]
    out = []
    for tile in tiles(w, h):
        win = tile.window
        for b in segment_window(session, image[win.y0 : win.y1, win.x0 : win.x1]):
            box = Box(b.box.x0 + win.x0, b.box.y0 + win.y0, b.box.x1 + win.x0, b.box.y1 + win.y0)
            if owns(tile, *box.center, w, h):
                out.append(Balloon(box=box, mask=b.mask, score=b.score))
    return out


def segment_window(session, image: np.ndarray) -> list[Balloon]:
    h, w = image.shape[:2]
    pred, proto, r, left, top = infer(session, image)
    c, ph, pw = proto.shape
    nc = pred.shape[1] - 4 - c
    scores = pred[:, 4 : 4 + nc].max(1)
    pred, scores = pred[scores > CONF_THRESH], scores[scores > CONF_THRESH]
    if not len(pred):
        return []
    xywh = np.stack([pred[:, 0] - pred[:, 2] / 2, pred[:, 1] - pred[:, 3] / 2, pred[:, 2], pred[:, 3]], 1)
    keep = np.array(cv2.dnn.NMSBoxes(xywh.tolist(), scores.tolist(), CONF_THRESH, NMS_THRESH)).reshape(-1)
    stride = round(input_size(session) / pw)
    out = []
    for i in keep:
        x0, y0, bw, bh = xywh[i]
        box = Box(max(0, int((x0 - left) / r)), max(0, int((y0 - top) / r)), min(w, int(np.ceil((x0 + bw - left) / r))), min(h, int(np.ceil((y0 + bh - top) / r))))
        if box.w <= 0 or box.h <= 0:
            continue
        lx0, ly0, lx1, ly1 = box.x0 * r + left, box.y0 * r + top, box.x1 * r + left, box.y1 * r + top
        px0, py0 = int(lx0 // stride), int(ly0 // stride)
        px1, py1 = min(pw, int(np.ceil(lx1 / stride))), min(ph, int(np.ceil(ly1 / stride)))
        logits = (pred[i, 4 + nc :] @ proto[:, py0:py1, px0:px1].reshape(c, -1)).reshape(py1 - py0, px1 - px0)
        up = cv2.resize(logits, ((px1 - px0) * stride, (py1 - py0) * stride), interpolation=cv2.INTER_LINEAR)
        cut = up[round(ly0) - py0 * stride : round(ly1) - py0 * stride, round(lx0) - px0 * stride : round(lx1) - px0 * stride]
        if not cut.size:
            continue
        mask = cv2.resize(cut, (box.w, box.h), interpolation=cv2.INTER_LINEAR) > 0
        if mask.any():
            out.append(Balloon(box=box, mask=mask, score=float(scores[i])))
    return out


def text_share(block: TextBlock, balloon: Balloon) -> float:
    ys, xs = np.nonzero(block.mask)
    if not len(ys):
        return 0.0
    ys, xs = ys + block.mask_box.y0 - balloon.box.y0, xs + block.mask_box.x0 - balloon.box.x0
    inside = (ys >= 0) & (xs >= 0) & (ys < balloon.box.h) & (xs < balloon.box.w)
    return float(balloon.mask[ys[inside], xs[inside]].sum()) / len(ys)


def claims(blocks: list[TextBlock], balloons: list[Balloon]) -> list[int | None]:
    out: list[int | None] = []
    for block in blocks:
        shares = [text_share(block, b) for b in balloons]
        best = int(np.argmax(shares)) if shares else -1
        out.append(best if shares and shares[best] >= CLAIM else None)
    return out


def unclaimed(blocks: list[TextBlock], balloons: list[Balloon]) -> list[Balloon]:
    return [b for b in balloons if all(text_share(block, b) < TOUCH for block in blocks)]
