from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .config import INPUT_FORMATS

MARK_COLOR = (255, 0, 200)


class BadReport(Exception):
    pass


def image_size(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(data), formats=tuple(INPUT_FORMATS)) as im:
            return im.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None


def parse_areas(raw: str) -> list[dict]:
    try:
        areas = json.loads(raw)
        parsed = []
        for a in areas:
            box = [float(v) for v in a["box"]]
            index = a.get("bubble_index")
            if len(box) != 4 or not (index is None or isinstance(index, int)):
                raise ValueError(a)
            parsed.append({"box": box, "bubble_index": index})
        return parsed
    except (ValueError, TypeError, KeyError) as e:
        raise BadReport(f"areas: expected [{{box: [x0, y0, x1, y1] as fractions, bubble_index}}], got {raw[:200]!r}") from e


def resolve_areas(areas: list[dict], size: tuple[int, int] | None, bubbles: list[dict]) -> list[dict]:
    out = []
    for a in areas:
        index = a["bubble_index"]
        if index is not None and 0 <= index < len(bubbles):
            out.append({"box": list(bubbles[index]["box"]), "bubble_index": index})
            continue
        x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in a["box"])
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        box = [x0, y0, x1, y1] if size is None else [round(x0 * size[0]), round(y0 * size[1]), round(x1 * size[0]), round(y1 * size[1])]
        out.append({"box": box, "bubble_index": None})
    return out


def render_marked(original: Image.Image, areas: list[dict]) -> bytes:
    img = original.convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    line = max(3, round(max(w, h) / 400))
    font = ImageFont.load_default(size=max(14, h // 55))
    for n, a in enumerate(areas):
        x0, y0, x1, y1 = a["box"]
        draw.rectangle((x0, y0, x1, y1), outline=MARK_COLOR, width=line)
        label = f"bubble {a['bubble_index']}" if a["bubble_index"] is not None else f"area {n}"
        l, t, r, b = draw.textbbox((0, 0), label, font=font)
        pad = line
        ty = y0 - (b - t) - 2 * pad if y0 - (b - t) - 2 * pad >= 0 else y1
        draw.rectangle((x0, ty, x0 + (r - l) + 2 * pad, ty + (b - t) + 2 * pad), fill=MARK_COLOR)
        draw.text((x0 + pad - l, ty + pad - t), label, font=font, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def image_extension(data: bytes) -> str:
    try:
        with Image.open(io.BytesIO(data), formats=tuple(INPUT_FORMATS)) as im:
            fmt = im.format or ""
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        fmt = ""
    if fmt:
        return INPUT_FORMATS.get(fmt, fmt.lower())
    return "svg" if b"<svg" in data[:2048].lower() else "bin"


def new_report_id(page_id: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%S')}.{now.microsecond // 1000:03d}Z-{page_id[:12]}"


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode()


def write_report(root: Path, report_id: str, files: dict[str, bytes]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=root, prefix=f".{report_id}."))
    try:
        for name, data in files.items():
            (tmp / name).write_bytes(data)
        final = root / report_id
        os.rename(tmp, final)
        return final
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
