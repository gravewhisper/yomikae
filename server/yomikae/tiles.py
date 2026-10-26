from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import Box

TILE_ABOVE = 2.0
TILE_ASPECT = 1.5
TILE_OVERLAP = 0.5


@dataclass(frozen=True)
class Tile:
    window: Box
    owned: Box


def tiles(width: int, height: int) -> list[Tile]:
    short, long = min(width, height), max(width, height)
    if long <= TILE_ABOVE * short:
        whole = Box(0, 0, width, height)
        return [Tile(whole, whole)]
    size = round(TILE_ASPECT * short)
    overlap = round(TILE_OVERLAP * short)
    n = -(-(long - overlap) // (size - overlap))
    starts = [round(k * (long - size) / (n - 1)) for k in range(n)]
    cuts = [0] + [(a + size + b) // 2 for a, b in zip(starts, starts[1:])] + [long]

    def span(a: int, b: int) -> Box:
        return Box(0, a, width, b) if height > width else Box(a, 0, b, height)

    return [Tile(span(s, s + size), span(c0, c1)) for s, c0, c1 in zip(starts, cuts, cuts[1:])]


def owns(tile: Tile, x: np.ndarray, y: np.ndarray, width: int, height: int) -> np.ndarray:
    o = tile.owned
    return ((x >= o.x0) | (o.x0 == 0)) & ((x < o.x1) | (o.x1 == width)) & ((y >= o.y0) | (o.y0 == 0)) & ((y < o.y1) | (o.y1 == height))
