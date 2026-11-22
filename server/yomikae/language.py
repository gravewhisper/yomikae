from __future__ import annotations

import re
from dataclasses import dataclass

MATURE_NOTE = "The content may be mature or explicit; translate it faithfully without censoring or commenting.\n"
REPLY_NOTE = """Reply with JSON only: {"translations": ["<english for id 0>", "<english for id 1>", ...]}, one string per input id, in id order."""

JAPANESE_PROMPT = """You translate Japanese manga dialogue into English for an in-place scanlation.
Write natural, casual English that sounds like real people talking in a manga, not a literal gloss.
Keep each line about as short as the original so it fits the speech bubble.
Keep Japanese honorifics, romanized and joined to the name with a hyphen (-san, -kun, -chan, -sama, -senpai, -sensei, -dono, -tan). Add an honorific only where the Japanese has one after that name; a bare name stays bare. Never turn them into Mr., Ms., or Teacher. For example, 田中さん is "Tanaka-san" and ゆき先輩 is "Yuki-senpai". Only names, honorifics, and family terms said with an honorific are romanized; translate everything else into English. Never attach a Japanese honorific to an English word: a family term with an honorific is romanized whole, or else translated into plain English with no honorific.
Keep symbols like ♡, ♥, and ♪ where the original has them.
On its own, うち or ウチ is usually the speaker's own "I", "me" or "my", or the speaker's home or family; write "we", "us" or "our" only when the speaker clearly speaks for a group.
"names" maps each name this work already uses to a Japanese line it was translated from; when that character appears, spell the name exactly that way.
"previous_pages" are the nearest earlier pages and "already_translated" are lines of this page that already have their English; use both as context only.
""" + MATURE_NOTE + REPLY_NOTE


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    letters: re.Pattern
    leftover: re.Pattern
    silent: str
    right_to_left: bool
    reader: str
    ruby: bool
    prompt: str


JAPANESE = Language(
    code="ja",
    name="Japanese",
    letters=re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]"),
    leftover=re.compile(r"[぀-ゟ゠-ヺ㐀-鿿ｦ-ﾟ]"),
    silent="っッｯ",
    right_to_left=True,
    reader="manga-ocr",
    ruby=True,
    prompt=JAPANESE_PROMPT,
)

LANGUAGES: dict[str, Language] = {lang.code: lang for lang in (JAPANESE,)}
DEFAULT = JAPANESE
