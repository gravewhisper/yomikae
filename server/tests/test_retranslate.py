from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager

import numpy as np
from conftest import decode, png_bytes

from make_test_page import make_page
from yomikae.cache import ContextStore, PageCache
from yomikae.pipeline import stale_bubbles
from yomikae.translate import Context, build_messages


class Scripted:
    name = "scripted"

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def translate(self, lines, context):
        self.calls.append((list(lines), context))
        return self.reply(len(self.calls), lines, context)

    async def health(self):
        return None


@contextmanager
def using(client, translator):
    engine = client.app.state.engine
    original = engine.translator
    engine.translator = translator
    try:
        yield
    finally:
        engine.translator = original


def page(seed):
    return png_bytes(make_page(seed)[0])


def post(client, data, key, idx):
    r = client.post("/v1/pages", files={"image": ("p.png", data, "image/png")}, data={"context_key": key, "page_index": str(idx)})
    assert r.status_code == 200, r.text
    return r.json()


def retranslate(client, data, body, page_id=None):
    page_id = page_id or hashlib.sha256(data).hexdigest()
    return client.post(f"/v1/pages/{page_id}/retranslate", files={"image": ("p.png", data, "image/png")}, data={"request": json.dumps(body)})


def by_call(call, lines, _):
    return [f"call {call} line {i}" for i in range(len(lines))]


def region_change(before, after, box):
    x0, y0, x1, y1 = box
    return np.abs(after[y0:y1, x0:x1].astype(int) - before[y0:y1, x0:x1].astype(int)).mean()


def test_only_the_picked_bubbles_get_new_english_and_the_page_is_replaced(client):
    key = "example.com/g/retranslate"
    data = page(601)
    translator = Scripted(by_call)
    with using(client, translator):
        first = post(client, data, key, 0)
        assert len(first["bubbles"]) >= 3
        before = decode(client.get(first["rendered_url"]).content)
        r = retranslate(client, data, {"context_key": key, "page_index": 0, "bubbles": [1], "hint": "she is a girl"})
    assert r.status_code == 200, r.text
    second = r.json()
    lines, context = translator.calls[-1]
    assert lines == [first["bubbles"][1]["source"]]
    assert context.hint == "she is a girl"
    assert [(c.source, c.translation) for c in context.done] == [(b["source"], b["translation"]) for i, b in enumerate(first["bubbles"]) if i != 1]
    assert second["cached"] is False
    assert [b["translation"] for b in second["bubbles"]] == [b["translation"] if i != 1 else "call 2 line 0" for i, b in enumerate(first["bubbles"])]
    assert second["rendered_url"] != first["rendered_url"] and second["rendered_url"].endswith("?v=1")
    assert not {"names_in_force", "revision", "fixed", "context_key"} & second.keys()

    after = decode(client.get(second["rendered_url"]).content)
    assert region_change(before, after, second["bubbles"][1]["box"]) > 3, "the picked bubble was not re-lettered"
    for i, b in enumerate(second["bubbles"]):
        if i != 1:
            assert region_change(before, after, b["box"]) < 1, f"bubble {i} changed"

    again = post(client, data, key, 0)
    assert again["cached"] is True and again["rendered_url"] == second["rendered_url"]
    assert again["bubbles"] == second["bubbles"]
    with using(client, translator):
        third = retranslate(client, data, {"context_key": key, "page_index": 0, "bubbles": [0, 2]}).json()
    assert third["rendered_url"].endswith("?v=2")
    assert translator.calls[-1][1].hint == ""
    engine = client.app.state.engine
    stored = [p.read_bytes() for p in engine.settings.cache_dir.rglob("*") if p.is_file()]
    assert not any("she is a girl".encode() in b for b in stored), "the hint was stored"


def test_a_retranslated_line_skips_recall_and_is_what_later_pages_recall(client):
    key = "example.com/g/retranslate-memory"
    translator = Scripted(lambda call, lines, _: [f"fixed {i}" if call == 3 else f"call {call} line {i}" for i in range(len(lines))])
    with using(client, translator):
        first = post(client, page(206), key, 0)
        second = post(client, page(403), key, 1)
        earlier = {b["source"]: b["translation"] for b in first["bubbles"]}
        shared = next(i for i, b in enumerate(second["bubbles"]) if b["source"] in earlier)
        source = second["bubbles"][shared]["source"]
        assert second["bubbles"][shared]["translation"] == earlier[source]
        r = retranslate(client, page(403), {"context_key": key, "page_index": 1, "bubbles": [shared]})
    assert r.status_code == 200, r.text
    assert translator.calls[-1][0] == [source], "the picked line was recalled instead of translated"
    assert r.json()["bubbles"][shared]["translation"] == "fixed 0"
    engine = client.app.state.engine
    assert engine.context.recall(key, [source]) == ["fixed 0"]
    rebuilt = ContextStore()
    rebuilt.rebuild(engine.cache)
    assert rebuilt.recall(key, [source]) == ["fixed 0"]


def test_names_sent_with_a_retranslate_are_stored_as_user_names_first(client):
    key = "example.com/g/retranslate-names"
    data = page(603)
    translator = Scripted(by_call)
    with using(client, translator):
        post(client, data, key, 0)
        r = retranslate(client, data, {"context_key": key, "page_index": 0, "bubbles": [0], "names": [{"ja": "在藤", "en": "Arifuji", "kind": "name"}]})
    assert r.status_code == 200, r.text
    assert translator.calls[-1][1].names == {"Arifuji": "在藤"}
    names = client.get("/v1/names", params={"context_key": key}).json()["names"]
    assert names == [{"ja": "在藤", "en": "Arifuji", "reading": "", "source": "user", "kind": "name"}]


def test_a_page_not_in_the_cache_is_translated_fully_first(client):
    key = "example.com/g/retranslate-cold"
    data = page(604)
    translator = Scripted(by_call)
    with using(client, translator):
        r = retranslate(client, data, {"context_key": key, "page_index": 0, "bubbles": [0]})
    assert r.status_code == 200, r.text
    assert len(translator.calls) == 2 and len(translator.calls[1][0]) == 1
    body = r.json()
    assert body["bubbles"][0]["translation"] == "call 2 line 0"
    assert [b["translation"] for b in body["bubbles"][1:]] == [f"call 1 line {i}" for i in range(1, len(body["bubbles"]))]


def test_retranslate_rejects_malformed_requests(client):
    data = page(601)
    good = {"context_key": "example.com/g/retranslate", "page_index": 0, "bubbles": [0]}
    bad = [
        retranslate(client, data, good, page_id="0" * 64),
        retranslate(client, data, {**good, "bubbles": []}),
        retranslate(client, data, {**good, "bubbles": [99]}),
        retranslate(client, data, {**good, "bubbles": ["1"]}),
        retranslate(client, data, {**good, "hint": "x" * 301}),
        retranslate(client, data, {**good, "names": [{"ja": "a", "en": f"N{i}"} for i in range(21)]}),
        retranslate(client, data, {**good, "names": [{"ja": "a", "en": ""}]}),
        retranslate(client, data, {"bubbles": [0], "names": [{"ja": "a", "en": "A"}]}),
        client.post(f"/v1/pages/{hashlib.sha256(data).hexdigest()}/retranslate", files={"image": ("p.png", data, "image/png")}, data={"request": "not json"}),
    ]
    for r in bad:
        assert r.status_code == 400 and r.json()["error"] == "bad_request", r.text
    assert retranslate(client, data, {**good, "hint": "x" * 300}).status_code == 200


def test_a_name_change_redoes_only_the_bubbles_that_use_it_when_the_page_is_viewed(client):
    key = "example.com/g/retranslate-stale"
    _, truth = make_page(605)
    ja = truth[0].text[:2]
    other = next(s for s in range(606, 640) if not any(ja in t.text for t in make_page(s)[1]))

    def reply(call, lines, context):
        spelling = next((en for en, j in context.names.items() if j == ja), "Zaito")
        return [f"{spelling}-san, look {call}.{i}" if ja in line else f"plain {call}.{i}" for i, line in enumerate(lines)]

    translator = Scripted(reply)
    data = page(605)
    with using(client, translator):
        first = post(client, data, key, 0)
        untouched = post(client, page(other), key, 1)
        uses = [i for i, b in enumerate(first["bubbles"]) if ja in b["source"]]
        assert uses and any("Zaito" in first["bubbles"][i]["translation"] for i in uses)
        r = client.put("/v1/names/entry", params={"context_key": key}, json={"ja": ja, "en": "Arifuji"})
        assert r.status_code == 200
        redone = post(client, data, key, 0)
        assert redone["updated"] is True and redone["cached"] is False
        assert redone["rendered_url"] != first["rendered_url"]
        for i, b in enumerate(redone["bubbles"]):
            if i in uses:
                assert b["translation"].startswith("Arifuji-san"), b
            else:
                assert b["translation"] == first["bubbles"][i]["translation"], b
        calls = len(translator.calls)
        seen = post(client, data, key, 0)
        assert seen["cached"] is True and "updated" not in seen
        later = post(client, page(other), key, 1)
        assert later["cached"] is True and "updated" not in later and later["bubbles"] == untouched["bubbles"]
        assert len(translator.calls) == calls

        client.put("/v1/names", params={"context_key": key}, json={"names": [{"ja": "", "en": "Arifujii"}]})
        respelled = post(client, data, key, 0)
    assert respelled["updated"] is True, "a bubble that still holds a removed spelling must be redone"


def test_which_bubbles_a_name_change_makes_stale():
    bubbles = [
        {"source": "在藤さん", "translation": "Zaito-san!"},
        {"source": "行くぞ", "translation": "Let's go, Ann."},
        {"source": "行こう", "translation": "Come on, Anna."},
        {"source": "在藤だ", "translation": "It's Arifuji."},
    ]
    page = {"context_key": "k", "bubbles": bubbles, "names_in_force": [{"ja": "", "en": "Ann"}]}
    assert stale_bubbles(page, [{"ja": "", "en": "Ann"}]) == []
    assert stale_bubbles(page, [{"ja": "", "en": "Ann"}, {"ja": "在藤", "en": "Arifuji"}]) == [0]
    assert stale_bubbles(page, [{"ja": "", "en": "Anne"}]) == [1]
    assert stale_bubbles(page, []) == [1]
    assert stale_bubbles({**page, "names_in_force": None}, [{"ja": "在藤", "en": "Arifuji"}]) == []
    assert stale_bubbles({**page, "context_key": None}, [{"ja": "在藤", "en": "Arifuji"}]) == []


def test_the_hint_reaches_the_prompt_only_when_given():
    plain = build_messages(["行け"], Context())
    hinted = build_messages(["行け"], Context(hint="Lene is a girl"))
    assert "hint" not in plain[0]["content"] and "hint" not in plain[1]["content"]
    assert '"hint"' in hinted[0]["content"] and json.loads(hinted[1]["content"])["hint"] == "Lene is a girl"


def test_replacing_a_cached_page_keeps_the_cache_size_exact(tmp_path):
    cache = PageCache(tmp_path / "v1")
    cache.store("a" * 64, "rendered.webp", b"x" * 1000, {"bubbles": []})
    cache.store("a" * 64, "rendered.webp", b"x" * 400, {"bubbles": [], "revision": 1})
    assert cache.size == sum(f.stat().st_size for f in (tmp_path / "v1" / ("a" * 64)).iterdir())
