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


def test_a_tagged_page_retried_in_halves_tags_only_the_lines_each_half_sends():
    import asyncio

    from PIL import Image

    from yomikae.model import Box
    from yomikae.translate import Context, Misaligned, build_messages, page_image, translate_lines

    page = page_image(Image.new("RGB", (2048, 1024), "white"), [Box(100 * i, 0, 100 * i + 50, 50) for i in range(4)], tagged=True)
    calls = []

    class SplitsOnce:
        name = "splits"

        async def translate(self, lines, context):
            calls.append((lines, context.page.boxes, build_messages(lines, context)))
            if len(lines) > 2:
                raise Misaligned("too many")
            return [f"en {line}" for line in lines]

        async def health(self):
            return None

    out = asyncio.run(translate_lines(SplitsOnce(), ["a", "b", "c", "d"], Context(page=page)))
    assert out == ["en a", "en b", "en c", "en d"]
    assert page.image.size == (1024, 512) and page.boxes[1] == Box(50, 0, 75, 25)
    assert [boxes for _, boxes, _ in calls[1:]] == [page.boxes[:2], page.boxes[2:]]
    system, user = calls[0][2]
    assert "red numbered tag" in system["content"]
    assert user["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
