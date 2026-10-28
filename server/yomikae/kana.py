from __future__ import annotations

import re
import unicodedata

HONORIFIC_KANA = {
    "san": ("さん",), "kun": ("くん", "君"), "chan": ("ちゃん",), "sama": ("さま", "様"), "senpai": ("先輩", "せんぱい"),
    "sensei": ("先生", "せんせい"), "dono": ("殿", "どの"), "nee": ("姉", "ねえ", "ねぇ"), "nii": ("兄", "にい", "にぃ"),
}
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
