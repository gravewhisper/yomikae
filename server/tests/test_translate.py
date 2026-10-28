from __future__ import annotations

from yomikae.cache import ContextStore
from yomikae.model import ContextLine

CAPTION = "Today I'll finish one alone... or not."


def work_with(*pages: list[ContextLine]) -> ContextStore:
    store = ContextStore()
    for idx, lines in pages:
        store.add("g/1", str(idx), idx, lines)
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
    assert store.names("g/1") == {"Nagisa": "a", "An": "b", "Yuki": "e", "Yuka": "e"}


def test_the_glossary_lists_a_name_without_its_honorific():
    store = work_with((1, [ContextLine("宏也落ち着け", "Hiroya-kun, calm down."), ContextLine("宏也さん", "Hiroya-san!")]))
    assert store.names("g/1") == {"Hiroya": "宏也落ち着け"}


def test_a_misread_name_copied_from_the_glossary_gives_way_to_the_real_one():
    import asyncio

    from yomikae.pipeline import translate_page

    class SometimesCopies:
        name = "copies"

        async def translate(self, lines, context):
            pinned = next(iter(context.names), None)
            out, copied = [], False
            for line in lines:
                own = "Nagina-nee" if "ナギナ" in line else "Nagisa-nee"
                name = f"{pinned}-nee" if pinned and not copied else own
                copied = True
                out.append(f"{name}, {line[-2:]}")
            return out

        async def health(self):
            return None

    store = ContextStore()
    pages = [["ナギナ姉、待って"], ["ナギサ姉、来て", "ナギサ姉、早く"], ["ナギサ姉、見て", "ナギサ姉、どこ"], ["ナギサ姉、ほら"]]
    outs = []
    for idx, lines in enumerate(pages):
        outs.append(asyncio.run(translate_page(SometimesCopies(), store, lines, "g/1", idx)))
        store.add("g/1", str(idx), idx, [ContextLine(a, b) for a, b in zip(lines, outs[-1])])
    assert outs[1][0].startswith("Nagina-nee") and outs[3] == ["Nagisa-nee, ほら"], outs
    assert store.names("g/1") == {"Nagisa": "ナギサ姉、早く"}


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


def test_a_sound_cut_off_before_the_next_bubble_becomes_its_stutter():
    import asyncio

    from yomikae.pipeline import translate_page

    replies = {"ふっ": "Heh.", "ふざけんなっ！": "Don't mess with me!", "ど…": "", "どういう意味さ…": "...What do you mean?", "そっ": "So...", "．．．そっか": "...I see."}

    class Literal:
        name = "literal"

        async def translate(self, lines, context):
            return [replies[line] for line in lines]

        async def health(self):
            return None

    lines = list(replies)
    out = asyncio.run(translate_page(Literal(), ContextStore(), lines, "g/1", 0))
    assert out == ["D-", "Don't mess with me!", "Wh-", "...What do you mean?", "I-", "...I see."]


def test_a_kana_sound_before_a_line_that_starts_with_kanji_becomes_its_stutter():
    from yomikae.translate import mend_stutters

    lines = ["み", "見えない…", "か…", "考えるな", "あ", "会いたい"]
    out = ["Mi", "I can't see...", "Ka...", "Don't think.", "Ah.", "I miss you."]
    assert mend_stutters(lines, out) == ["I-", "I can't see...", "D-", "Don't think.", "Ah.", "I miss you."]


def test_interjections_and_drawn_out_sounds_are_not_stutters():
    from yomikae.translate import mend_stutters

    lines = ["あ！", "あれは誰？", "はっ", "はは…", "あ？", "あ、あの…", "かっ…", "かかかっ", "なー", "なんで", "あ", "ありがとう"]
    out = ["Ah!", "Who's that?", "Ha!", "Haha...", "Huh?", "U-um...", "Ka...", "Kakaka", "Hey~", "Why?", "Oh.", "Thanks."]
    assert mend_stutters(lines, out) == out


def test_an_english_family_word_never_keeps_a_japanese_honorific():
    import asyncio

    from yomikae.translate import Context, translate_lines

    class Hybrid:
        name = "hybrid"

        async def translate(self, lines, context):
            return ["Brother-sama...", "Thanks, big sister-chan!", "Onii-sama, Yuki-chan and Mom."]

        async def health(self):
            return None

    out = asyncio.run(translate_lines(Hybrid(), ["兄さま…", "ありがとう、お姉ちゃん！", "お兄さま、ゆきちゃんとママ。"], Context()))
    assert out == ["Brother...", "Thanks, big sister!", "Onii-sama, Yuki-chan and Mom."]


class Scripted:
    name = "scripted"

    def __init__(self, *replies: dict[str, str]):
        self.replies = list(replies)
        self.calls: list[list[str]] = []

    async def translate(self, lines, context):
        self.calls.append(lines)
        reply = self.replies.pop(0)
        return [reply[line] for line in lines]

    async def health(self):
        return None


def test_a_line_left_with_only_its_symbols_is_retried_alone():
    import asyncio

    from yomikae.translate import Context, translate_lines

    lines = ["凛恋さんはたから", "作", "変態♡"]
    translator = Scripted({"凛恋さんはたから": "Riren-san is a", "作": "perv", "変態♡": "♡"}, {"変態♡": "Pervert♡"})
    out = asyncio.run(translate_lines(translator, lines, Context()))
    assert out == ["Riren-san is a", "perv", "Pervert♡"]
    assert translator.calls[1] == ["変態♡"]


def test_a_line_still_without_words_after_its_retry_is_left_blank():
    import asyncio

    from yomikae.translate import Context, translate_lines

    translator = Scripted({"変態♡": "♡"}, {"変態♡": "♡♡"})
    assert asyncio.run(translate_lines(translator, ["変態♡"], Context())) == [""]


def test_lines_of_only_sounds_and_dots_may_come_back_without_words():
    import asyncio

    from yomikae.translate import Context, translate_lines

    lines = ["っ", "．．．．．．っ", "ー", "……"]
    translator = Scripted({"っ": "!", "．．．．．．っ": "......!", "ー": "-", "……": "..."})
    assert asyncio.run(translate_lines(translator, lines, Context())) == ["!", "......!", "-", "..."]
    assert len(translator.calls) == 1


def test_a_short_word_or_a_word_with_a_heart_needs_words_back():
    import asyncio

    from yomikae.translate import Context, translate_lines

    lines = ["あ♡", "ん", "嘘"]
    translator = Scripted({"あ♡": "♡", "ん": "...", "嘘": "!?"}, {"あ♡": "Ah♡", "ん": "Mm", "嘘": "No way!?"})
    assert asyncio.run(translate_lines(translator, lines, Context())) == ["Ah♡", "Mm", "No way!?"]
