from __future__ import annotations

from yomikae.cache import ContextStore
from yomikae.model import ContextLine

CAPTION = "Today I'll finish one alone... or not."


def work_with(*pages: list[ContextLine]) -> ContextStore:
    store = ContextStore()
    for idx, lines in pages:
        store.add("g/1", idx, lines)
    return store


def test_ocr_variants_of_a_caption_recall_its_translation():
    store = work_with((37, [ContextLine("今日は一人で一抜く一抜かない", CAPTION), ContextLine("なにこれ？", "What is this?")]))
    for variant in ["今日は一人で一抜く一抜かない", "今日は一人で一抜くっ抜かない", "今日は一人で一抜く一歩かない", "今日は一人で抜く…抜かない"]:
        assert store.recall("g/1", [variant]) == [CAPTION], variant
    assert store.recall("g/1", ["今日はこれで．．", "今日はいい天気だね", "なにこれ？"]) == [None, None, "What is this?"]
    assert store.recall("g/2", ["今日は一人で一抜く一抜かない"]) == [None]


def test_a_line_that_only_shares_most_of_its_words_is_not_recalled():
    store = work_with((37, [ContextLine("はーいありがとうございまーす", "Here you go, thank you very much~")]))
    assert store.recall("g/1", ["．．．ありがと．．ございます", "はいありがとうございました"]) == [None, None]


def test_a_blank_translation_is_never_recalled():
    store = work_with((1, [ContextLine("今日は一人で一抜く一抜かない", "")]))
    assert store.recall("g/1", ["今日は一人で一抜く一抜かない"]) == [None]


def test_the_glossary_keeps_the_most_used_spelling_of_a_name():
    store = work_with(
        (1, [ContextLine("a", "Nagisa-nee, wait!"), ContextLine("b", "Thanks, An-chan.")]),
        (2, [ContextLine("c", "Nagisaki-nee is here.")]),
        (3, [ContextLine("d", "Nagisa-nee?"), ContextLine("e", "Yuki-chan and Yuka-chan.")]),
    )
    assert store.names("g/1") == {"Nagisa-nee": "a", "An-chan": "b", "Yuki-chan": "e", "Yuka-chan": "e"}
