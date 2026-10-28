from __future__ import annotations

import os
import re
import unicodedata

import cv2
import numpy as np
from PIL import Image

from .balloon import claims, unclaimed
from .detect import LIGHT, MASK_PAD_FRAC, reading_order
from .model import Balloon, Box, Bubble, TextBlock
from .typeset import find_region

OCR_PAD = 6
JAPANESE = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")
KATAKANA = re.compile(r"^[゠-ヿｦ-ﾟ]+$")
KANA = re.compile(r"^[぀-ヿｦ-ﾟ]+$")
LATIN = re.compile(r"[A-Za-z\uff21-\uff3a\uff41-\uff5a]")
SFX_ENDINGS = ("ッ", "ー", "ン")
MIN_CONFIDENCE = 0.6
MIN_GATED_TOKENS = 20
EDGE_LINE_RATIO = 1.5
OUTLINE = 2
INK = 128
MIN_INK = 20
STRICT_CONFIDENCE = 0.9
CREDIT_MARKS = frozenset({"作", "画", "絵", "文", "著", "作画", "原作", "漫画", "原案", "脚本", "構成", "作・画", "企画", "監修", "協力"})


def load_ocr(device: str = "cpu"):
    from manga_ocr import MangaOcr

    return MangaOcr(force_cpu=device == "cpu")


def recognize(ocr, crop: Image.Image) -> tuple[str, float, int]:
    import torch
    from manga_ocr.ocr import post_process

    x = ocr._preprocess(crop.convert("L").convert("RGB"))[None].to(ocr.model.device)
    with torch.inference_mode():
        ids = ocr.model.generate(x, max_length=300)
        logits = ocr.model(pixel_values=x, decoder_input_ids=ids[:, :-1]).logits[0].float()
    probs = torch.softmax(logits, -1).gather(1, ids[0, 1:, None])[:, 0]
    text = post_process(ocr.tokenizer.decode(ids[0], skip_special_tokens=True))
    return text, float(probs.mean()), len(probs)


def core_text(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in "LN" and ch not in "ー〜～")


def is_japanese_text(text: str) -> bool:
    core = core_text(text)
    if not core or not JAPANESE.search(core):
        return False
    return len(LATIN.findall(core)) <= len(JAPANESE.findall(core))


def looks_like_sfx(text: str) -> bool:
    core = core_text(text)
    if len(core) <= 2 and KANA.match(core):
        return True
    return bool(KATAKANA.match(core)) and (len(core) <= 3 or text.rstrip("!！?？…・ ").endswith(SFX_ENDINGS))


def in_bubble(gray: np.ndarray, block: TextBlock) -> bool:
    gray = gray.copy()
    mb = block.mask_box
    region = gray[mb.y0 : mb.y1, mb.x0 : mb.x1]
    ink = cv2.dilate(block.mask, np.ones((5, 5), np.uint8)) > 0
    if (~ink).any():
        region[ink] = np.median(region[~ink])
    return not find_region(gray, block).on_art


def text_pixels(block: TextBlock, window: Box) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.nonzero(block.mask)
    ys, xs = ys + block.mask_box.y0 - window.y0, xs + block.mask_box.x0 - window.x0
    keep = (ys >= 0) & (xs >= 0) & (ys < window.h) & (xs < window.w)
    return ys[keep], xs[keep]


def shares_bubble(gray: np.ndarray, block: TextBlock, others: list[TextBlock]) -> bool:
    h, w = gray.shape
    near = block.box.expand(2 * max(block.box.w, block.box.h), w, h)
    for other in others:
        o = other.box
        if o.x1 <= near.x0 or o.x0 >= near.x1 or o.y1 <= near.y0 or o.y0 >= near.y1:
            continue
        both = Box(min(block.box.x0, o.x0), min(block.box.y0, o.y0), max(block.box.x1, o.x1), max(block.box.y1, o.y1))
        window = both.expand(max(both.w, both.h) // 2, w, h)
        light = np.where(gray[window.y0 : window.y1, window.x0 : window.x1] >= LIGHT, 255, 0).astype(np.uint8)
        mine, theirs = text_pixels(block, window), text_pixels(other, window)
        if not len(mine[0]) or not len(theirs[0]):
            continue
        light[mine] = light[theirs] = 255
        _, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.erode(light, np.ones((3, 3), np.uint8)), connectivity=4)
        a, b = np.bincount(labels[mine]).argmax(), np.bincount(labels[theirs]).argmax()
        if not a or a != b:
            continue
        x, y, cw, ch, _ = stats[a]
        leaks = (x == 0 and window.x0 > 0) or (y == 0 and window.y0 > 0) or (x + cw == window.w and window.x1 < w) or (y + ch == window.h and window.y1 < h)
        if not leaks:
            return True
    return False


def line_bands(mask: np.ndarray, vertical: bool) -> list[tuple[int, int]]:
    profile = mask.sum(0) if vertical else mask.sum(1)
    on = np.concatenate([[0], (profile > 0.1 * profile.max()).astype(int), [0]])
    return list(zip(np.flatnonzero(np.diff(on) == 1), np.flatnonzero(np.diff(on) == -1)))


def without_foreign_edge_line(ocr, page: Image.Image, block: TextBlock) -> TextBlock:
    mb = block.mask_box
    mask = block.mask > 0
    bands = line_bands(mask, block.vertical)
    if len(bands) < 3:
        return block
    for band in (bands[0], bands[-1]):
        others = [b1 - b0 for b0, b1 in bands if (b0, b1) != band]
        if band[1] - band[0] < EDGE_LINE_RATIO * float(np.median(others)):
            continue
        a0, a1 = band
        if block.vertical:
            crop = Box(mb.x0 + a0, block.box.y0, mb.x0 + a1, block.box.y1)
        else:
            crop = Box(block.box.x0, mb.y0 + a0, block.box.x1, mb.y0 + a1)
        crop = crop.expand(OCR_PAD, page.width, page.height)
        text, _, _ = recognize(ocr, page.crop((crop.x0, crop.y0, crop.x1, crop.y1)))
        if is_japanese_text(text) or not LATIN.search(text):
            continue
        kept = mask.copy()
        cut = slice(None, bands[1][0]) if band == bands[0] else slice(bands[-2][1], None)
        if block.vertical:
            kept[:, cut] = False
        else:
            kept[cut] = False
        ys, xs = np.nonzero(kept)
        box = Box(int(xs.min()) + mb.x0, int(ys.min()) + mb.y0, int(xs.max()) + 1 + mb.x0, int(ys.max()) + 1 + mb.y0)
        return TextBlock(box=box, vertical=block.vertical, mask_box=mb, mask=np.where(kept, block.mask, 0).astype(np.uint8), recovered=block.recovered)
    return block


def balloon_text(gray: np.ndarray, balloon: Balloon) -> TextBlock | None:
    b = balloon.box
    h, w = gray.shape
    interior = cv2.erode(np.pad(balloon.mask, 1).astype(np.uint8), np.ones((2 * OUTLINE + 1, 2 * OUTLINE + 1), np.uint8))[1:-1, 1:-1] > 0
    sub = gray[b.y0 : b.y1, b.x0 : b.x1]
    if not interior.any() or np.median(sub[interior]) < LIGHT:
        return None
    ink = interior & (sub < INK)
    if ink.sum() < MIN_INK:
        return None
    ys, xs = np.nonzero(ink)
    box = Box(b.x0 + int(xs.min()), b.y0 + int(ys.min()), b.x0 + int(xs.max()) + 1, b.y0 + int(ys.max()) + 1)
    mask_box = box.expand(int(min(box.w, box.h) * MASK_PAD_FRAC) + 4, w, h)
    full = np.zeros((h, w), np.uint8)
    full[b.y0 : b.y1, b.x0 : b.x1] = ink * 255
    return TextBlock(box=box, vertical=box.h >= box.w, mask_box=mask_box, mask=full[mask_box.y0 : mask_box.y1, mask_box.x0 : mask_box.x1].copy())


def read(ocr, image: np.ndarray, blocks: list[TextBlock], balloons: list[Balloon] = ()) -> tuple[list[Bubble], list[Bubble]]:
    h, w = image.shape[:2]
    page = Image.fromarray(image)
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    picks = claims(blocks, balloons)
    found = [(block, balloons[pick] if pick is not None else None, pick is not None and picks.count(pick) > 1, False) for block, pick in zip(blocks, picks)]
    spotting = os.environ.get("YOMIKAE_BALLOON_DETECT") == "1"
    for extra in unclaimed(blocks, balloons) if spotting else ():
        block = balloon_text(gray, extra)
        if block is not None:
            found.append((block, extra, False, True))
    order = {id(b): i for i, b in enumerate(reading_order([f[0] for f in found]))}
    bubbles, unsure = [], []
    for raw, home, shared, spotted in sorted(found, key=lambda f: order[id(f[0])]):
        block = without_foreign_edge_line(ocr, page, raw)
        crop = block.box.expand(OCR_PAD, w, h)
        text, confidence, tokens = recognize(ocr, page.crop((crop.x0, crop.y0, crop.x1, crop.y1)))
        text = text.strip()
        enclosed = home is not None
        if not is_japanese_text(text) or (looks_like_sfx(text) and not enclosed and not in_bubble(gray, block) and not shares_bubble(gray, block, [b for b in blocks if b is not raw])):
            continue
        if not enclosed and unicodedata.normalize("NFKC", text) in CREDIT_MARKS:
            continue
        if raw.recovered and not enclosed and (confidence < STRICT_CONFIDENCE or looks_like_sfx(text)):
            continue
        if spotted and (confidence < MIN_CONFIDENCE or (KATAKANA.match(core_text(text)) and looks_like_sfx(text))):
            continue
        if tokens >= MIN_GATED_TOKENS and confidence < MIN_CONFIDENCE:
            unsure.append(Bubble(block=block, source=text, balloon=home, shared=shared))
        else:
            bubbles.append(Bubble(block=block, source=text, balloon=home, shared=shared))
    return bubbles, unsure
