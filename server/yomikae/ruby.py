from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

import cv2
import numpy as np
from PIL import Image

from .model import Box, Bubble, CastEntry, TextBlock

KANA = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゔ": "vu",
}
SMALL_Y = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
BIG_Y = {"ゃ": "や", "ゅ": "ゆ", "ょ": "よ"}
NO_GEMINATE = set("あいうえおなにぬねのまみむめもやゆよらりるれろわをんっゃゅょぁぃぅぇぉー")
NAME_SPAN = 3
INK = 150
RUBY_WIDTH = 0.62
RUBY_GAP = 0.7
BASE_MIN_WIDTH = 0.8
BIG_GLYPH = 0.6
JOIN_GAP = 0.5
GROUP_GAP = 1.0
MIN_GROUP = 0.6
BASE_REACH = 0.25
RUBY_SIDE = 48
RUBY_PAD = 8
BASE_PAD = 6
MIN_CONFIDENCE = 0.7
MAX_KANA_PER_KANJI = 4
KANA_ONLY = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30fc]+")
WORD = re.compile(r"[A-Za-z]+(?:-[a-z]+)?")
KANJI = re.compile(r"[㐀-鿿々]")
HONORIFIC = re.compile(r"-(?:san|kun|chan|sama|senpai|sensei|dono|tan|nee|nii|neesan|niisan|neechan|niichan)$")
VOWEL_RUNS = re.compile(r"([aeiou])\1+|ou")


def hiragana(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in text)


def romanize(reading: str) -> str:
    kana = hiragana(reading)
    out: list[str] = []
    double = False
    for ch in kana:
        if ch == "っ":
            double = True
            continue
        if ch in SMALL_Y and out and out[-1].endswith("i") and len(out[-1]) > 1:
            last = out.pop()
            stem = last[:-1] if last[:-1].endswith(("sh", "ch", "j")) else last[:-1] + "y"
            out.append(stem + SMALL_Y[ch])
            continue
        if ch == "ー" and out:
            out.append(out[-1][-1])
            continue
        roman = KANA.get(ch)
        if roman is None:
            continue
        if double:
            roman = ("t" if roman.startswith("ch") else roman[0]) + roman
            double = False
        out.append(roman)
    return "".join(out)


def sound(text: str) -> str:
    plain = re.sub(r"[^a-z]", "", text.lower())
    return VOWEL_RUNS.sub(lambda m: "o" if m.group(0) == "ou" else m.group(1), plain.replace("nb", "mb").replace("nm", "mm").replace("np", "mp"))


def spelled(reading: str, translation: str) -> str | None:
    target = sound(romanize(reading))
    if len(target) < 2:
        return None
    words = [m for m in WORD.finditer(translation)]
    for i, first in enumerate(words):
        if not first.group(0)[0].isupper():
            continue
        for j in range(i, min(i + NAME_SPAN, len(words))):
            span = words[i : j + 1]
            if not all(w.group(0)[0].isupper() for w in span):
                break
            text = translation[first.start() : span[-1].end()]
            name = HONORIFIC.sub("", text)
            if target in (sound(name), sound("".join(HONORIFIC.sub("", w.group(0)) for w in reversed(span)))):
                return name
    return None


def furigana_names(lines: list[str], translations: list[str], ruby: list[tuple[str, str]]) -> list[CastEntry]:
    found: dict[str, CastEntry] = {}
    for base, reading in ruby:
        if base in found or not KANJI.search(base):
            continue
        for line, text in zip(lines, translations):
            if base not in line or not text:
                continue
            name = spelled(reading, text)
            if name:
                found[base] = CastEntry(base, name, hiragana(reading), "furigana")
                break
    return list(found.values())


def tidy(reading: str) -> str:
    kana = list(hiragana(reading))
    for i, ch in enumerate(kana):
        after = kana[i + 1] if i + 1 < len(kana) else ""
        if ch == "っ" and (i == 0 or not after or after in NO_GEMINATE):
            kana[i] = "つ"
        elif ch in SMALL_Y and (i == 0 or not KANA.get(kana[i - 1], "").endswith("i")):
            kana[i] = BIG_Y[ch]
    return "".join(kana)


def runs(on: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.concatenate([[0], on.astype(int), [0]]))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


def find_ruby(gray: np.ndarray, block: TextBlock) -> list[tuple[Box, Box, int]]:
    if not block.vertical:
        return []
    mb = block.mask_box
    ink = (gray[mb.y0 : mb.y1, mb.x0 : mb.x1] < INK) & (cv2.dilate(block.mask, np.ones((3, 3), np.uint8)) > 0)
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    comps = stats[1:, :4].astype(int).tolist()
    if len(comps) < 3:
        return []
    sizes = [max(w, h) for _, _, w, h in comps]
    floor = BIG_GLYPH * np.percentile(sizes, 90)
    on = np.zeros(ink.shape[1], bool)
    for (x, _, w, _), size in zip(comps, sizes):
        if size >= floor:
            on[x : x + w] = True
    cores = runs(on)
    main = float(np.median([b - a for a, b in cores]))
    found = []
    for i, (c, d) in enumerate(cores):
        right = cores[i + 1][0] if i + 1 < len(cores) else ink.shape[1]
        small = [(x, y, w, h) for x, y, w, h in comps if max(w, h) <= RUBY_WIDTH * main and x >= d - 1 and x + w <= right + 1 and x + w / 2 - d <= RUBY_GAP * main]
        if d - c < BASE_MIN_WIDTH * main or not small:
            continue
        small.sort(key=lambda r: r[1])
        pitch = float(np.median([max(w, h) for _, _, w, h in small]))
        groups: list[list[int]] = []
        for x, y, w, h in small:
            if groups and y - groups[-1][3] < GROUP_GAP * pitch:
                g = groups[-1]
                groups[-1] = [min(g[0], x), g[1], max(g[2], x + w), max(g[3], y + h)]
            else:
                groups.append([x, y, x + w, y + h])
        reach = round(BASE_REACH * main)
        for x0, y0, x1, y1 in groups:
            if y1 - y0 < MIN_GROUP * pitch or x1 - x0 < 2:
                continue
            ruby = Box(mb.x0 + x0, mb.y0 + y0, mb.x0 + x1, mb.y0 + y1)
            found.append((ruby, Box(mb.x0 + c, max(0, ruby.y0 - reach), mb.x0 + d, ruby.y1 + reach), i))
    return found


def padded(crop: Image.Image, pad: int) -> Image.Image:
    out = Image.new("L", (crop.width + 2 * pad, crop.height + 2 * pad), 255)
    out.paste(crop.convert("L"), (pad, pad))
    return out


def kanji_in(text: str, source: str) -> str:
    best = ""
    for run in re.findall(r"[\u3400-\u9fff\u3005]+", text):
        for i in range(len(run)):
            for j in range(len(run), i + len(best), -1):
                if run[i:j] in source:
                    best = run[i:j]
                    break
    return best


def joined_base(first: str, second: str, source: str) -> str | None:
    for overlap in range(min(len(first), len(second)), -1, -1):
        if first.endswith(second[:overlap]) and first + second[overlap:] in source:
            return first + second[overlap:]
    return None


def read_ruby(ocr, page: Image.Image, gray: np.ndarray, block: TextBlock, source: str) -> list[tuple[str, str]]:
    from .ocr import recognize

    pairs: list[tuple[str, str, int, int]] = []
    for ruby, base, column in find_ruby(gray, block):
        crop = page.crop((ruby.x0 - 1, ruby.y0 - 1, ruby.x1 + 1, ruby.y1 + 1))
        scale = max(1, round(RUBY_SIDE / max(1, ruby.w)))
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
        reading, confidence, _ = recognize(ocr, padded(crop, RUBY_PAD * scale))
        if confidence < MIN_CONFIDENCE or not KANA_ONLY.fullmatch(reading):
            continue
        text, _, _ = recognize(ocr, padded(page.crop((base.x0 - 2, base.y0, base.x1 + 2, base.y1)), BASE_PAD))
        kanji = kanji_in(text, source)
        if not kanji:
            continue
        if pairs and pairs[-1][2] == column and ruby.y0 - pairs[-1][3] < JOIN_GAP * base.w:
            joined = joined_base(pairs[-1][0], kanji, source)
            if joined:
                kanji, reading = joined, pairs[-1][1] + reading
                pairs.pop()
        pairs.append((kanji, reading, column, ruby.y1))
    return [(k, tidy(r)) for k, r, _, _ in pairs if len(k) <= len(r) <= MAX_KANA_PER_KANJI * len(k)]


def attach(ocr, image: np.ndarray, bubbles: list[Bubble]) -> list[Bubble]:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    page = Image.fromarray(image)
    return [replace(b, ruby=tuple(read_ruby(ocr, page, gray, b.block, b.source))) for b in bubbles]
