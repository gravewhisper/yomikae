from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

KINDS = ("ellipse", "tailed", "merged", "rounded", "rect", "caption", "notched", "angular", "wobbly", "cloud", "deep cloud", "burst", "tailed burst", "jagged box")
SHOUTS = {"burst", "tailed burst", "jagged box"}


def canvas(w: int, h: int) -> tuple[np.ndarray, tuple[int, int]]:
    pad = round(0.7 * max(w, h))
    return np.zeros((h + 2 * pad, w + 2 * pad), np.uint8), (w // 2 + pad, h // 2 + pad)


def polygon(mask: np.ndarray, points: np.ndarray) -> np.ndarray:
    cv2.fillPoly(mask, [np.round(points).astype(np.int32)], 255)
    return mask


def radial(center: tuple[int, int], radii: np.ndarray, angles: np.ndarray, ax: float, ay: float) -> np.ndarray:
    return np.stack([center[0] + ax * radii * np.cos(angles), center[1] + ay * radii * np.sin(angles)], 1)


def tail(mask: np.ndarray, center: tuple[int, int], ax: float, ay: float, rng: np.random.Generator) -> np.ndarray:
    a = rng.uniform(0, 2 * np.pi)
    base = 0.25 * min(ax, ay)
    tip = radial(center, np.array([rng.uniform(1.25, 1.6)]), np.array([a]), ax, ay)[0]
    root = np.array(center, float)
    normal = np.array([-np.sin(a), np.cos(a)]) * base
    return polygon(mask, np.array([root + normal, tip + rng.normal(0, 4, 2), root - normal]))


def make(kind: str, rng: np.random.Generator) -> np.ndarray:
    w, h = int(rng.uniform(160, 420)), int(rng.uniform(140, 360))
    mask, c = canvas(w, h)
    ax, ay = w / 2, h / 2
    if kind in ("ellipse", "tailed"):
        cv2.ellipse(mask, c, (round(ax), round(ay)), 0, 0, 360, 255, -1)
        if kind == "tailed":
            tail(mask, c, ax, ay, rng)
    elif kind in ("rounded", "rect", "caption"):
        if kind == "caption":
            h = int(rng.uniform(40, 90))
            mask, c = canvas(w, h)
            ay = h / 2
        r = int(rng.uniform(0.15, 0.45) * min(w, h)) if kind == "rounded" else 0
        x0, y0, x1, y1 = c[0] - round(ax), c[1] - round(ay), c[0] + round(ax), c[1] + round(ay)
        cv2.rectangle(mask, (x0 + r, y0), (x1 - r, y1), 255, -1)
        cv2.rectangle(mask, (x0, y0 + r), (x1, y1 - r), 255, -1)
        for x, y in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
            cv2.circle(mask, (x, y), r, 255, -1)
    elif kind == "notched":
        x0, y0, x1, y1 = c[0] - round(ax), c[1] - round(ay), c[0] + round(ax), c[1] + round(ay)
        cv2.rectangle(mask, (x0, y0), (x1, y1), 255, -1)
        r = round(rng.uniform(0.04, 0.08) * min(w, h))
        for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            cv2.circle(mask, (x, y), r, 0, -1)
    elif kind == "angular":
        n = int(rng.integers(5, 10))
        t = rng.uniform(0, 2 * np.pi) + np.linspace(0, 2 * np.pi, n, endpoint=False) + rng.normal(0, 0.3 * np.pi / n, n)
        polygon(mask, radial(c, rng.uniform(0.9, 1.1, n), t, ax, ay))
    elif kind == "jagged box":
        corners = np.array([(-1, -1), (1, -1), (1, 1), (-1, 1)], float) * (ax, ay) + c + rng.normal(0, 0.03 * min(w, h), (4, 2))
        points = []
        for i in range(4):
            a, b = corners[i], corners[(i + 1) % 4]
            points.append(a)
            for f in np.sort(rng.uniform(0.15, 0.85, int(rng.integers(1, 3)))):
                edge = b - a
                normal = np.array([edge[1], -edge[0]]) / np.linalg.norm(edge)
                half = rng.uniform(0.03, 0.06) * min(w, h)
                along = edge / np.linalg.norm(edge)
                base = a + f * edge
                points += [base - half * along, base + rng.uniform(0.06, 0.12) * min(w, h) * normal, base + half * along]
        polygon(mask, np.array(points))
    elif kind == "wobbly":
        t = np.linspace(0, 2 * np.pi, 720, endpoint=False)
        r = np.ones_like(t)
        for k in range(2, 7):
            r += rng.uniform(0.01, 0.045) * np.sin(k * t + rng.uniform(0, 2 * np.pi))
        r += np.convolve(rng.normal(0, 0.012, t.size), np.ones(9) / 9, "same")
        polygon(mask, radial(c, r, t, ax, ay))
    elif kind == "merged":
        for _ in range(int(rng.integers(2, 4))):
            dx, dy = rng.uniform(-0.45, 0.45, 2)
            cv2.ellipse(mask, (round(c[0] + dx * ax), round(c[1] + dy * ay)), (round(ax * rng.uniform(0.4, 0.6)), round(ay * rng.uniform(0.4, 0.6))), 0, 0, 360, 255, -1)
    elif kind in ("cloud", "deep cloud"):
        n = int(rng.integers(5, 19))
        ring, size = (0.82, (0.55, 0.8)) if kind == "cloud" else (0.75, (0.45, 0.6))
        cv2.ellipse(mask, c, (round(ax * ring), round(ay * ring)), 0, 0, 360, 255, -1)
        angles = np.sort(rng.uniform(0, 2 * np.pi) + np.linspace(0, 2 * np.pi, n, endpoint=False) + rng.normal(0, 0.08, n))
        spacing = np.pi * (ax + ay) / n
        for a in angles:
            x, y = radial(c, np.array([ring]), np.array([a]), ax, ay)[0]
            r = spacing * rng.uniform(*size)
            cv2.ellipse(mask, (round(x), round(y)), (round(r), round(r * rng.uniform(0.7, 1.3))), np.degrees(a), 0, 360, 255, -1)
    else:
        n = int(rng.integers(6, 33))
        depth = rng.uniform(0.2, 0.5)
        t = rng.uniform(0, 2 * np.pi) + np.linspace(0, 2 * np.pi, 2 * n, endpoint=False)
        t += rng.normal(0, 0.25 * np.pi / n, t.size)
        r = np.where(np.arange(2 * n) % 2 == 0, rng.uniform(0.85, 1.15, 2 * n), 1.0 - depth * rng.uniform(0.8, 1.2, 2 * n))
        polygon(mask, radial(c, r, t, ax, ay))
        if kind == "tailed burst":
            tail(mask, c, ax, ay, rng)
    return mask > 0


def segmenter_view(mask: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    stride = int(rng.integers(2, 9))
    h, w = mask.shape
    small = cv2.resize(mask.astype(np.float32), (max(1, w // stride), max(1, h // stride)), interpolation=cv2.INTER_AREA)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR) > 0.5


def outline_view(mask: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    k = int(rng.integers(2, 7))
    return cv2.erode(mask.astype(np.uint8), np.ones((2 * k + 1, 2 * k + 1), np.uint8)) > 0


def labeled(seed: int = 0, per_kind: int = 40) -> list[tuple[str, str, np.ndarray]]:
    rng = np.random.default_rng(seed)
    out = []
    for kind in KINDS:
        for i in range(per_kind):
            shape = make(kind, rng)
            view, mask = ("segmenter", segmenter_view(shape, rng)) if i % 2 else ("flood fill", outline_view(shape, rng))
            out.append((kind, view, mask))
    return out


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from yomikae.typeset import BURST, points, pointed, spikes

    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    rows = [(kind, spikes(mask), *points(mask), pointed(mask)) for seed in range(seeds) for kind, _, mask in labeled(seed)]
    print(f"{'kind':<14}{'n':>5}{'lobes':>8}{'tips':>7}{'notches':>9}{'called burst':>14}")
    for kind in KINDS:
        mine = np.array([row[1:] for row in rows if row[0] == kind])
        print(f"{kind:<14}{len(mine):>5}{np.median(mine[:, 0]):>8.1f}{np.median(mine[:, 1]):>7.1f}{np.median(mine[:, 2]):>9.1f}{int(mine[:, 3].sum()):>14}")
    shouts = [row[4] for row in rows if row[0] in SHOUTS]
    others = [row[4] for row in rows if row[0] not in SHOUTS]
    print(f"called burst: {sum(shouts)} of {len(shouts)} bursts, {sum(others)} of {len(others)} others (lobes >= {BURST}, or sharp tips without notches)")


if __name__ == "__main__":
    main()
