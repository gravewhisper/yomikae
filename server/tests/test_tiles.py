from __future__ import annotations

import numpy as np
import pytest

from make_test_page import make_page
from yomikae import balloon, detect
from yomikae.config import BUBBLE_MODEL_PATH, DETECTOR_PATH
from yomikae.model import Box
from yomikae.tiles import TILE_OVERLAP, tiles


@pytest.mark.parametrize("size", [(1280, 1816), (1816, 1280), (2560, 1816), (908, 1280), (800, 1600)])
def test_pages_and_spreads_are_read_whole(size):
    whole = Box(0, 0, *size)
    assert [(t.window, t.owned) for t in tiles(*size)] == [(whole, whole)]


@pytest.mark.parametrize("size", [(720, 20020), (800, 6000), (3840, 1816)])
def test_a_strip_is_covered_by_equal_overlapping_tiles_that_split_ownership(size):
    vertical = size[1] > size[0]
    short, long = min(size), max(size)
    cover = tiles(*size)
    along = [(t.window.y0, t.window.y1, t.owned.y0, t.owned.y1) if vertical else (t.window.x0, t.window.x1, t.owned.x0, t.owned.x1) for t in cover]
    assert len(cover) > 1 and len({w1 - w0 for w0, w1, _, _ in along}) == 1
    assert along[0][0] == along[0][2] == 0 and along[-1][1] == along[-1][3] == long
    margin = TILE_OVERLAP * short / 2 - 1
    for (w0, w1, o0, o1), (_, _, n0, _) in zip(along, along[1:]):
        assert o1 == n0 and w1 - o1 >= margin
    for w0, w1, o0, o1 in along[1:]:
        assert o0 - w0 >= margin


def test_a_normal_page_gets_the_same_model_outputs_as_one_whole_image_pass():
    image = np.asarray(make_page(3)[0])
    detector = detect.load_session(DETECTOR_PATH)
    padded, r, nw, nh = detect.letterbox(image)
    blk, seg, det = detector.run(None, {"images": padded.transpose(2, 0, 1)[None].astype(np.float32) / 255.0})
    got = detect.infer(detector, image)
    assert got[3:] == (r, nw, nh)
    for a, b in zip(got[:3], (blk[0], seg[0, 0, :nh, :nw], det[0, 0, :nh, :nw])):
        assert np.array_equal(a, b)
    segmenter = detect.load_session(BUBBLE_MODEL_PATH)
    whole = balloon.segment_window(segmenter, image)
    tiled = balloon.segment(segmenter, image)
    assert len(tiled) == len(whole) > 0
    for a, b in zip(tiled, whole):
        assert a.box == b.box and a.score == b.score and np.array_equal(a.mask, b.mask)
