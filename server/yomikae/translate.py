from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

import httpx
from PIL import Image, ImageDraw, ImageFont

from .config import Settings
from .model import Box, ContextLine


class TranslatorUnavailable(Exception):
    pass


class TranslatorFailed(Exception):
    pass


class Misaligned(Exception):
    pass


IMAGE_SIDE = 1024
IMAGE_QUALITY = 85
TAG_SIZE = 18


@dataclass(frozen=True, eq=False)
class PageImage:
    image: Image.Image
    boxes: tuple[Box, ...]
    tagged: bool


def page_image(image: Image.Image, boxes: Sequence[Box], tagged: bool, side: int = IMAGE_SIDE) -> PageImage:
    scale = min(1.0, side / max(image.size))
    small = image.convert("RGB")
    if scale < 1:
        small = small.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    return PageImage(small, tuple(Box(*(round(v * scale) for v in b.as_list())) for b in boxes), tagged)


@dataclass(frozen=True)
class Context:
    pages: list[list[ContextLine]] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    done: list[ContextLine] = field(default_factory=list)
    page: PageImage | None = None


def subset(context: Context, indices: Sequence[int]) -> Context:
    if context.page is None:
        return context
    return replace(context, page=replace(context.page, boxes=tuple(context.page.boxes[i] for i in indices)))


class Translator(Protocol):
    name: str

    async def translate(self, lines: list[str], context: Context) -> list[str]: ...

    async def health(self) -> str | None:
        ...


SYSTEM_PROMPT = """You translate Japanese manga dialogue into English for an in-place scanlation.
Write natural, casual English that sounds like real people talking in a manga, not a literal gloss.
Keep each line about as short as the original so it fits the speech bubble.
Keep Japanese honorifics, romanized and joined to the name with a hyphen (-san, -kun, -chan, -sama, -senpai, -sensei, -dono, -tan). Never turn them into Mr., Ms., or Teacher. For example, 田中さん is "Tanaka-san" and ゆき先輩 is "Yuki-senpai". Only names and honorifics are romanized; translate everything else into English.
Keep symbols like ♡, ♥, and ♪ where the original has them.
On its own, うち or ウチ is usually the speaker's own "I", "me" or "my", or the speaker's home or family; write "we", "us" or "our" only when the speaker clearly speaks for a group.
"names" maps each name this work already uses to a Japanese line it was translated from; when that character appears, spell the name exactly that way.
"previous_pages" are the nearest earlier pages and "already_translated" are lines of this page that already have their English; use both as context only.
The content may be mature or explicit; translate it faithfully without censoring or commenting.
Reply with JSON only: {"translations": ["<english for id 0>", "<english for id 1>", ...]}, one string per input id, in id order."""

PAGE_NOTE = "\nThe attached image is the page these lines come from; use it to see who says each line and to whom."
TAGGED_NOTE = "\nThe attached image is the page these lines come from, with each line's id drawn as a small red numbered tag at its text; use it to see who says each line and to whom."

RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "translations",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "translations": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["translations"],
        },
    },
}

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
JSON_LEAK = re.compile(r'[{}]|"\s*,\s*"|^[\s\[\]",:]*$')


def parse_translations(content: str, n: int) -> list[str] | None:
    m = FENCE.search(content)
    body = m.group(1) if m else content
    start = min((i for i in (body.find("{"), body.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(body[start:])
    except json.JSONDecodeError:
        return None
    items = data.get("translations") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    out: dict[int, str] = {}
    for pos, item in enumerate(items):
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            idx = item.get("id", pos)
            if isinstance(idx, int) and 0 <= idx < n:
                out[idx] = item["text"].strip()
        elif isinstance(item, str) and len(items) == n:
            out[pos] = item.strip()
    if sorted(out) != list(range(n)) or any(JSON_LEAK.search(t) for t in out.values()):
        return None
    return [out[i] for i in range(n)]


JAPANESE = re.compile(r"[\u3040-\u309f\u30a0-\u30fa\u3400-\u9fff\uff66-\uff9f]")


def pairs(lines: list[ContextLine]) -> list[dict]:
    return [{"ja": c.source, "en": c.translation} for c in lines]


def build_user_message(lines: list[str], context: Context) -> str:
    payload = {
        "names": context.names,
        "previous_pages": [pairs(page) for page in context.pages],
        "already_translated": pairs(context.done),
        "lines": [{"id": i, "text": t} for i, t in enumerate(lines)],
    }
    return json.dumps({k: v for k, v in payload.items() if v or k == "lines"}, ensure_ascii=False)


def draw_tags(page: PageImage) -> Image.Image:
    image = page.image.copy()
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=TAG_SIZE)
    for i, box in enumerate(page.boxes):
        l, t, r, b = draw.textbbox((0, 0), str(i), font=font)
        w, h = r - l + 6, b - t + 6
        x = min(max(0, box.x0 - w + 4), image.width - w)
        y = min(max(0, box.y0 - h + 4), image.height - h)
        draw.rectangle((x, y, x + w, y + h), fill=(220, 0, 0))
        draw.text((x + 3 - l, y + 3 - t), str(i), font=font, fill=(255, 255, 255))
    return image


def image_part(page: PageImage) -> dict:
    buf = io.BytesIO()
    (draw_tags(page) if page.tagged else page.image).save(buf, "JPEG", quality=IMAGE_QUALITY)
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}


def build_messages(lines: list[str], context: Context) -> list[dict]:
    user = build_user_message(lines, context)
    if context.page is None:
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    note = TAGGED_NOTE if context.page.tagged else PAGE_NOTE
    return [{"role": "system", "content": SYSTEM_PROMPT + note}, {"role": "user", "content": [image_part(context.page), {"type": "text", "text": user}]}]


def untranslated(source: str, text: str) -> bool:
    if unicodedata.normalize("NFKC", text).strip() == unicodedata.normalize("NFKC", source).strip():
        return True
    return len(JAPANESE.findall(text)) > sum(ch.isascii() and ch.isalpha() for ch in text)


async def aligned(translator: Translator, lines: list[str], context: Context) -> list[str]:
    try:
        return await translator.translate(lines, context)
    except Misaligned:
        if len(lines) == 1:
            return [""]
        mid = len(lines) // 2
        first = await aligned(translator, lines[:mid], subset(context, range(mid)))
        return first + await aligned(translator, lines[mid:], subset(context, range(mid, len(lines))))


async def translate_lines(translator: Translator, lines: list[str], context: Context) -> list[str]:
    out = await aligned(translator, lines, context)
    echoes = [i for i, (source, text) in enumerate(zip(lines, out)) if text and untranslated(source, text)]
    if echoes:
        retry = await aligned(translator, [lines[i] for i in echoes], subset(context, echoes))
        for i, text in zip(echoes, retry):
            out[i] = "" if untranslated(lines[i], text) else text
    return out


TRANSIENT_ERRORS = ("Channel Error", "predict request failed: fetch failed")


def transient(r: httpx.Response) -> bool:
    return r.status_code == 400 and any(e in r.text for e in TRANSIENT_ERRORS)


class OpenAICompatible:
    retry_delays = (0.5, 1.0)

    def __init__(self, settings: Settings):
        self.base = settings.openai_base_url
        self.model = settings.openai_model
        self.name = f"openai:{self.model}"
        self.headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        self.reasoning_effort = settings.openai_reasoning_effort
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=2.0), headers=self.headers)
        self.prompt_tokens = 0

    async def _complete(self, messages: list[dict], structured: bool) -> str:
        body: dict = {"model": self.model, "messages": messages, "temperature": 0.3}
        if structured:
            body["response_format"] = RESPONSE_SCHEMA
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        for delay in (*self.retry_delays, None):
            try:
                r = await self.client.post(f"{self.base}/chat/completions", json=body)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                raise TranslatorUnavailable(f"{self.base} unreachable: {e!r}") from e
            if delay is None or not transient(r):
                break
            await asyncio.sleep(delay)
        if r.status_code >= 500 or r.status_code in (401, 403, 404):
            raise TranslatorUnavailable(f"{self.base} returned {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            tries = f" after {len(self.retry_delays) + 1} tries" if transient(r) else ""
            raise TranslatorFailed(f"translator at {self.base} returned {r.status_code}{tries}: {r.text[:200]}")
        try:
            reply = r.json()
            self.prompt_tokens += (reply.get("usage") or {}).get("prompt_tokens") or 0
            return reply["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError, AttributeError) as e:
            raise TranslatorUnavailable(f"unexpected response shape: {r.text[:200]}") from e

    async def translate(self, lines: list[str], context: Context) -> list[str]:
        parsed = parse_translations(await self._complete(build_messages(lines, context), structured=True), len(lines))
        if parsed is None:
            raise Misaligned(f"reply does not align with {len(lines)} lines")
        return parsed

    async def health(self) -> str | None:
        try:
            r = await self.client.get(f"{self.base}/models", timeout=2.0)
        except httpx.HTTPError as e:
            return f"translator unreachable at {self.base}: {e!r}"
        return None if r.status_code < 400 else f"translator at {self.base} returned {r.status_code}"


class GoogleFree:

    name = "google_free"
    URL = "https://translate.googleapis.com/translate_a/single"

    def __init__(self, settings: Settings):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=3.0))

    async def _one(self, text: str) -> str:
        params = {"client": "gtx", "sl": "ja", "tl": "en", "dt": "t", "q": text}
        try:
            r = await self.client.get(self.URL, params=params)
            r.raise_for_status()
            return "".join(seg[0] for seg in r.json()[0] if seg and seg[0])
        except (httpx.HTTPError, ValueError, TypeError, IndexError) as e:
            raise TranslatorUnavailable(f"google_free failed: {e!r}") from e

    async def translate(self, lines: list[str], context: Context) -> list[str]:
        return [await self._one(t) for t in lines]

    async def health(self) -> str | None:
        try:
            await self._one("テスト")
        except TranslatorUnavailable as e:
            return str(e)
        return None


class Fake:

    name = "fake"

    def __init__(self, settings: Settings):
        pass

    async def translate(self, lines: list[str], context: Context) -> list[str]:
        return [f"Line {i + 1}: hello there, this is a test" for i in range(len(lines))]

    async def health(self) -> str | None:
        return None


REGISTRY: dict[str, Callable[[Settings], Translator]] = {
    "openai": OpenAICompatible,
    "google_free": GoogleFree,
    "fake": Fake,
}


def make_translator(settings: Settings) -> Translator:
    try:
        return REGISTRY[settings.translator](settings)
    except KeyError:
        raise ValueError(f"YOMIKAE_TRANSLATOR={settings.translator!r}; expected one of {sorted(REGISTRY)}") from None
