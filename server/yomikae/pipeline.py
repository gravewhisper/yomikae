from __future__ import annotations

import asyncio
import hashlib
import io
import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import cached_property
from typing import TypeVar

import numpy as np
from PIL import Image, UnidentifiedImageError

from . import balloon, detect, inpaint, ocr, ruby, typeset
from .cache import ContextStore, PageCache
from .config import BUBBLE_MODEL_PATH, DETECTOR_PATH, INPUT_FORMATS, Settings
from .model import Box, Bubble, CastEntry, ContextLine
from .ruby import furigana_names
from .translate import Context, PageImage, Translator, TranslatorUnavailable, make_translator, mend_stutters, subset, translate_lines

T = TypeVar("T")
PIPELINE_VERSION = 31
STAGES = ("detect", "balloons", "ocr", "translate", "inpaint", "typeset")
WEBP_QUALITY = 90
JPEG_QUALITY = 90
MATCH_IOU = 0.5
MAX_PIXELS = 40_000_000


@dataclass(frozen=True)
class Format:
    ext: str
    media_type: str
    max_side: int
    options: dict


FORMATS = (
    Format("webp", "image/webp", 16383, {"format": "WEBP", "quality": WEBP_QUALITY, "method": 1}),
    Format("jpg", "image/jpeg", 65535, {"format": "JPEG", "quality": JPEG_QUALITY}),
    Format("png", "image/png", 2**31 - 1, {"format": "PNG", "compress_level": 1}),
)


class BadImage(Exception):
    pass


def decode_image(data: bytes) -> np.ndarray:
    try:
        with Image.open(io.BytesIO(data), formats=tuple(INPUT_FORMATS)) as im:
            if im.width * im.height > MAX_PIXELS:
                raise BadImage(f"image is {im.width}x{im.height} pixels, more than the limit of {MAX_PIXELS}")
            im.load()
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im)
            return np.asarray(im.convert("RGB"))
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise BadImage(str(e) or "could not decode image") from e
    except Image.DecompressionBombError as e:
        raise BadImage(f"image has more pixels than the limit of {MAX_PIXELS}: {e}") from e


def encode(image: np.ndarray) -> tuple[str, bytes]:
    fmt = next(f for f in FORMATS if max(image.shape[:2]) <= f.max_side)
    buf = io.BytesIO()
    Image.fromarray(image).save(buf, **fmt.options)
    return f"rendered.{fmt.ext}", buf.getvalue()


def iou(a: Box, b: Box) -> float:
    ix = max(0, min(a.x1, b.x1) - max(a.x0, b.x0))
    iy = max(0, min(a.y1, b.y1) - max(a.y0, b.y0))
    inter = ix * iy
    return inter / max(1, a.w * a.h + b.w * b.h - inter)


def match_bubbles(old: list[dict], new: list[Bubble]) -> list[int | None]:
    taken: set[int] = set()
    out: list[int | None] = []
    for b in new:
        scored = [(o["source"] == b.source, iou(Box(*o["box"]), b.block.box), j) for j, o in enumerate(old) if j not in taken]
        best = max((c for c in scored if c[1] >= MATCH_IOU), default=None)
        out.append(best[2] if best else None)
        if best:
            taken.add(best[2])
    return out


def names_in_force(cast: list[CastEntry]) -> list[dict]:
    return [{"ja": e.ja, "en": e.en, **({"was": list(e.was)} if e.was else {})} for e in cast if e.source != "auto"]


def spells(text: str, name: str) -> bool:
    return re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text, re.I) is not None


def stale_bubbles(result: dict, current: list[dict]) -> list[int]:
    recorded = result.get("names_in_force")
    if recorded is None or not result.get("context_key"):
        return []
    before = {n["ja"] or n["en"].lower(): n for n in recorded}
    now = {n["ja"] or n["en"].lower(): n for n in current}
    changed = []
    for k, n in {**before, **now}.items():
        old, new = before.get(k, {}).get("en"), now.get(k, {}).get("en")
        if old != new:
            olds = [s for s in (old, *now.get(k, {}).get("was", ())) if s and s.lower() != (new or "").lower()]
            changed.append((n["ja"], olds, new))
    return [
        i
        for i, b in enumerate(result["bubbles"])
        if any(
            any(spells(b["translation"], old) for old in olds) or (ja and new and ja in b["source"] and not spells(b["translation"], new))
            for ja, olds, new in changed
        )
    ]


def keep_original(image: np.ndarray, inpainted: np.ndarray, bubbles: list[Bubble]) -> np.ndarray:
    _, mask = inpaint.text_mask(image, bubbles)
    return np.where(mask[..., None] > 0, image, inpainted)


class Timer:
    def __init__(self):
        self.ms: dict[str, int] = {s: 0 for s in STAGES}

    def run(self, stage: str, fn: Callable[..., T], *args) -> T:
        t = time.perf_counter()
        try:
            return fn(*args)
        finally:
            self.ms[stage] = self.ms.get(stage, 0) + round((time.perf_counter() - t) * 1000)


class Engine:
    def __init__(self, settings: Settings):
        import torch

        self.settings = settings
        self.device = "cuda" if torch.cuda.is_available() and not settings.low_vram else "cpu"
        self.gpu_name = torch.cuda.get_device_name(0) if self.device == "cuda" else "cpu"
        self.gpu_lock = threading.Lock()
        self.gpu_thread = ThreadPoolExecutor(1, thread_name_prefix="gpu")
        detect_device = settings.detect_device if self.device == "cuda" else "cpu"
        self.detector = detect.load_session(DETECTOR_PATH, detect_device)
        self.balloons = detect.load_session(BUBBLE_MODEL_PATH, detect_device)
        self.ocr = ocr.load_ocr(self.device)
        self.lama = inpaint.load_model(self.device)
        self.lettering = typeset.load_lettering(settings.case)
        self.translator = make_translator(settings)
        self.cache = PageCache(settings.cache_dir / f"v{PIPELINE_VERSION}")
        self.context = ContextStore(settings.cache_dir / "names")
        self.context.rebuild(self.cache)
        self.inflight: dict[str, asyncio.Future] = {}
        self.editing: dict[str, asyncio.Lock] = {}

    @cached_property
    def cpu_lama(self):
        return inpaint.load_model("cpu")

    @contextmanager
    def gpu(self) -> Iterator[None]:
        with self.gpu_lock:
            try:
                yield
            finally:
                if self.device == "cuda":
                    import torch

                    torch.cuda.empty_cache()

    def warmup(self) -> None:
        self.gpu_thread.submit(self._warmup).result()

    def _warmup(self) -> None:
        blank = np.full((1816, 1280, 3), 255, np.uint8)
        blank[800:1000, 600:680] = 0
        with self.gpu():
            detect.detect(self.detector, blank)
            balloon.segment(self.balloons, blank)
            ocr.recognize(self.ocr, Image.fromarray(blank[780:1020, 580:700]))
            for side in (128, 320, 512):
                crop = np.full((side, side, 3), 255, np.uint8)
                mask = np.zeros((side, side), np.uint8)
                mask[side // 4 : side // 2, side // 4 : side // 2] = 255
                inpaint.run_lama(self.lama, crop, mask)

    async def process(self, data: bytes, context_key: str | None, page_index: int | None) -> dict:
        page_id = hashlib.sha256(data).hexdigest()
        cached = self.cache.load(page_id)
        if cached is not None:
            stale = stale_bubbles(cached, names_in_force(self.context.cast(cached.get("context_key"))))
            if not stale:
                return {**cached, "timings_ms": {**{s: 0 for s in STAGES}, "total": 0}, "cached": True}
            return await self.retranslate(page_id, data, cached["context_key"], cached.get("page_index"), [], redo=True)
        if page_id in self.inflight:
            return await asyncio.shield(self.inflight[page_id])
        fut = asyncio.get_running_loop().create_future()
        self.inflight[page_id] = fut
        try:
            result = await self._run(page_id, data, context_key, page_index)
            fut.set_result(result)
            return result
        except BaseException as e:
            fut.set_exception(e)
            fut.exception()
            raise
        finally:
            del self.inflight[page_id]

    async def retranslate(self, page_id: str, data: bytes, context_key: str | None, page_index: int | None, picked: list[int], hint: str = "", redo: bool = False) -> dict:
        async with self.editing.setdefault(page_id, asyncio.Lock()):
            base = self.cache.load(page_id)
            if base is None:
                return await self.process(data, context_key, page_index)
            stale = stale_bubbles(base, names_in_force(self.context.cast(base.get("context_key"))))
            picked = sorted(set(stale) | set(picked))
            if not picked:
                return {**base, "timings_ms": {**{s: 0 for s in STAGES}, "total": 0}, "cached": True}
            if not redo:
                return await self._run(page_id, data, context_key, page_index, base, set(picked), hint)
            try:
                return {**await self._run(page_id, data, context_key, page_index, base, set(picked), hint), "updated": True}
            except TranslatorUnavailable:
                return {**base, "timings_ms": {**{s: 0 for s in STAGES}, "total": 0}, "cached": True}

    async def _run(
        self,
        page_id: str,
        data: bytes,
        context_key: str | None,
        page_index: int | None,
        base: dict | None = None,
        picked: set[int] = frozenset(),
        hint: str = "",
    ) -> dict:
        start = time.perf_counter()
        timer = Timer()
        image = await asyncio.to_thread(decode_image, data)

        def read_page() -> tuple[list[Bubble], list[Bubble]]:
            with self.gpu():
                blocks = timer.run("detect", detect.detect, self.detector, image)
                balloons = timer.run("balloons", balloon.segment, self.balloons, image)
                bubbles, unsure = timer.run("ocr", ocr.read, self.ocr, image, blocks, balloons)
                return timer.run("ocr", ruby.attach, self.ocr, image, bubbles), unsure

        def remove_text(bubbles: list[Bubble]) -> np.ndarray:
            with self.gpu():
                return timer.run("inpaint", inpaint.inpaint, self.lama, image, bubbles).image

        async def remove_text_anywhere(bubbles: list[Bubble]) -> np.ndarray:
            import torch

            try:
                return await loop.run_in_executor(self.gpu_thread, remove_text, bubbles)
            except torch.OutOfMemoryError:
                return (await asyncio.to_thread(timer.run, "inpaint", inpaint.inpaint, self.cpu_lama, image, bubbles)).image

        loop = asyncio.get_running_loop()
        bubbles, unsure = await loop.run_in_executor(self.gpu_thread, read_page)
        matched = match_bubbles(base["bubbles"], bubbles) if base else [None] * len(bubbles)
        pinned = [base["bubbles"][j]["translation"] if j is not None and j not in picked else None for j in matched] if base else None
        fixed = [i for i, j in enumerate(matched) if j is not None and (j in picked or j in base.get("fixed", []))] if base else []
        if bubbles:
            readings = [pair for b in bubbles for pair in b.ruby]
            translate_task = asyncio.create_task(self._translate([b.source for b in bubbles], context_key, page_index, timer, readings, pinned, hint))
            try:
                inpainted = await remove_text_anywhere(bubbles)
            finally:
                translations = await translate_task
            bubbles = [replace(b, translation=t) for b, t in zip(bubbles, translations)]
            if not all(translations):
                inpainted = await asyncio.to_thread(keep_original, image, inpainted, [b for b in bubbles if not b.translation])
            rendered, font_sizes = await asyncio.to_thread(timer.run, "typeset", typeset.typeset, self.lettering, inpainted, bubbles)
        else:
            rendered, font_sizes = image, []
        name, encoded = await asyncio.to_thread(timer.run, "encode", encode, rendered)

        h, w = image.shape[:2]
        revision = base.get("revision", 0) + 1 if base else 0
        result = {
            "page_id": page_id,
            "width": w,
            "height": h,
            "rendered_url": f"/v1/pages/{page_id}/{name}" + (f"?v={revision}" if revision else ""),
            "bubbles": [
                {"box": b.block.box.as_list(), "source": b.source, "translation": b.translation, "vertical": b.block.vertical}
                for b in bubbles
            ],
            "context_key": context_key,
            "page_index": page_index,
            "font_sizes": font_sizes,
            "skipped": [{"box": b.block.box.as_list(), "source": b.source} for b in unsure],
            "names_in_force": names_in_force(self.context.cast(context_key)),
            "revision": revision,
            "fixed": fixed,
        }
        await asyncio.to_thread(self.cache.store, page_id, name, encoded, result)
        if base and base.get("context_key") != context_key:
            self.context.forget(base)
        if context_key:
            lines = [ContextLine(b.source, b.translation) for b in bubbles]
            self.context.add(context_key, page_id, page_index, lines, [lines[i] for i in fixed])
        timings = {**timer.ms, "total": round((time.perf_counter() - start) * 1000)}
        return {**result, "timings_ms": timings, "cached": False}

    async def _translate(
        self,
        lines: list[str],
        context_key: str | None,
        page_index: int | None,
        timer: Timer,
        readings: list[tuple[str, str]],
        pinned: list[str | None] | None,
        hint: str,
    ) -> list[str]:
        t = time.perf_counter()
        try:
            return await translate_page(self.translator, self.context, lines, context_key, page_index, ruby=readings, pinned=pinned, hint=hint)
        finally:
            timer.ms["translate"] = round((time.perf_counter() - t) * 1000)


async def translate_page(
    translator: Translator,
    store: ContextStore,
    lines: list[str],
    context_key: str | None,
    page_index: int | None,
    page: PageImage | None = None,
    ruby: list[tuple[str, str]] = (),
    pinned: list[str | None] | None = None,
    hint: str = "",
    upcoming: Sequence[Sequence[str]] = (),
) -> list[str]:
    if pinned is None:
        listed = [e for e in store.cast(context_key) if e.source != "auto" and e.ja]
        recalled = [None if known and any(e.ja in line and not spells(known, e.en) for e in listed) else known for line, known in zip(lines, store.recall(context_key, lines))]
    else:
        recalled = pinned
    todo = [line for line, known in zip(lines, recalled) if known is None]
    names: dict[str, str] = {}
    terms: dict[str, str] = {}
    for entry in store.cast(context_key):
        (terms if entry.kind == "term" else names).setdefault(entry.en, entry.ja)
    known_ja = set(names.values()) | set(terms.values())
    readings = {base: reading for base, reading in ruby if base not in known_ja and any(base in line for line in todo)}
    context = Context(
        store.before(context_key, page_index),
        names,
        [ContextLine(line, known) for line, known in zip(lines, recalled) if known],
        page,
        readings,
        terms,
        hint,
        [list(page) for page in upcoming],
    )
    context = subset(context, [i for i, known in enumerate(recalled) if known is None])
    fresh = iter(await translate_lines(translator, todo, context) if todo else [])
    out = mend_stutters(lines, [known if known is not None else next(fresh) for known in recalled])
    if pinned is not None:
        out = [known if known is not None else line for known, line in zip(pinned, out)]
    store.learn_names(context_key, furigana_names(lines, out, list(ruby), store.memory(context_key)))
    return out
