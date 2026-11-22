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

KOREAN_PROMPT = """You translate Korean webtoon (manhwa) dialogue into English for an in-place scanlation.
Write natural, casual English that sounds like real people talking in a webtoon, not a literal gloss.
Keep each line about as short as the original so it fits the speech bubble.
The Korean comes from OCR, and a line break in it only marks where the bubble wrapped the text.
Keep the difference between casual speech (banmal) and polite or formal speech (jondaetmal) in the English register: blunt and relaxed for casual, more careful for polite, stiff for formal.
Romanize names in Revised Romanization, in the order the Korean gives them, family name first when it is there. For example, 김민준 is "Kim Minjun".
Keep Korean honorific suffixes, romanized and joined to the name or title with a hyphen: -ssi and -nim. For example, 민준 씨 is "Minjun-ssi" and 사장님 is "Sajang-nim". Add a suffix only where the Korean has one after that word; a bare name stays bare. Never turn them into Mr., Ms., Sir, or Boss. Drop the vocative -a or -ya after a name: 민준아 is just "Minjun".
Keep these address terms romanized, as words of their own, when a speaker uses them for someone: hyung, noona, oppa, unnie, sunbae, hoobae, ajussi, ajumma. Translate every other family word into plain English: 엄마 is "Mom", 아빠 is "Dad", 할머니 is "Grandma", 할아버지 is "Grandpa". Only names, these suffixes, and these address terms are romanized; translate everything else into English.
Keep symbols like ♡, ♥, and ♪ where the original has them.
"names" maps each name this work already uses to a Korean line it was translated from; when that character appears, spell the name exactly that way.
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
    vertical: bool
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
    vertical=True,
    reader="manga-ocr",
    ruby=True,
    prompt=JAPANESE_PROMPT,
)

HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")
KOREAN = Language(
    code="ko",
    name="Korean",
    letters=HANGUL,
    leftover=HANGUL,
    silent="",
    right_to_left=False,
    vertical=False,
    reader="paddle",
    ruby=False,
    prompt=KOREAN_PROMPT,
)

LANGUAGES: dict[str, Language] = {lang.code: lang for lang in (JAPANESE, KOREAN)}
DEFAULT = JAPANESE
