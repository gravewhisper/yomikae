from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

CJK_FONT_PATHS = (
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/google-noto-sans-cjk-fonts/NotoSansCJK-Bold.ttc",
)
JP_FONT_INDEX = 0
KR_FONT_INDEX = 1


def find_cjk_font(env=os.environ) -> str:
    if env.get("YOMIKAE_TEST_CJK_FONT"):
        return env["YOMIKAE_TEST_CJK_FONT"]
    for path in CJK_FONT_PATHS:
        if Path(path).exists():
            return path
    try:
        found = subprocess.run(["fc-match", "-f", "%{file}", "Noto Sans CJK JP:bold"], capture_output=True, text=True, check=False).stdout
    except FileNotFoundError:
        found = ""
    if Path(found).name == "NotoSansCJK-Bold.ttc":
        return found
    raise FileNotFoundError("synthetic test pages need NotoSansCJK-Bold.ttc: install Noto Sans CJK (fonts-noto-cjk on Debian and Ubuntu) or set YOMIKAE_TEST_CJK_FONT")


FONT_PATH = find_cjk_font()

LINES = [
    "今日はいい天気だね",
    "早く帰ろうよ",
    "それは本当なの",
    "僕にまかせて",
    "どこへ行くつもり",
    "もう一度だけ言って",
    "約束したでしょう",
    "あの店で待ってる",
    "君の名前を教えて",
    "絶対に負けない",
    "ちょっと待ってくれ",
    "明日また会おうね",
]

KOREAN_LINES = [
    "오늘 날씨 정말 좋다",
    "빨리 집에 가자",
    "그게 정말이야?",
    "나한테 맡겨",
    "어디 가는 거야?",
    "한 번만 더 말해 줘",
    "약속했잖아",
    "내일 또 만나자",
    "절대 지지 않을 거야",
]


@dataclass
class GroundTruthBubble:
    ellipse: list[int]
    text_box: list[int]
    text: str


def draw_screentone(img: Image.Image, box: tuple[int, int, int, int], rng: random.Random) -> None:
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    step = rng.choice([6, 8, 10])
    r = step // 4
    for y in range(y0, y1, step):
        offset = (step // 2) if ((y - y0) // step) % 2 else 0
        for x in range(x0 + offset, x1, step):
            draw.ellipse((x - r, y - r, x + r, y + r), fill=(90, 90, 90))


def draw_line_art(img: Image.Image, box: tuple[int, int, int, int], rng: random.Random) -> None:
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    for _ in range(14):
        pts = [(rng.randint(x0, x1), rng.randint(y0, y1)) for _ in range(3)]
        draw.line(pts, fill=(20, 20, 20), width=rng.choice([2, 3, 4]))


def vertical_text_size(text: str, font: ImageFont.FreeTypeFont, per_col: int) -> tuple[int, int, list[str]]:
    cols = [text[i : i + per_col] for i in range(0, len(text), per_col)]
    size = font.size
    col_w = int(size * 1.25)
    return col_w * len(cols), int(size * 1.1) * max(len(c) for c in cols), cols


def draw_vertical_text(
    img: Image.Image, origin: tuple[int, int], cols: list[str], font: ImageFont.FreeTypeFont, ruby: dict[int, list[tuple[int, int, str]]] | None = None
) -> None:
    draw = ImageDraw.Draw(img)
    size = font.size
    col_w = int(size * (1.7 if ruby else 1.25))
    pitch = int(size * 1.1)
    small = ImageFont.truetype(FONT_PATH, max(8, int(size * 0.42)), index=JP_FONT_INDEX)
    x_right = origin[0] + col_w * len(cols)
    for ci, col in enumerate(cols):
        cx = x_right - col_w * ci - col_w // 2
        for ri, ch in enumerate(col):
            cy = origin[1] + pitch * ri + size // 2
            draw.text((cx, cy), ch, font=font, fill=(0, 0, 0), anchor="mm")
        for start, length, kana in (ruby or {}).get(ci, []):
            mid = origin[1] + pitch * start + pitch * length / 2
            for k, ch in enumerate(kana):
                draw.text((cx + int(size * 0.8), mid + (k - (len(kana) - 1) / 2) * small.size * 1.05), ch, font=small, fill=(0, 0, 0), anchor="mm")


def draw_horizontal_text(
    img: Image.Image, origin: tuple[int, int], lines: list[str], font: ImageFont.FreeTypeFont, ruby: dict[int, list[tuple[int, int, str]]] | None = None
) -> None:
    draw = ImageDraw.Draw(img)
    size = font.size
    pitch = int(size * 1.05)
    line_h = int(size * 1.6)
    small = ImageFont.truetype(FONT_PATH, max(8, int(size * 0.42)), index=JP_FONT_INDEX)
    for li, line in enumerate(lines):
        cy = origin[1] + line_h * li + size // 2
        for ci, ch in enumerate(line):
            draw.text((origin[0] + pitch * ci + size // 2, cy), ch, font=font, fill=(0, 0, 0), anchor="mm")
        for start, length, kana in (ruby or {}).get(li, []):
            mid = origin[0] + pitch * start + pitch * length / 2
            for k, ch in enumerate(kana):
                draw.text((mid + (k - (len(kana) - 1) / 2) * small.size * 1.05, cy - int(size * 0.78)), ch, font=small, fill=(0, 0, 0), anchor="mm")


def make_page(seed: int, width: int = 1280, height: int = 1816) -> tuple[Image.Image, list[GroundTruthBubble]]:
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    margin = 40
    gutter = 24
    rows = 3
    row_h = (height - 2 * margin - gutter * (rows - 1)) // rows
    panels = []
    for r in range(rows):
        y0 = margin + r * (row_h + gutter)
        split = rng.randint(width // 3, 2 * width // 3)
        panels.append((margin, y0, split - gutter // 2, y0 + row_h))
        panels.append((split + gutter // 2, y0, width - margin, y0 + row_h))

    for p in panels:
        kind = rng.random()
        if kind < 0.4:
            draw_screentone(img, p, rng)
        elif kind < 0.8:
            draw_line_art(img, p, rng)
        draw.rectangle(p, outline=(0, 0, 0), width=5)

    lines = rng.sample(LINES, k=len(LINES))
    bubbles: list[GroundTruthBubble] = []
    for p in rng.sample(panels, k=4):
        text = lines.pop()
        font = ImageFont.truetype(FONT_PATH, rng.choice([30, 34, 38]), index=JP_FONT_INDEX)
        per_col = rng.choice([4, 5, 6])
        tw, th, cols = vertical_text_size(text, font, per_col)
        pad_x, pad_y = int(tw * 0.55) + 30, int(th * 0.3) + 30
        ew, eh = tw + 2 * pad_x, th + 2 * pad_y
        px0, py0, px1, py1 = p
        if ew > px1 - px0 - 20 or eh > py1 - py0 - 20:
            continue
        ex = rng.randint(px0 + 10, px1 - 10 - ew)
        ey = rng.randint(py0 + 10, py1 - 10 - eh)
        ellipse = (ex, ey, ex + ew, ey + eh)
        draw.ellipse(ellipse, fill=(255, 255, 255), outline=(0, 0, 0), width=4)
        tx, ty = ex + pad_x, ey + pad_y
        draw_vertical_text(img, (tx, ty), cols, font)
        ink = np.array(img.crop((tx - 10, ty - 10, tx + tw + 10, ty + th + 10)).convert("L")) < 128
        ys, xs = np.nonzero(ink)
        text_box = [int(tx - 10 + xs.min()), int(ty - 10 + ys.min()), int(tx - 10 + xs.max() + 1), int(ty - 10 + ys.max() + 1)]
        bubbles.append(GroundTruthBubble(list(ellipse), text_box, text))
    return img, bubbles


def make_korean_page(seed: int, width: int = 800, height: int = 1800) -> tuple[Image.Image, list[GroundTruthBubble]]:
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height), (255, 255, 255))
    for y0 in range(60, height - 400, 440):
        if rng.random() < 0.5:
            draw_line_art(img, (60, y0, width - 60, y0 + 380), rng)
    draw = ImageDraw.Draw(img)
    bubbles: list[GroundTruthBubble] = []
    for i, line in enumerate(rng.sample(KOREAN_LINES, k=3)):
        font = ImageFont.truetype(FONT_PATH, rng.choice([30, 34]), index=KR_FONT_INDEX)
        words = line.split()
        rows = [" ".join(words[: len(words) // 2 + 1]), " ".join(words[len(words) // 2 + 1 :])] if len(words) > 2 else [line]
        boxes = [draw.textbbox((0, 0), row, font=font) for row in rows]
        tw, row_h = max(b[2] - b[0] for b in boxes), int(font.size * 1.4)
        th = row_h * len(rows)
        ew, eh = tw + 120, th + 90
        ex = rng.randint(70, width - 70 - ew)
        ey = 100 + i * 560 + rng.randint(0, 80)
        ellipse = (ex, ey, ex + ew, ey + eh)
        draw.ellipse(ellipse, fill=(255, 255, 255), outline=(0, 0, 0), width=4)
        for r, row in enumerate(rows):
            draw.text((ex + ew // 2, ey + 45 + row_h * r + row_h // 2), row, font=font, fill=(0, 0, 0), anchor="mm")
        ink = np.array(img.crop(ellipse).convert("L")) < 128
        ink[:8], ink[-8:], ink[:, :8], ink[:, -8:] = False, False, False, False
        ys, xs = np.nonzero(ink)
        bubbles.append(GroundTruthBubble(list(ellipse), [ex + int(xs.min()), ey + int(ys.min()), ex + int(xs.max()) + 1, ey + int(ys.max()) + 1], line))
    return img, bubbles


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--count", type=int, default=1)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for seed in range(args.seed, args.seed + args.count):
        img, bubbles = make_page(seed)
        img.save(args.out_dir / f"page_{seed}.png")
        (args.out_dir / f"page_{seed}.json").write_text(
            json.dumps([asdict(b) for b in bubbles], ensure_ascii=False, indent=1)
        )
        print(args.out_dir / f"page_{seed}.png", len(bubbles), "bubbles")


if __name__ == "__main__":
    main()
