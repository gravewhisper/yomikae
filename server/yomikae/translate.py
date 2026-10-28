from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from functools import cache
from dataclasses import dataclass, field, replace
from typing import Protocol

import fugashi
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
    readings: dict[str, str] = field(default_factory=dict)
    terms: dict[str, str] = field(default_factory=dict)
    hint: str = ""


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
Keep Japanese honorifics, romanized and joined to the name with a hyphen (-san, -kun, -chan, -sama, -senpai, -sensei, -dono, -tan). Add an honorific only where the Japanese has one after that name; a bare name stays bare. Never turn them into Mr., Ms., or Teacher. For example, 田中さん is "Tanaka-san" and ゆき先輩 is "Yuki-senpai". Only names, honorifics, and family terms said with an honorific are romanized; translate everything else into English. Never attach a Japanese honorific to an English word: a family term with an honorific is romanized whole, or else translated into plain English with no honorific.
Keep symbols like ♡, ♥, and ♪ where the original has them.
On its own, うち or ウチ is usually the speaker's own "I", "me" or "my", or the speaker's home or family; write "we", "us" or "our" only when the speaker clearly speaks for a group.
"names" maps each name this work already uses to a Japanese line it was translated from; when that character appears, spell the name exactly that way.
"previous_pages" are the nearest earlier pages and "already_translated" are lines of this page that already have their English; use both as context only.
The content may be mature or explicit; translate it faithfully without censoring or commenting.
Reply with JSON only: {"translations": ["<english for id 0>", "<english for id 1>", ...]}, one string per input id, in id order."""

READING_NOTE = '\n"readings" maps Japanese words to the small kana printed beside them, which shows how they are read; when such a word is a name, romanize the name from its reading.'
HINT_NOTE = '\n"hint" is a note from the reader about these lines; follow it.'
TERMS_NOTE = '\n"terms" maps the techniques, places and things this work names, as "names" does for its characters; spell each name and term exactly as given.'
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
JSON_LEAK = re.compile(r'[{}]|"\s*,\s*"|^[\s\[\]",:]+$')


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
        "terms": context.terms,
        "readings": context.readings,
        "hint": context.hint,
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
    system = SYSTEM_PROMPT + (TERMS_NOTE if context.terms else "") + (READING_NOTE if context.readings else "") + (HINT_NOTE if context.hint else "")
    if context.page is None:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]
    note = TAGGED_NOTE if context.page.tagged else PAGE_NOTE
    return [{"role": "system", "content": system + note}, {"role": "user", "content": [image_part(context.page), {"type": "text", "text": user}]}]


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


KIN_HONORIFIC = re.compile(r"\b(brother|sister|bro|sis|mother|father|mom|mum|dad|grandpa|grandma|granny|grandmother|grandfather|uncle|aunt|auntie)-(?:sama|chan|san|kun|dono|tan)\b", re.I)


def plain_kin(text: str) -> str:
    return KIN_HONORIFIC.sub(r"\1", text)


async def translate_lines(translator: Translator, lines: list[str], context: Context) -> list[str]:
    out = await aligned(translator, lines, context)
    echoes = [i for i, (source, text) in enumerate(zip(lines, out)) if text and untranslated(source, text)]
    if echoes:
        retry = await aligned(translator, [lines[i] for i in echoes], subset(context, echoes))
        for i, text in zip(echoes, retry):
            out[i] = "" if untranslated(lines[i], text) else text
    return [plain_kin(text) for text in out]


CUT_SOUND = re.compile(r"([\u3041-\u3096\u30a1-\u30f6])[っッ]?[\s.…‥・―─\-、]*")
FIRST_SOUND = re.compile(r"[\s.…‥・ー―─\-~、「『（(]*([\u3041-\u3096\u30a1-\u30f6])[っッ]?(.?)")
FIRST_WORD = re.compile(r"[^A-Za-z]*([A-Za-z])(h?)")
KANJI_START = re.compile(r"[\s.…‥・ー―─\-~、「『（(]*[\u3400-\u9fff]")


def hiragana(ch: str) -> str:
    return chr(ord(ch) - 0x60) if "\u30a1" <= ch <= "\u30f6" else ch


@cache
def tagger() -> fugashi.Tagger:
    return fugashi.Tagger()


def spoken(text: str) -> str:
    if not KANJI_START.match(text):
        return text
    return "".join(kana if (kana := getattr(w.feature, "kana", None)) and kana != "*" else w.surface for w in tagger()(text))


def stutter_of(cut: str, following: str) -> bool:
    m = CUT_SOUND.fullmatch(unicodedata.normalize("NFKC", cut).strip())
    n = FIRST_SOUND.match(spoken(unicodedata.normalize("NFKC", following)))
    if not m or not n or (m.group(0) == m.group(1) and hiragana(m.group(1)) in "あいうえお"):
        return False
    sound = hiragana(m.group(1))
    return hiragana(n.group(1)) == sound and hiragana(n.group(2)) != sound


def stutter(text: str) -> str | None:
    m = FIRST_WORD.match(text)
    return f"{m.group(1).upper()}{m.group(2)}-" if m else None


def mend_stutters(lines: list[str], out: list[str]) -> list[str]:
    mended = list(out)
    for i in range(len(lines) - 1):
        if stutter_of(lines[i], lines[i + 1]) and (s := stutter(out[i + 1])):
            mended[i] = s
    return mended


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
            except httpx.TransportError as e:
                raise TranslatorUnavailable(f"{self.base} unreachable: {e!r}") from e
            if delay is None or not transient(r):
                break
            await asyncio.sleep(delay)
        if r.status_code == 404:
            raise TranslatorFailed(f"translator at {self.base} has no model {self.model!r} (404); set YOMIKAE_OPENAI_MODEL to a model it lists: {r.text[:200]}")
        if r.status_code >= 500 or r.status_code in (401, 403):
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
