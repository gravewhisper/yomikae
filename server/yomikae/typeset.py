from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import re

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import detect, lining
from .config import FALLBACK_FONT_PATHS, lettering_fonts
from .model import Balloon, Box, Bubble, TextBlock

MIN_SIZE = 10
TINY = 6
MAX_SIZE = 32
BAND = 0.2
SAME_SIZE = 0.1
MIN_FILL = 0.3
SWIM_FILL = 0.2
SWIM_BAND = 0.5
MARGIN = 0.35
FRAME_MARGIN = 0.2
FRAME_PAD = 3
MAX_PAD = 16
EXTRA_LINES = 2
RELINE_SLACK = 0.1
LINE_SPACING = 1.12
SHOUT_GLYPH = 1.8
LOUD_GLYPH = 1.2
SHOUT_SCALE = 1.25
SHOUT_GLYPH_CAP = 1.6
SHOUT_WORDS = 12
SPIKE_DEPTH = 0.04
SPIKE_FULL = 0.6
BURST = 3
POINT_TOLERANCE = 0.015
POINT_ANGLE = 130
POINT_HEIGHT = 0.05
POINTS = 4
NOTCHES = 2
OUTLINE_RING = 3
OUTLINE_INK = 0.3
HYPHEN_FLOOR = 0.75
SOURCE_SCALE = 0.8
SOURCE_CAP = 64
CAPS_PITCH = 1.45
LIGHT = 200
MIN_LIGHT = 0.3
MAX_HOLE = 0.05
MAX_BUBBLE_GROWTH = 14
GUTTER = 12
RIDGE = 1.5
WALK = 3
OFF_CENTRE = 0.05
FRAME_GAP = 0.6
FRAME_REACH = 6
FRAME_CONTRAST = 6
FRAME_COVER = 0.9
FRAME_CLOSED = 0.85
FRAME_LINES = 3
BUSY = 0.02
REPEATS = re.compile(r"([~!?])\1{2,}")
REPLACEMENTS = str.maketrans({"～": "~", "〜": "~", "！": "!", "？": "?", "　": " ", "\ufe0f": None})


@dataclass(frozen=True, eq=False)
class Region:

    box: Box
    mask: np.ndarray
    anchor: tuple[int, int]
    on_art: bool
    dark: bool
    pad: int = 0
    busy: bool = False
    margin: float = MARGIN
    burst: bool = False


@dataclass(frozen=True)
class Layout:

    size: int
    lines: list[tuple[str, int, int]]
    style: Style
    faults: int = 0
    clip: bool = False
    off: bool = False


class Fonts:

    def __init__(self, path: Path, fallbacks: tuple[Path, ...] = ()):
        from fontTools.ttLib import TTFont

        self.paths = (path, *fallbacks)
        self.cmaps = [set(TTFont(str(p)).getBestCmap()) for p in self.paths]

    @lru_cache(maxsize=256)
    def get(self, size: int, index: int = 0) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self.paths[index]), size)

    def font_index(self, ch: str) -> int | None:
        if ch == " ":
            return 0
        return next((i for i, cmap in enumerate(self.cmaps) if ord(ch) in cmap), None)

    def clean(self, text: str) -> str:
        text = REPEATS.sub(r"\1\1", " ".join(text.translate(REPLACEMENTS).split()))
        return "".join(ch for ch in text if self.font_index(ch) is not None)

    def runs(self, text: str) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for ch in text:
            i = self.font_index(ch)
            if out and out[-1][1] == i:
                out[-1] = (out[-1][0] + ch, i)
            else:
                out.append((ch, i))
        return out

    def length(self, text: str, size: int) -> float:
        return sum(self.get(size, i).getlength(run) for run, i in self.runs(text))

    @lru_cache(maxsize=65536)
    def measure(self, text: str, size: int) -> float:
        return self.length(text, size)

    @lru_cache(maxsize=256)
    def ink(self, size: int, sample: str) -> tuple[int, int]:
        _, top, _, bottom = self.get(size).getbbox(sample, anchor="ls")
        return -top, max(0, bottom)

    def write(self, draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, size: int, **style) -> None:
        x, baseline = xy
        for run, i in self.runs(text):
            font = self.get(size, i)
            draw.text((x, baseline), run, font=font, anchor="ls", **style)
            x += font.getlength(run)

    def draw(self, page: Image.Image, xy: tuple[int, int], text: str, size: int, fill: tuple, stroke: int = 0, halo: tuple = (255, 255, 255)) -> None:
        self.write(ImageDraw.Draw(page), xy, text, size, fill=fill, stroke_width=stroke, stroke_fill=halo)


@dataclass(frozen=True)
class Style:

    face: Fonts
    upper: bool

    def display(self, text: str) -> str:
        return self.face.clean(text.upper() if self.upper else text)

    def pitch(self, size: int) -> int:
        if self.upper:
            return round(self.face.ink(size, "H")[0] * CAPS_PITCH)
        return round(size * LINE_SPACING)

    def extent(self, size: int) -> tuple[int, int]:
        return self.face.ink(size, "HJQ," if self.upper else "Hdgjpqy,")


@dataclass(frozen=True)
class Lettering:

    dialogue: Style
    shout: Style


def load_lettering(case: str, fonts: tuple[Path, Path] | None = None) -> Lettering:
    dialogue, shout = fonts or lettering_fonts(case)
    upper = case == "upper"
    return Lettering(Style(Fonts(dialogue, FALLBACK_FONT_PATHS), upper), Style(Fonts(shout, FALLBACK_FONT_PATHS), upper))


def spikes(mask: np.ndarray) -> int:
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return 0
    outline = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(outline)
    if len(outline) < 4 or area <= 0:
        return 0
    defects = cv2.convexityDefects(outline, np.sort(cv2.convexHull(outline, returnPoints=False), axis=0))
    if defects is None:
        return 0
    deep = sorted(int(far) for _, _, far, depth in defects.reshape(-1, 4) if depth / 256 >= SPIKE_DEPTH * (4 * area / np.pi) ** 0.5)
    if len(deep) < 2:
        return 0
    points = outline[:, 0].astype(np.float32)
    n = len(points)
    count = 0
    for a, b in zip(deep, deep[1:] + [deep[0] + n]):
        lobe = points[np.arange(a, b + 1) % n]
        chord = lobe[-1] - lobe[0]
        length = float(np.hypot(*chord))
        if length < 1:
            continue
        rel = lobe - lobe[0]
        height = float(np.abs(rel[:, 0] * chord[1] - rel[:, 1] * chord[0]).max()) / length
        count += 1 if cv2.contourArea(lobe) < SPIKE_FULL * length * height else -1
    return count


def points(mask: np.ndarray) -> tuple[int, int]:
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return 0, 0
    outline = max(contours, key=cv2.contourArea)
    size = (4 * cv2.contourArea(outline) / np.pi) ** 0.5
    poly = cv2.approxPolyDP(outline, POINT_TOLERANCE * size, True)[:, 0].astype(np.float64)
    if len(poly) < 3:
        return 0, 0
    before, after = np.roll(poly, 1, 0), np.roll(poly, -1, 0)
    u, v, chord = before - poly, after - poly, after - before
    turn = (poly - before)[:, 0] * v[:, 1] - (poly - before)[:, 1] * v[:, 0]
    out = turn * np.sign(cv2.contourArea(poly.astype(np.float32), oriented=True)) > 0
    angle = np.degrees(np.arccos(np.clip((u * v).sum(1) / (np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1) + 1e-9), -1, 1)))
    height = np.abs(chord[:, 0] * u[:, 1] - chord[:, 1] * u[:, 0]) / (np.linalg.norm(chord, axis=1) + 1e-9)
    sharp = (angle < POINT_ANGLE) & (height >= POINT_HEIGHT * size)
    tips = sharp & out & ~np.roll(out, 1) & ~np.roll(out, -1)
    notches = sharp & ~out & np.roll(out, 1) & np.roll(out, -1)
    return int(tips.sum()), int(notches.sum())


def pointed(mask: np.ndarray) -> bool:
    lobes = spikes(mask)
    tips, notches = points(mask)
    return lobes >= BURST or (lobes > -BURST and tips >= POINTS and notches <= NOTCHES)


def is_burst(gray: np.ndarray, box: Box, mask: np.ndarray, dark: bool) -> bool:
    h, w = gray.shape
    area = box.expand(OUTLINE_RING + 1, w, h)
    inside = np.zeros((area.h, area.w), np.uint8)
    inside[box.y0 - area.y0 : box.y1 - area.y0, box.x0 - area.x0 : box.x1 - area.x0] = mask
    ring = cv2.dilate(inside, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * OUTLINE_RING + 1, 2 * OUTLINE_RING + 1))) > inside
    paper = gray[area.y0 : area.y1, area.x0 : area.x1]
    ink = (paper >= 128) if dark else (paper < 128)
    return bool(ring.any()) and float(ink[ring].mean()) >= OUTLINE_INK and pointed(mask)


def art_region(block: Box, w: int, h: int, dark: bool) -> Region:
    pad = max(4, round(min(block.w, block.h) * 0.15))
    target_w = max(block.w, round(block.h * 0.5))
    cx = (block.x0 + block.x1) // 2
    box = Box(max(0, cx - target_w // 2 - pad), max(0, block.y0 - pad), min(w, cx + target_w // 2 + pad), min(h, block.y1 + pad))
    anchor = (block.center[0] - box.x0, block.center[1] - box.y0)
    return Region(box=box, mask=np.ones((box.h, box.w), bool), anchor=anchor, on_art=True, dark=dark)


def frame_lines(coverage: np.ndarray, start: int, step: int) -> list[int]:
    out = []
    i = start
    while 0 <= i < len(coverage) and len(out) < FRAME_LINES:
        if coverage[i] >= FRAME_COVER:
            out.append(i)
            while 0 <= i < len(coverage) and coverage[i] >= FRAME_COVER / 2:
                i += step
        i += step
    return out


def ridges(sub: np.ndarray) -> np.ndarray:
    padded = np.pad(sub, ((FRAME_REACH, FRAME_REACH), (0, 0)), mode="edge")
    n = len(sub)
    above = np.max([padded[i : i + n] for i in range(FRAME_REACH)], axis=0)
    below = np.max([padded[FRAME_REACH + 1 + i : FRAME_REACH + 1 + i + n] for i in range(FRAME_REACH)], axis=0)
    return sub < np.minimum(above, below) - FRAME_CONTRAST


def frames(gray: np.ndarray, block: Box) -> list[Box]:
    h, w = gray.shape
    area = block.expand(round(FRAME_GAP * max(block.w, block.h)) + 2 * FRAME_REACH, w, h)
    sub = gray[area.y0 : area.y1, area.x0 : area.x1].astype(np.int16)
    across, down = ridges(sub), ridges(sub.T).T
    x0, y0, x1, y1 = block.x0 - area.x0, block.y0 - area.y0, block.x1 - area.x0, block.y1 - area.y0
    rows, cols = across[:, x0:x1].mean(1), down[y0:y1].mean(0)
    sides = (frame_lines(rows, y0 - 1, -1), frame_lines(rows, y1, 1), frame_lines(cols, x0 - 1, -1), frame_lines(cols, x1, 1))
    out = []
    for t in sides[0]:
        for b in sides[1]:
            for l in sides[2]:
                for r in sides[3]:
                    closed = min(across[t, l : r + 1].mean(), across[b, l : r + 1].mean(), down[t : b + 1, l].mean(), down[t : b + 1, r].mean())
                    if closed >= FRAME_CLOSED and np.median(sub[t + 1 : b, l + 1 : r]) >= LIGHT:
                        out.append(Box(area.x0 + l + 1, area.y0 + t + 1, area.x0 + r, area.y0 + b))
    return sorted(out, key=lambda f: f.w * f.h)


def frame_region(gray: np.ndarray, block: Box, box: Box) -> Region:
    pad = FRAME_PAD
    mask = np.zeros((box.h, box.w), bool)
    mask[pad:-pad, pad:-pad] = True
    anchor = (block.center[0] - box.x0, block.center[1] - box.y0)
    busy = float((gray[box.y0 : box.y1, box.x0 : box.x1] < LIGHT).mean()) > BUSY
    return Region(box=box, mask=mask, anchor=anchor, on_art=False, dark=False, pad=pad, busy=busy, margin=FRAME_MARGIN)


def within(inner: Box, outer: Box) -> bool:
    return inner.x0 >= outer.x0 - FRAME_REACH and inner.y0 >= outer.y0 - FRAME_REACH and inner.x1 <= outer.x1 + FRAME_REACH and inner.y1 <= outer.y1 + FRAME_REACH


def find_region(gray: np.ndarray, text: TextBlock, others: tuple[TextBlock, ...] = ()) -> Region:
    light = light_region(gray, text, others)
    boxes = frames(gray, text.box)
    if not boxes:
        return light
    holding = [b for b in boxes if within(light.box, b)]
    if light.on_art or not holding:
        return frame_region(gray, text.box, boxes[0])
    box = holding[0]
    touching = sum(abs(a - b) <= FRAME_REACH for a, b in zip(light.box.as_list(), box.as_list()))
    return frame_region(gray, text.box, box) if touching >= 3 else light


def text_pixels(text: TextBlock, origin: Box, shape: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.nonzero(text.mask)
    ys, xs = ys + text.mask_box.y0 - origin.y0, xs + text.mask_box.x0 - origin.x0
    keep = (ys >= 0) & (xs >= 0) & (ys < shape[0]) & (xs < shape[1])
    return ys[keep], xs[keep]


def lobe_parts(shape: np.ndarray) -> np.ndarray | None:
    found = detect.lobe_marks(shape)
    if found is None:
        return None
    d, labels = found
    kernel = np.ones((3, 3), np.uint8)
    for level in range(int(d.max()), -1, -1):
        allowed = shape & (d >= level)
        while True:
            grown = cv2.dilate(labels, kernel)
            new = allowed & (labels == 0) & (grown > 0)
            if not new.any():
                break
            labels[new] = grown[new]
    return detect.nearest_mark(labels)


def own_part(shape: np.ndarray, origin: Box, text: TextBlock, others: tuple[TextBlock, ...]) -> np.ndarray | None:
    if not others:
        return None
    lobes = lobe_parts(shape)
    if lobes is None:
        return None
    n = int(lobes.max()) + 1
    held = np.zeros((1 + len(others), n))
    centers = np.zeros((1 + len(others), 2))
    for k, t in enumerate((text, *others)):
        ys, xs = text_pixels(t, origin, shape.shape)
        inside = shape[ys, xs]
        held[k] = np.bincount(lobes[ys[inside], xs[inside]], minlength=n)
        centers[k] = (t.box.center[1] - origin.y0, t.box.center[0] - origin.x0)
    owner = held.argmax(0)
    for i in np.flatnonzero(held.sum(0) == 0):
        ys, xs = np.nonzero(shape & (lobes == i))
        if len(ys):
            owner[i] = int(np.argmin(np.hypot(centers[:, 0] - ys.mean(), centers[:, 1] - xs.mean())))
    if not (owner == 0).any():
        return None
    return shape & (owner == 0)[lobes]


def part_pad(part: np.ndarray) -> int:
    ys, xs = np.nonzero(part)
    if not len(ys):
        return 3
    return min(MAX_PAD, max(3, round(min(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1) * 0.07)))


def light_region(gray: np.ndarray, text: TextBlock, others: tuple[TextBlock, ...] = ()) -> Region:
    block = text.box
    h, w = gray.shape
    dark = float(np.median(gray[block.y0 : block.y1, block.x0 : block.x1])) < 100
    search = block.expand(max(block.w, block.h) * 2, w, h)
    sub = gray[search.y0 : search.y1, search.x0 : search.x1]
    if dark:
        sub = 255 - sub
    background = cv2.erode(np.where(sub >= LIGHT, 255, 0).astype(np.uint8), np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(background, connectivity=4)

    ys, xs = np.nonzero(text.mask)
    ys, xs = ys + text.mask_box.y0 - search.y0, xs + text.mask_box.x0 - search.x0
    keep = (ys >= 0) & (xs >= 0) & (ys < labels.shape[0]) & (xs < labels.shape[1])
    if keep.any():
        inside = labels[ys[keep], xs[keep]]
    else:
        inside = labels[block.y0 - search.y0 : block.y1 - search.y0, block.x0 - search.x0 : block.x1 - search.x0].ravel()
    counts = np.bincount(inside, minlength=n)
    light = inside.size - counts[0]
    counts[0] = 0
    label = int(counts.argmax())
    if not label or light < MIN_LIGHT * inside.size or counts[label] < 0.6 * light:
        return art_region(block, w, h, dark)
    x, y, cw, ch, area = (int(v) for v in stats[label])
    sh, sw = background.shape
    leaks = (
        (x == 0 and search.x0 > 0) or (y == 0 and search.y0 > 0)
        or (x + cw == sw and search.x1 < w) or (y + ch == sh and search.y1 < h)
    )
    if leaks or area > MAX_BUBBLE_GROWTH * block.w * block.h:
        return art_region(block, w, h, dark)
    comp = (labels[y : y + ch, x : x + cw] == label).astype(np.uint8)
    holes, hole_labels, hole_stats, _ = cv2.connectedComponentsWithStats(1 - comp, connectivity=4)
    border = set(hole_labels[0]) | set(hole_labels[-1]) | set(hole_labels[:, 0]) | set(hole_labels[:, -1])
    small = [i for i in range(1, holes) if i not in border and hole_stats[i][4] < MAX_HOLE * area]
    comp[np.isin(hole_labels, small)] = 1
    box = Box(search.x0 + x, search.y0 + y, search.x0 + x + cw, search.y0 + y + ch)
    burst = is_burst(gray, box, comp, dark)
    part = own_part(comp > 0, box, text, others)
    pad = min(MAX_PAD, max(3, round(min(cw, ch) * 0.07))) if part is None else part_pad(part)
    if part is not None:
        comp = part.astype(np.uint8)
    comp = cv2.dilate(comp * 255, np.ones((3, 3), np.uint8))
    comp = cv2.erode(comp, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * pad + 1, 2 * pad + 1)))
    left, top, right, bottom = pad - box.x0, pad - box.y0, box.x1 + pad - w, box.y1 + pad - h
    if left > 0:
        comp[:, :left] = 0
    if top > 0:
        comp[:top, :] = 0
    if right > 0:
        comp[:, -right:] = 0
    if bottom > 0:
        comp[-bottom:, :] = 0
    anchor = (block.center[0] - box.x0, block.center[1] - box.y0)
    return Region(box=box, mask=comp > 0, anchor=anchor, on_art=False, dark=dark, pad=pad, burst=burst)


def balloon_region(gray: np.ndarray, text: TextBlock, balloon: Balloon, pad: int | None = None, others: tuple[TextBlock, ...] = ()) -> Region:
    box = balloon.box
    block = text.box
    dark = float(np.median(gray[block.y0 : block.y1, block.x0 : block.x1])) < 100
    part = own_part(balloon.mask, box, text, others)
    if part is None:
        part = balloon.mask
        pad = pad or min(MAX_PAD, max(3, round(min(box.w, box.h) * 0.07)))
    else:
        pad = part_pad(part)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * pad + 1, 2 * pad + 1))
    mask = cv2.erode(np.pad(part, 1).astype(np.uint8), kernel)[1:-1, 1:-1] > 0
    sub = gray[box.y0 : box.y1, box.x0 : box.x1]
    paper = (255 - sub if dark else sub)[mask]
    busy = bool(paper.size) and float((paper < LIGHT).mean()) > BUSY
    anchor = (block.center[0] - box.x0, block.center[1] - box.y0)
    return Region(box=box, mask=mask, anchor=anchor, on_art=False, dark=dark, pad=pad, busy=busy)


def bubble_region(gray: np.ndarray, bubble: Bubble, others: tuple[TextBlock, ...] = ()) -> Region:
    grown = find_region(gray, bubble.block, others)
    if bubble.balloon is None or grown.margin == FRAME_MARGIN or grown.burst or (bubble.shared and not grown.on_art):
        return grown
    if grown.on_art:
        region = balloon_region(gray, bubble.block, bubble.balloon, others=others)
        return replace(region, burst=is_burst(gray, bubble.balloon.box, bubble.balloon.mask, region.dark))
    return balloon_region(gray, bubble.block, bubble.balloon, grown.pad)


def row_span(mask: np.ndarray, y0: int, y1: int, cx: int) -> tuple[int, int] | None:
    if y0 < 0 or y1 > mask.shape[0]:
        return None
    rows = mask[y0:y1].all(0)
    if not rows[cx]:
        return None
    gaps_left = np.flatnonzero(~rows[:cx])
    gaps_right = np.flatnonzero(~rows[cx:])
    left = gaps_left[-1] + 1 if len(gaps_left) else 0
    right = cx + gaps_right[0] if len(gaps_right) else len(rows)
    return int(left), int(right)


@lru_cache(maxsize=64)
def layout_centers(region: Region) -> tuple[tuple[int, int], ...]:
    ys, xs = np.nonzero(region.mask)
    if len(ys) == 0:
        return ()
    centers = []
    for cx, cy in ((int(xs.mean()), int(ys.mean())), region.anchor):
        if 0 <= cy < region.mask.shape[0] and 0 <= cx < region.mask.shape[1] and region.mask[cy, cx]:
            centers.append((cx, cy))
            break
    else:
        i = int(np.argmin(np.abs(ys - region.anchor[1]) + np.abs(xs - region.anchor[0])))
        centers.append((int(xs[i]), int(ys[i])))
    d = outline_distance(region)
    ys, xs = np.nonzero(d >= float(d.max()) - RIDGE)
    i = int(np.argmin((ys - ys.mean()) ** 2 + (xs - xs.mean()) ** 2))
    pole = (int(xs[i]), int(ys[i]))
    (cx, cy), (px, py) = centers[0], pole
    reach = OFF_CENTRE * region.mask.shape[0]
    steps = max(1, round(np.hypot(px - cx, py - cy) / WALK))
    for k in range(1, steps + 1):
        point = (round(cx + (px - cx) * k / steps), round(cy + (py - cy) * k / steps))
        if (k == steps or abs(point[1] - cy) <= reach) and region.mask[point[1], point[0]] and point != centers[-1]:
            centers.append(point)
    return tuple(centers)


@lru_cache(maxsize=64)
def outline_distance(region: Region) -> np.ndarray:
    return cv2.distanceTransform(np.pad(region.mask, 1).astype(np.uint8), cv2.DIST_L2, 3)[1:-1, 1:-1]


def inner_mask(region: Region, size: int) -> np.ndarray:
    extra = region.margin * size - region.pad
    if region.on_art or extra <= 0:
        return region.mask
    return outline_distance(region) > extra


def try_layout(
    text: str, region: Region, style: Style, size: int, hyphenate: bool, names: frozenset[str] = frozenset()
) -> Layout | None:
    fonts = style.face
    pitch = style.pitch(size)
    above, below = style.extent(size)
    mask = inner_mask(region, size)
    mh, mw = mask.shape
    items = lining.atoms(text)
    measure = lambda t: fonts.measure(t, size)  # noqa: E731
    best: tuple[lining.Lined, int, list[tuple[int, int]], list[int], bool] | None = None
    centers = layout_centers(region)
    tried = 0
    for n in range(1, (mh - above - below) // pitch + 2):
        found = False
        for cx, cy in centers:
            first = cy - (above + (n - 1) * pitch + below) // 2 + above
            baselines = [first + i * pitch for i in range(n)]
            spans = [row_span(mask, b - above, b + below, cx) for b in baselines]
            if any(s is None for s in spans):
                continue
            axis = int(np.median([(l + r) / 2 for l, r in spans]))
            widths = [r - l for l, r in spans]
            if hyphenate:
                items = lining.hyphenated(lining.atoms(text), max(widths), measure, names)
            lined = lining.break_lines(items, widths, measure)
            if lined is None:
                continue
            found = True
            off = abs(cy - centers[0][1]) > OFF_CENTRE * mh
            if best is None or (off, lined.cost) < (best[4], best[0].cost):
                best = (lined, axis, spans, baselines, off)
            break
        if not found:
            if best:
                break
            continue
        tried += 1
        if tried == EXTRA_LINES + 1:
            break
    if best is None:
        return None
    lined, axis, spans, baselines, off = best
    placed = []
    for line, (l, r), b in zip(lined.lines, spans, baselines):
        lw = fonts.length(line, size)
        x = round(min(max(axis - lw / 2, l), r - lw))
        placed.append((line, region.box.x0 + x, region.box.y0 + b))
    return Layout(size=size, lines=placed, style=style, faults=lined.faults + off, off=off)


def fit(text: str, region: Region, style: Style, cap: int, floor: int = MIN_SIZE, names: frozenset[str] = frozenset()) -> Layout | None:
    floor = min(max(MIN_SIZE, floor), max(MIN_SIZE, cap))
    readable = range(max(MIN_SIZE, cap), floor - 1, -1)
    ways = (False, True)
    for options, sizes in [*(((way,), readable) for way in ways), (ways, range(floor - 1, MIN_SIZE - 1, -1))]:
        best: Layout | None = None
        for size in sizes:
            if best and (best.faults == 0 or size < best.size * (1 - RELINE_SLACK)):
                break
            layout = next(filter(None, (try_layout(text, region, style, size, hyphenate, names) for hyphenate in options)), None)
            if layout and (best is None or layout.faults < best.faults):
                best = layout
        if best:
            return best
    return None


def fill(layout: Layout, region: Region) -> float:
    text_area = sum(layout.style.face.length(line, layout.size) for line, _, _ in layout.lines) * layout.size
    return text_area / max(1, int(region.mask.sum()))


def grow(text: str, region: Region, layout: Layout, limit: int, goal: float = MIN_FILL) -> Layout:
    if region.on_art or len(layout.lines) < 2:
        return layout
    best = layout
    for size in range(layout.size + 1, limit + 1):
        bigger = try_layout(text, region, layout.style, size, False)
        if bigger is None or bigger.off > layout.off:
            break
        best = bigger
        if fill(bigger, region) >= goal:
            break
    return best


def wanted_size(piece: Piece, region: Region, layout: Layout, page_size: int) -> int:
    if fill(layout, region) >= MIN_FILL:
        return layout.size
    grown = grow(piece.text, region, layout, min(piece.largest[1].size, round(page_size * (1 + BAND))))
    if fill(grown, region) < SWIM_FILL:
        grown = grow(piece.text, region, grown, min(piece.largest[1].size, round(page_size * (1 + SWIM_BAND))), SWIM_FILL)
    return grown.size


def same_sizes(sizes: list[int]) -> list[int]:
    out = list(sizes)
    group: list[int] = []
    for i in sorted(range(len(sizes)), key=lambda i: sizes[i]):
        if group and sizes[i] > sizes[group[0]] * (1 + SAME_SIZE):
            group = []
        group.append(i)
        out[i] = sizes[group[0]]
    return out


def fit_bubble(
    text: str, regions: tuple[Region, Region], style: Style, cap: int, floor: int = MIN_SIZE, names: frozenset[str] = frozenset()
) -> tuple[Region, Layout]:
    region, fallback = regions
    layout = fit(text, region, style, cap, floor, names)
    if layout is None and not region.on_art and (region.margin == FRAME_MARGIN or not region.mask.any()):
        region = fallback
        layout = fit(text, region, style, cap, floor, names)
    layout = layout or shrink(text, region, style, names)
    if layout is None:
        cx, cy = next(iter(layout_centers(region)), region.anchor)
        above, below = style.extent(MIN_SIZE)
        x = round(region.box.x0 + cx - style.face.length(text, MIN_SIZE) / 2)
        layout = Layout(MIN_SIZE, [(text, x, region.box.y0 + cy + (above - below) // 2)], style, clip=bool(region.mask.any()))
    return region, layout


def shrink(text: str, region: Region, style: Style, names: frozenset[str]) -> Layout | None:
    for size in range(MIN_SIZE - 1, TINY - 1, -1):
        layout = next(filter(None, (try_layout(text, region, style, size, hyphenate, names) for hyphenate in (False, True))), None)
        if layout:
            return layout
    return None


def box_distance(box: Box, area: Box) -> np.ndarray:
    xs = np.arange(area.x0, area.x1)
    ys = np.arange(area.y0, area.y1)
    dx = np.maximum(np.maximum(box.x0 - xs, xs - (box.x1 - 1)), 0)
    dy = np.maximum(np.maximum(box.y0 - ys, ys - (box.y1 - 1)), 0)
    return np.hypot(dy[:, None], dx[None, :])


def footprint(r: Region) -> np.ndarray:
    if not r.pad or r.on_art:
        return r.mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r.pad + 1, 2 * r.pad + 1))
    return cv2.dilate(r.mask.astype(np.uint8), kernel) > 0


def separate(blocks: list[Box], candidates: list[tuple[Region, Region]]) -> list[tuple[Region, Region]]:
    footprints = [[footprint(o) for o in others] for others in candidates]
    out = []
    for i, regions in enumerate(candidates):
        trimmed = []
        for r in regions:
            mask = r.mask.copy()
            for j, others in enumerate(candidates):
                if j == i:
                    continue
                for o, full in zip(others, footprints[j]):
                    area = Box(max(r.box.x0, o.box.x0), max(r.box.y0, o.box.y0), min(r.box.x1, o.box.x1), min(r.box.y1, o.box.y1))
                    if area.w <= 0 or area.h <= 0:
                        continue
                    mine = mask[area.y0 - r.box.y0 : area.y1 - r.box.y0, area.x0 - r.box.x0 : area.x1 - r.box.x0]
                    theirs = full[area.y0 - o.box.y0 : area.y1 - o.box.y0, area.x0 - o.box.x0 : area.x1 - o.box.x0]
                    mine &= ~(theirs & (box_distance(blocks[i], area) + GUTTER > box_distance(blocks[j], area)))
            trimmed.append(replace(r, mask=mask))
        out.append(tuple(trimmed))
    return out


def page_target(max_fits: list[int]) -> int:
    return min(MAX_SIZE, int(np.median(max_fits)))


def glyph_sizes(bubbles: list[Bubble]) -> list[tuple[float, float]]:

    def runs(block: TextBlock) -> float:
        mask = block.mask > 0
        if not mask.any():
            return 0.0
        profile = mask.sum(0) if block.vertical else mask.sum(1)
        on = np.concatenate([[0], (profile > 0.1 * profile.max()).astype(int), [0]])
        widths = np.nonzero(np.diff(on) == -1)[0] - np.nonzero(np.diff(on) == 1)[0]
        return float(np.median(widths[widths >= 0.3 * widths.max()]))

    def per_char(b: Bubble) -> float:
        box = b.block.box
        thickness = box.w if b.block.vertical else box.h
        return min(thickness, (box.w * box.h / max(1, len(b.source))) ** 0.5)

    estimates = [[runs(b.block) for b in bubbles], [per_char(b) for b in bubbles]]
    ratios = []
    for values in estimates:
        known = [v for v in values if v > 0]
        median = float(np.median(known)) if known else 0.0
        ratios.append([v / median if v > 0 and median else None for v in values])
    glyph = [min((r for r in pair if r is not None), default=1.0) for pair in zip(*ratios)]
    lettering = [min(r, c) if r > 0 else 0.0 for r, c in zip(*estimates)]
    return list(zip(glyph, lettering))


def is_shout(bubble: Bubble, glyph: float, burst: bool) -> bool:
    if len(bubble.translation.split()) > SHOUT_WORDS:
        return False
    return burst or glyph >= SHOUT_GLYPH or (glyph >= LOUD_GLYPH and ("！" in bubble.source or "!" in bubble.source))


@dataclass
class Piece:

    text: str
    names: frozenset[str]
    style: Style
    regions: tuple[Region, Region]
    largest: tuple[Region, Layout]
    shout: float | None
    source: int

    def lifted(self, size: int) -> int:
        return max(size, min(self.largest[1].size, self.source))

    def refit(self, cap: int, floor: int = MIN_SIZE) -> tuple[Region, Layout]:
        return fit_bubble(self.text, (self.largest[0], self.regions[1]), self.style, cap, floor, self.names)


def dialogue_sizes(pieces: list[Piece]) -> tuple[list[int], int, int]:
    target = page_target([min(p.largest[1].size, round(MAX_SIZE * (1 + BAND))) for p in pieces])
    floor = round(target * HYPHEN_FLOOR)
    base = [p.refit(target, floor) if p.largest[1].size > target or p.largest[1].size < floor else p.largest for p in pieces]
    page_size = round(np.median(same_sizes([layout.size for _, layout in base])))
    wanted = [wanted_size(p, region, layout, page_size) for p, (region, layout) in zip(pieces, base)]
    return same_sizes([p.lifted(size) for p, size in zip(pieces, wanted)]), page_size, floor


def typeset(lettering: Lettering, inpainted: np.ndarray, bubbles: list[Bubble]) -> tuple[np.ndarray, list[int]]:
    gray = cv2.cvtColor(inpainted, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    blocks = [b.block.box for b in bubbles]
    siblings = [tuple(o.block for o in bubbles if o is not b and b.balloon is not None and o.balloon is b.balloon) for b in bubbles]
    grown = [bubble_region(gray, b, others) for b, others in zip(bubbles, siblings)]
    candidates = separate(blocks, [(g, art_region(box, w, h, g.dark)) for g, box in zip(grown, blocks)])
    pieces: list[Piece | None] = []
    for bubble, (glyph, px), regions in zip(bubbles, glyph_sizes(bubbles), candidates):
        shout = glyph if is_shout(bubble, glyph, regions[0].burst) else None
        style = lettering.shout if shout else lettering.dialogue
        text = style.display(bubble.translation)
        source = min(SOURCE_CAP, round(px * SOURCE_SCALE))
        top = max(round(MAX_SIZE * (SHOUT_GLYPH_CAP if shout else 1 + BAND)), source)
        names = lining.names(bubble.translation)
        largest = fit_bubble(text, regions, style, top, names=names)
        pieces.append(Piece(text, names, style, regions, largest, shout, source) if text else None)

    dialogue = [p for p in pieces if p and not p.shout]
    sizes, page_size, floor = dialogue_sizes(dialogue) if dialogue else ([], MAX_SIZE, MIN_SIZE)
    chosen = dict(zip(map(id, dialogue), sizes))
    for p in pieces:
        if p and p.shout:
            chosen[id(p)] = p.lifted(min(p.largest[1].size, round(page_size * min(max(p.shout, SHOUT_SCALE), SHOUT_GLYPH_CAP))))

    page = Image.fromarray(inpainted)
    out = []
    for p in pieces:
        if p is None:
            out.append(0)
            continue
        region, layout = p.largest
        if layout.size != chosen[id(p)]:
            region, layout = p.refit(chosen[id(p)], MIN_SIZE if p.shout else floor)
        out.append(layout.size)
        ink, halo = ((255, 255, 255), (0, 0, 0)) if region.dark else ((0, 0, 0), (255, 255, 255))
        stroke = max(2, layout.size // 8) if region.on_art or region.busy else 0
        canvas = page.copy() if layout.clip else page
        for line, x, y in layout.lines:
            layout.style.face.draw(canvas, (x, y), line, layout.size, ink, stroke, halo)
        if layout.clip:
            box = region.box
            page.paste(canvas.crop((box.x0, box.y0, box.x1, box.y1)), (box.x0, box.y0), Image.fromarray(region.mask.astype(np.uint8) * 255))
    return np.asarray(page), out
