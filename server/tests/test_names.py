from __future__ import annotations

import asyncio

from yomikae.cache import ContextStore
from yomikae.model import CastEntry, ContextLine
from yomikae.pipeline import translate_page
from yomikae.ruby import furigana_names, romanize
from yomikae.translate import build_messages


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
    store.add("g/1", 1, [ContextLine("渚姉、待って", "Nagisa-nee, wait!")])
    assert [(e.en, e.ja, e.source) for e in store.cast("g/1")] == [("Nagisa-nee", "渚姉、待って", "auto")]
    translator = Recorder(lambda lines: ["Hi."] * len(lines))
    asyncio.run(translate_page(translator, store, ["こんにちは"], "g/1", 2))
    assert translator.contexts[0].names == store.names("g/1")
    assert translator.contexts[0].readings == {}
    assert "readings" not in build_messages(["こんにちは"], translator.contexts[0])[0]["content"]


def test_a_user_name_hides_the_auto_spelling_it_corrects_and_is_never_overwritten(tmp_path):
    store = ContextStore(tmp_path)
    store.add("g/1", 1, [ContextLine("凛恋さん、おはよう", "Morning, Riren-san."), ContextLine("ユキちゃん", "Yuki-chan!")])
    store.set_name("g/1", CastEntry("凛恋", "Rinren", "りんれん", "user"))
    store.learn_names("g/1", [CastEntry("凛恋", "Rinko", "りんこ", "furigana")])
    cast = store.cast("g/1")
    assert [(e.en, e.source) for e in cast] == [("Rinren", "user"), ("Yuki-chan", "auto")]
    reloaded = ContextStore(tmp_path)
    assert [(e.ja, e.en, e.reading, e.source) for e in reloaded.saved_names("g/1")] == [("凛恋", "Rinren", "りんれん", "user")]
    assert reloaded.saved_names("g/2") == []


def test_a_furigana_name_is_learned_once_and_replaces_a_wrong_auto_spelling():
    store = ContextStore()
    store.add("g/1", 1, [ContextLine("我愛羅くん！", "Gara-kun!")])
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


def test_names_api_reads_and_replaces_one_works_list(client):
    key = "example.com/g/cast"
    assert client.get("/v1/names", params={"context_key": key}).json() == {"context_key": key, "names": []}
    r = client.put("/v1/names/entry", params={"context_key": key}, json={"ja": "凛恋", "en": "Rinren", "reading": "りんれん"})
    assert r.status_code == 200, r.text
    assert r.json()["names"] == [{"ja": "凛恋", "en": "Rinren", "reading": "りんれん", "source": "user"}]
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


def vertical_page(columns, ruby):
    from PIL import Image, ImageDraw, ImageFont

    main = ImageFont.truetype("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", 44)
    small = ImageFont.truetype("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", 18)
    page = Image.new("L", (520, 520), 255)
    draw = ImageDraw.Draw(page)
    right = 380
    for c, column in enumerate(columns):
        cx = right - 80 * c
        for r, ch in enumerate(column):
            draw.text((cx, 90 + 48 * r), ch, font=main, fill=0, anchor="mm")
        for start, kana in ruby.get(c, []):
            top = 90 + 48 * start - 24
            for k, ch in enumerate(kana):
                draw.text((cx + 36, top + 10 + 20 * k), ch, font=small, fill=0, anchor="mm")
    return page


def test_furigana_beside_a_kanji_name_is_read_and_kept_out_of_the_line():
    import numpy as np

    from yomikae import ocr, ruby
    from yomikae.model import Box, TextBlock

    reader = ocr.load_ocr()
    page = vertical_page(["桜野さんは", "どこですか"], {0: [(0, "さくらの")]})
    ink = np.asarray(page) < 128
    ys, xs = np.nonzero(ink)
    box = Box(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    mask_box = box.expand(12, *page.size)
    mask = np.where(ink[mask_box.y0 : mask_box.y1, mask_box.x0 : mask_box.x1], 255, 0).astype(np.uint8)
    image = np.asarray(page.convert("RGB"))
    bubbles, _ = ocr.read(reader, image, [TextBlock(box, True, mask_box, mask)])
    [bubble] = ruby.attach(reader, image, bubbles)
    assert "さくら" not in bubble.source, bubble.source
    assert bubble.ruby == (("桜野", "さくらの"),), (bubble.source, bubble.ruby)
    plain = vertical_page(["桜野さんは", "どこですか"], {})
    ink = np.asarray(plain) < 128
    mask = np.where(ink[mask_box.y0 : mask_box.y1, mask_box.x0 : mask_box.x1], 255, 0).astype(np.uint8)
    image = np.asarray(plain.convert("RGB"))
    assert ruby.attach(reader, image, ocr.read(reader, image, [TextBlock(box, True, mask_box, mask)])[0])[0].ruby == ()
