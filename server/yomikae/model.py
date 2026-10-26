from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Box:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    @property
    def center(self) -> tuple[int, int]:
        return (self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2

    def expand(self, pad: int, width: int, height: int) -> Box:
        return Box(max(0, self.x0 - pad), max(0, self.y0 - pad), min(width, self.x1 + pad), min(height, self.y1 + pad))

    def as_list(self) -> list[int]:
        return [self.x0, self.y0, self.x1, self.y1]


@dataclass(frozen=True)
class TextBlock:

    box: Box
    vertical: bool
    mask_box: Box
    mask: np.ndarray
    recovered: bool = False


@dataclass(frozen=True, eq=False)
class Balloon:
    box: Box
    mask: np.ndarray
    score: float


@dataclass(frozen=True)
class Bubble:
    block: TextBlock
    source: str
    translation: str = ""
    balloon: Balloon | None = None
    shared: bool = False
    ruby: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ContextLine:
    source: str
    translation: str


@dataclass(frozen=True)
class CastEntry:
    ja: str
    en: str
    reading: str = ""
    source: str = "auto"
    kind: str = "name"
