from __future__ import annotations

import asyncio
import json

from yomikae.cache import ContextStore
from yomikae.model import CastEntry, ContextLine
from yomikae.pipeline import translate_page
from yomikae.ruby import furigana_names, romanize, tidy
from yomikae.translate import Context, build_messages


class Recorder:
    name = "recorder"

    def __init__(self, reply):
        self.reply = reply
        self.contexts = []

    async def translate(self, lines, context):
        self.contexts.append(context)
        return self.reply(lines)

    async def health(self):
        return None


def test_auto_names_reach_the_prompt_exactly_as_before():
    store = ContextStore()
    store.add("g/1", "p1", 1, [ContextLine("渚姉、待って", "Nagisa-nee, wait!")])
    assert [(e.en, e.ja, e.source) for e in store.cast("g/1")] == [("Nagisa", "渚姉、待って", "auto")]
    translator = Recorder(lambda lines: ["Hi."] * len(lines))
    asyncio.run(translate_page(translator, store, ["こんにちは"], "g/1", 2))
    assert translator.contexts[0].names == store.names("g/1")
    assert translator.contexts[0].readings == {}
    assert "readings" not in build_messages(["こんにちは"], translator.contexts[0])[0]["content"]


def test_a_user_name_hides_the_auto_spelling_it_corrects_and_is_never_overwritten(tmp_path):
    store = ContextStore(tmp_path)
    store.add("g/1", "p1", 1, [ContextLine("凛恋さん、おはよう", "Morning, Riren-san."), ContextLine("ユキちゃん", "Yuki-chan!")])
    store.set_name("g/1", CastEntry("凛恋", "Rinren", "りんれん", "user"))
    store.learn_names("g/1", [CastEntry("凛恋", "Rinko", "りんこ", "furigana")])
    cast = store.cast("g/1")
    assert [(e.en, e.source) for e in cast] == [("Rinren", "user"), ("Yuki", "auto")]
    reloaded = ContextStore(tmp_path)
    assert [(e.ja, e.en, e.reading, e.source) for e in reloaded.saved_names("g/1")] == [("凛恋", "Rinren", "りんれん", "user")]
    assert reloaded.saved_names("g/2") == []


def test_a_furigana_name_is_learned_once_and_replaces_a_wrong_auto_spelling():
    store = ContextStore()
    store.add("g/1", "p1", 1, [ContextLine("我愛羅くん！", "Gara-kun!")])
    translator = Recorder(lambda lines: ["Gaara-kun, wait!"] * len(lines))
    asyncio.run(translate_page(translator, store, ["我愛羅くん待って"], "g/1", 2, ruby=[("我愛羅", "があら"), ("待", "ま")]))
    assert translator.contexts[0].readings == {"我愛羅": "があら", "待": "ま"}
    assert "readings" in build_messages(["x"], translator.contexts[0])[0]["content"]
    assert [(e.ja, e.en, e.reading, e.source) for e in store.cast("g/1")] == [("我愛羅", "Gaara", "があら", "furigana")]
    asyncio.run(translate_page(translator, store, ["我愛羅くん"], "g/1", 3, ruby=[("我愛羅", "があら")]))
    assert translator.contexts[1].names == {"Gaara": "我愛羅"} and translator.contexts[1].readings == {}


def test_furigana_is_a_name_only_when_its_reading_is_spelled_in_the_english():
    lines = ["明日は烏丸枢の葬儀だ"]
    found = furigana_names(lines, ["Karasuma Kururu's funeral is tomorrow."], [("明日", "あした"), ("烏丸枢", "からすまくるる")])
    assert found == [CastEntry("烏丸枢", "Karasuma Kururu", "からすまくるる", "furigana")]
    assert furigana_names(lines, ["The funeral is tomorrow."], [("烏丸枢", "からすまくるる")]) == []
    assert furigana_names(lines, ["Kururu Karasuma's funeral."], [("烏丸枢", "からすまくるる")])[0].en == "Kururu Karasuma"
    assert [romanize(r) for r in ("きょうこ", "さっちゃん", "シュウ", "じゃない")] == ["kyouko", "satchan", "shuu", "janai"]
    assert [tidy(r) for r in ("じゅっ", "っくえ", "がっこう", "さょう", "きょう")] == ["じゅつ", "つくえ", "がっこう", "さよう", "きょう"]
    assert furigana_names(["術だ"], ["Jutsu!"], [("術", "じゅつ")]) == []
    assert furigana_names(["先生！"], ["Sensei!"], [("先生", "せんせい")]) == []


def test_a_single_kanji_is_a_name_only_when_both_languages_address_it_with_an_honorific():
    kururu = CastEntry("枢", "Kururu", "くるる", "furigana")
    assert furigana_names(["枢さん、おはよう"], ["Morning, Kururu-san."], [("枢", "くるる")]) == [kururu]
    earlier = [ContextLine("枢さん…", "Kururu-san...")]
    assert furigana_names(["枢と呼んで"], ["Call me Kururu."], [("枢", "くるる")], earlier) == [kururu]
    assert furigana_names(["枢と呼んで"], ["Call me Kururu."], [("枢", "くるる")]) == []
    assert furigana_names(["鬼だ！", "炎の術"], ["An Oni!", "Honoo Jutsu!"], [("鬼", "おに"), ("炎", "ほのお")]) == []
    assert furigana_names(["皆さん！", "家には帰さんからな"], ["Everyone!", "You're not going home!"], [("皆", "みな"), ("帰", "かえ")]) == []
    assert furigana_names(["大鬼さん"], ["Ooni-san"], [("鬼", "おに")]) == []
    assert furigana_names(["お孫様！"], ["Mago-sama!"], [("孫", "まご")]) == []
    assert furigana_names(["孫様！"], ["Ki-sama!"], [("孫", "き")]) == []


def test_furigana_terms_go_to_their_own_map_and_names_stay_as_before():
    lines = ["螺旋丸を呉葉さんに託す", "呉葉の「極焔」"]
    english = ["I entrust the Rasengan to Kureha-san.", "Kureha's \" Gokuen\"!"]
    found = furigana_names(lines, english, [("螺旋丸", "らせんがん"), ("呉葉", "くれは"), ("極焔", "ごくえん")])
    assert [(e.ja, e.kind) for e in found] == [("螺旋丸", "term"), ("呉葉", "name"), ("極焔", "term")]
    assert furigana_names(["忍術だ"], ["Ninjutsu!"], [("忍術", "にんじゅつ")])[0].kind == "term"
    store = ContextStore()
    store.learn_names("g/1", found)
    translator = Recorder(lambda lines: ["Go."] * len(lines))
    asyncio.run(translate_page(translator, store, ["行け"], "g/1", 2))
    context = translator.contexts[0]
    assert context.names == {"Kureha": "呉葉"} and context.terms == {"Rasengan": "螺旋丸", "Gokuen": "極焔"}
    system, user = (m["content"] for m in build_messages(["行け"], context))
    assert '"terms"' in system and '"terms"' in user
    plain = build_messages(["行け"], Context(names={"Kureha": "呉葉"}))
    assert '"terms"' not in plain[0]["content"] and '"terms"' not in plain[1]["content"]


def test_a_saved_entry_without_a_kind_reads_as_a_name_and_the_users_kind_wins(tmp_path, client):
    store = ContextStore(tmp_path)
    store.names_path("g/1").write_text('{"names": [{"ja": "渚", "en": "Nagisa", "source": "user"}]}')
    assert store.saved_names("g/1")[0].kind == "name"
    store.learn_names("g/1", [CastEntry("螺旋丸", "Rasengan", "らせんがん", "furigana", "term")])
    store.set_name("g/1", CastEntry("螺旋丸", "Rasengan", "らせんがん", "user", "name"))
    assert [(e.en, e.source, e.kind) for e in ContextStore(tmp_path).cast("g/1")] == [("Nagisa", "user", "name"), ("Rasengan", "user", "name")]
    key = "example.com/g/kinds"
    r = client.put("/v1/names/entry", params={"context_key": key}, json={"ja": "螺旋丸", "en": "Rasengan", "kind": "term"})
    assert r.json()["names"] == [{"ja": "螺旋丸", "en": "Rasengan", "reading": "", "source": "user", "kind": "term", "was": [], "guess": ""}]
    r = client.put("/v1/names", params={"context_key": key}, json={"names": [{"ja": "渚", "en": "Nagisa"}]})
    assert r.json()["names"][0]["kind"] == "name"
    assert client.put("/v1/names/entry", params={"context_key": key}, json={"en": "A", "kind": "place"}).status_code == 400


def test_names_api_reads_and_replaces_one_works_list(client):
    key = "example.com/g/cast"
    assert client.get("/v1/names", params={"context_key": key}).json() == {"context_key": key, "names": []}
    r = client.put("/v1/names/entry", params={"context_key": key}, json={"ja": "凛恋", "en": "Rinren", "reading": "りんれん"})
    assert r.status_code == 200, r.text
    assert r.json()["names"] == [{"ja": "凛恋", "en": "Rinren", "reading": "りんれん", "source": "user", "kind": "name", "was": [], "guess": ""}]
    r = client.put("/v1/names/entry", params={"context_key": key}, json={"ja": "凛恋", "en": "Rin"})
    assert [n["en"] for n in r.json()["names"]] == ["Rin"]
    body = {"names": [{"ja": "我愛羅", "en": "Gaara", "reading": "があら", "source": "furigana"}, {"ja": "", "en": "Yuki-chan", "source": "auto"}, {"ja": "渚", "en": "Nagisa"}]}
    r = client.put("/v1/names", params={"context_key": key}, json=body)
    assert [(n["en"], n["source"]) for n in r.json()["names"]] == [("Nagisa", "user"), ("Gaara", "furigana")]
    assert client.get("/v1/names", params={"context_key": "example.com/g/other"}).json()["names"] == []
    assert client.get("/v1/names").json()["error"] == "bad_request"
    assert client.put("/v1/names/entry", params={"context_key": key}, json={"ja": "x", "en": ""}).status_code == 400
    assert client.put("/v1/names", params={"context_key": key}, content=b"not json").json()["error"] == "bad_request"
    assert client.put("/v1/names", params={"context_key": key}, json={"names": [{"en": "A", "source": "nope"}]}).status_code == 400


def ruby_page(lines, ruby, vertical, size=44):
    from make_test_page import FONT_PATH, draw_horizontal_text, draw_vertical_text
    from PIL import Image, ImageFont

    page = Image.new("RGB", (560, 560), (255, 255, 255))
    draw = draw_vertical_text if vertical else draw_horizontal_text
    draw(page, (120, 100), lines, ImageFont.truetype(FONT_PATH, size), ruby)
    return page


def read_block(page, vertical, box=None):
    import numpy as np

    from yomikae import ocr, ruby
    from yomikae.model import Box, TextBlock

    reader = ocr.load_ocr()
    ink = np.asarray(page.convert("L")) < 128
    if box is None:
        ys, xs = np.nonzero(ink)
        box = Box(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    mask_box = box.expand(12, *page.size)
    mask = np.where(ink[mask_box.y0 : mask_box.y1, mask_box.x0 : mask_box.x1], 255, 0).astype(np.uint8)
    image = np.asarray(page)
    bubbles, _ = ocr.read(reader, image, [TextBlock(box, vertical, mask_box, mask)])
    [bubble] = ruby.attach(reader, image, bubbles)
    return bubble, box


def test_furigana_beside_a_kanji_name_is_read_and_kept_out_of_the_line():
    bubble, _ = read_block(ruby_page(["桜野さんは", "どこですか"], {0: [(0, 2, "さくらの")]}, True), True)
    assert "さくら" not in bubble.source, bubble.source
    assert bubble.ruby == (("桜野", "さくらの"),), (bubble.source, bubble.ruby)
    assert read_block(ruby_page(["桜野さんは", "どこですか"], {}, True), True)[0].ruby == ()


def test_furigana_above_horizontal_text_is_read_and_two_plain_lines_are_not_ruby():
    bubble, box = read_block(ruby_page(["明日は桜野さん", "と会うよ"], {0: [(3, 2, "さくらの")]}, False), False)
    assert "さくら" not in bubble.source, bubble.source
    assert bubble.ruby == (("桜野", "さくらの"),), (bubble.source, bubble.ruby)
    assert read_block(ruby_page(["明日は桜野さん", "と会うよ"], {}, False), False, box)[0].ruby == ()
    assert read_block(ruby_page(["かな", "とても小さな字"], {}, False, 30), False)[0].ruby == ()


def test_following_pages_reach_the_prompt_only_when_given():
    store = ContextStore()
    translator = Recorder(lambda lines: ["Hi."] * len(lines))
    asyncio.run(translate_page(translator, store, ["こんにちは"], "g/1", 1))
    asyncio.run(translate_page(translator, store, ["こんにちは"], "g/1", 2, upcoming=[["レーネ、行くぞ"]]))
    plain, ahead = (build_messages(["こんにちは"], c) for c in translator.contexts)
    assert "next_pages" not in plain[0]["content"] and "next_pages" not in plain[1]["content"]
    assert '"next_pages"' in ahead[0]["content"]
    assert json.loads(ahead[1]["content"])["next_pages"] == [["レーネ、行くぞ"]]


def test_filler_pages_are_skipped_as_earlier_context_and_older_story_pages_fill_in():
    store = ContextStore()
    store.add("g/1", "p1", 1, [ContextLine("おはよう", "Morning.")])
    store.add("g/1", "p2", 2, [ContextLine("第二話", "Chapter 2")], filler=True)
    store.add("g/1", "p3", 3, [ContextLine("行くぞ", "Let's go.")])
    store.add("g/1", "p4", 4, [ContextLine("作者", "Author")], filler=True)
    assert store.before("g/1", 5) == [[ContextLine("おはよう", "Morning.")], [ContextLine("行くぞ", "Let's go.")]]
    assert store.recall("g/1", ["第二話"]) == ["Chapter 2"]


def test_an_auto_name_carries_the_japanese_right_before_its_honorific():
    store = ContextStore()
    lines = [
        ContextLine("田中先生、おはよう", "Morning, Tanaka-sensei."),
        ContextLine("おはようゆきちゃん", "Morning, Yuki-chan."),
        ContextLine("レーナちゃん！", "Lena-chan!"),
        ContextLine("宏也落ち着け", "Hiroya-kun, calm down."),
        ContextLine("宏也くん", "Hiroya-kun!"),
        ContextLine("美咲さまと凛恋さま", "Misaki-sama and Riren-sama."),
        ContextLine("ねえ", "Hey, Kenta-kun."),
    ]
    store.add("g/1", "p1", 1, lines)
    assert {e.en: (e.ja, e.guess) for e in store.cast("g/1")} == {
        "Tanaka": ("田中先生、おはよう", "田中"),
        "Yuki": ("おはようゆきちゃん", "ゆき"),
        "Lena": ("レーナちゃん！", "レーナ"),
        "Hiroya": ("宏也落ち着け", "宏也"),
        "Misaki": ("美咲さまと凛恋さま", ""),
        "Riren": ("美咲さまと凛恋さま", ""),
        "Kenta": ("ねえ", ""),
    }


def test_an_auto_name_stays_hidden_once_its_character_is_saved_under_any_spelling(tmp_path):
    store = ContextStore(tmp_path)
    store.add("g/1", "p1", 1, [ContextLine("凛恋ちゃん、おはよう", "Morning, Riren-chan."), ContextLine("ユキちゃんと凛恋ちゃん", "Yuki-chan and Riren-chan!")])
    store.learn_names("g/1", [CastEntry("凛恋", "Riren", "りれん", "furigana")])
    assert [(e.en, e.source) for e in store.cast("g/1")] == [("Riren", "furigana"), ("Yuki", "auto")]
    store.save_names("g/1", [CastEntry("凛恋", "Lily", "りれん", "user")])
    assert [(e.en, e.source, e.was) for e in store.cast("g/1")] == [("Lily", "user", ("Riren",)), ("Yuki", "auto", ())]
    store.save_names("g/1", [CastEntry("凛恋", "Lilia", "りれん", "user")])
    assert [(e.ja, e.en, e.was) for e in ContextStore(tmp_path).saved_names("g/1")] == [("凛恋", "Lilia", ("Riren", "Lily"))]
    translator = Recorder(lambda lines: ["Hi."] * len(lines))
    asyncio.run(translate_page(translator, store, ["こんにちは"], "g/1", 2))
    assert translator.contexts[0].names == {"Lilia": "凛恋", "Yuki": "ユキちゃんと凛恋ちゃん"}
    for i in range(12):
        store.save_names("g/1", [CastEntry("凛恋", f"Name{i}", "りれん", "user")])
    assert store.saved_names("g/1")[0].was == tuple(f"Name{i}" for i in range(3, 11))


def test_an_auto_name_whose_japanese_or_reading_matches_a_saved_name_is_hidden():
    store = ContextStore()
    store.add("g/1", "p1", 1, [ContextLine("凛恋さま、どうぞ", "Here, Rinko-sama."), ContextLine("リレンちゃん！", "Riren-chan!")])
    store.save_names("g/1", [CastEntry("凛恋", "Lily", "", "user")])
    assert [(e.en, e.source) for e in store.cast("g/1")] == [("Lily", "user"), ("Riren", "auto")]
    store.save_names("g/1", [CastEntry("在藤", "Arifuji", "りれん", "user")])
    assert [(e.en, e.source) for e in store.cast("g/1")] == [("Arifuji", "user"), ("Rinko", "auto")]


def test_a_saved_name_without_japanese_keeps_its_old_spellings_and_blocks_a_twin_from_furigana(tmp_path):
    store = ContextStore(tmp_path)
    store.add("g/1", "p1", 1, [ContextLine("おい", "Hey, Riren-chan.")])
    store.save_names("g/1", [CastEntry("", "Rirenx", "", "user", was=("Riren",))])
    store.learn_names("g/1", [CastEntry("凛恋", "Riren", "りれん", "furigana")])
    assert [(e.ja, e.en, e.source, e.was) for e in store.cast("g/1")] == [("", "Rirenx", "user", ("Riren",))]
    store.names_path("g/2").write_text('{"names": [{"ja": "渚", "en": "Nagisa", "source": "user"}]}')
    assert store.saved_names("g/2")[0].was == ()


def test_renaming_flipping_deleting_and_adding_never_shows_one_character_twice(client):
    key = "example.com/g/rename"
    client.app.state.engine.context.add(key, "rename-1", 1, [ContextLine("凛恋ちゃん、おはよう", "Morning, Riren-chan."), ContextLine("ユキちゃん！", "Yuki-chan!")])

    def put(rows):
        r = client.put("/v1/names", params={"context_key": key}, json={"names": rows})
        assert r.status_code == 200, r.text
        return [(n["source"], n["guess"] if n["source"] == "auto" else n["ja"], n["en"], n["kind"], n["was"]) for n in r.json()["names"]]

    yuki = ("auto", "ユキ", "Yuki", "name", [])
    assert put([]) == [("auto", "凛恋", "Riren", "name", []), yuki]
    assert put([{"ja": "凛恋", "en": "Rirenx", "source": "user", "was": ["Riren"]}]) == [("user", "凛恋", "Rirenx", "name", ["Riren"]), yuki]
    client.app.state.engine.context.learn_names(key, [CastEntry("凛恋", "Riren", "りれん", "furigana")])
    assert put([{"ja": "凛恋", "en": "Lily", "reading": "りれん"}]) == [("user", "凛恋", "Lily", "name", ["Riren", "Rirenx"]), yuki]
    assert put([{"ja": "凛恋", "en": "Lily", "kind": "term"}]) == [("user", "凛恋", "Lily", "term", ["Riren", "Rirenx"]), yuki]
    assert put([{"ja": "凛恋", "en": "Lily", "kind": "term"}, {"ja": "", "en": "Riren"}]) == [("user", "凛恋", "Riren", "name", ["Rirenx", "Lily"]), yuki]
    r = client.put("/v1/names/entry", params={"context_key": key}, json={"ja": "", "en": "Lily"})
    assert [(n["ja"], n["en"], n["was"]) for n in r.json()["names"] if n["source"] == "user"] == [("凛恋", "Lily", ["Rirenx", "Riren"])]
    assert put([]) == [("auto", "凛恋", "Riren", "name", []), yuki]
    assert client.put("/v1/names", params={"context_key": key}, json={"names": [{"en": "A", "was": "B"}]}).status_code == 400


def test_a_line_of_only_punctuation_keeps_its_symbols_and_never_reaches_the_translator():
    store = ContextStore()
    store.add("g/1", "p1", 1, [ContextLine("天文部の夏合宿", "with our Astronomy Club.")])
    sent = []
    translator = Recorder(lambda lines: sent.append(list(lines)) or ["with our Astronomy Club."] * len(lines))
    out = asyncio.run(translate_page(translator, store, ["こんにちは", "？", "．．．", "！？", "ー‼"], "g/1", 2))
    assert out[1:] == ["?", "...", "!?", "-!!"]
    assert sent == [["こんにちは"]]
