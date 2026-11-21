from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import replace

import pytest

from yomikae.cache import ContextStore, PageCache, prune_stale
from yomikae.model import ContextLine
from yomikae.pipeline import PIPELINE_VERSION, Engine

from conftest import png_bytes

LEGACY = "ab" * 32


def page(key: str | None, index: int | None, *lines: tuple[str, str]) -> dict:
    return {"context_key": key, "page_index": index, "bubbles": [{"source": s, "translation": t} for s, t in lines]}


def stored(cache: PageCache, page_id: str, result: dict, size: int = 1000, read_at: float | None = None) -> None:
    cache.store(page_id, "rendered.webp", b"w" * size, {**result, "page_id": page_id})
    if read_at is not None:
        os.utime(cache.root / page_id / "result.json", (read_at, read_at))


def test_startup_prunes_only_older_versions_and_legacy_hash_folders(tmp_path):
    current = tmp_path / f"v{PIPELINE_VERSION}"
    keep = [current, tmp_path / f"v{PIPELINE_VERSION + 1}", tmp_path / "vfoo", tmp_path / ("ab" * 31 + "a"), tmp_path / "reports", tmp_path / ("AB" * 32)]
    gone = [tmp_path / f"v{PIPELINE_VERSION - 1}", tmp_path / "v1", tmp_path / LEGACY]
    for d in keep + gone:
        (d / "x").mkdir(parents=True)
    (tmp_path / ("cd" * 32)).write_bytes(b"a file, not a folder")
    target = tmp_path / "elsewhere" / "v0"
    target.mkdir(parents=True)
    (tmp_path / ("ef" * 32)).symlink_to(target)

    assert prune_stale(current, translation_version=1) == len(gone)

    assert all(not d.exists() for d in gone), "an older version or legacy hash folder survived"
    assert all((d / "x").exists() for d in keep), "a folder outside the stale patterns was touched"
    assert (tmp_path / ("cd" * 32)).is_file() and target.exists(), "a hash-named file or a symlink target was touched"


def test_a_version_bump_keeps_the_line_memory_but_serves_no_old_page(tmp_path):
    old = PageCache(tmp_path / "v1", "fake")
    stored(old, "p1", page("work", 1, ("こんにちは", "Hello"), ("さようなら", "Goodbye")))
    stored(old, "p2", page("other", 1, ("はい", "Yes")))

    assert prune_stale(tmp_path / "v2", translation_version=1) == 1

    new = PageCache(tmp_path / "v2", "fake")
    store = ContextStore()
    store.rebuild(new)
    assert store.recall("work", ["こんにちは", "さようなら", "なに"]) == ["Hello", "Goodbye", None]
    assert store.recall("other", ["はい"]) == ["Yes"]
    assert new.load("p1") is None, "a page rendered by the old pipeline was served"
    assert not (tmp_path / "v1").exists()


def test_a_version_bump_drops_memory_made_by_older_translations_or_another_translator(tmp_path):
    stored(PageCache(tmp_path / "v1", "fake"), "p1", page("work", 1, ("こんにちは", "Hello")))
    stored(PageCache(tmp_path / "v2", "other"), "p2", page("work", 2, ("はい", "Yes")))

    prune_stale(tmp_path / "v3", translation_version=2)

    store = ContextStore()
    store.rebuild(PageCache(tmp_path / "v3", "fake"))
    assert store.recall("work", ["こんにちは", "はい"]) == [None, None]


def test_a_version_bump_carries_the_newest_memory_of_a_page_and_keeps_its_last_read_time(tmp_path):
    stored(PageCache(tmp_path / "v1", "fake"), "p1", page("work", 1, ("こんにちは", "Hi")))
    stored(PageCache(tmp_path / "v2", "fake"), "p1", page("work", 1, ("こんにちは", "Hello")), read_at=1000.0)
    stored(PageCache(tmp_path / "v3", "fake"), "p2", page("work", 2, ("こんにちは", "Hey")))

    prune_stale(tmp_path / "v3", translation_version=1)

    store = ContextStore()
    store.rebuild(PageCache(tmp_path / "v3", "fake"))
    assert [line.translation for line in store.memory("work")] == ["Hello", "Hey"]
    assert (tmp_path / "v3" / "p1" / "result.json").stat().st_mtime == 1000.0


def test_eviction_under_a_tiny_cap_drops_the_least_recently_read_pages(tmp_path):
    cache = PageCache(tmp_path / "v1", "fake")
    for i, page_id in enumerate(["a", "b", "c", "d"]):
        stored(cache, page_id, page(None, i), read_at=1000 + i)

    evicted = cache.evict(max_bytes=2500)

    assert sorted(r["page_index"] for r in evicted) == [0, 1]
    assert [p for p in "abcd" if cache.load(p) is not None] == ["c", "d"]
    assert not cache.over(2500)


def test_a_cache_hit_refreshes_recency(tmp_path):
    cache = PageCache(tmp_path / "v1", "fake")
    for i, page_id in enumerate(["a", "b", "c"]):
        stored(cache, page_id, page(None, i), read_at=1000 + i)

    assert cache.load("a") is not None
    cache.evict(max_bytes=2500)

    assert [p for p in "abc" if cache.load(p) is not None] == ["a", "c"]


def test_eviction_never_removes_a_page_mid_write(tmp_path):
    cache = PageCache(tmp_path / "v1", "fake")
    for i, page_id in enumerate(["a", "b"]):
        stored(cache, page_id, page(None, i), read_at=1000 + i)
    cache.writing.add("a")

    cache.evict(max_bytes=0)

    assert (tmp_path / "v1" / "a" / "result.json").exists()


def test_context_drops_evicted_pages_and_matches_a_rebuild(tmp_path):
    cache = PageCache(tmp_path / "v1", "fake")
    context = ContextStore()
    pages = [
        page("work", 0, ("一", "First page")),
        page("work", 1, ("二", "Kenta-kun, wait")),
        page("work", 2, ("三", "Third page"), ("空", "")),
        page("other", None, ("四", "Elsewhere")),
    ]
    for i, result in enumerate(pages):
        stored(cache, f"p{i}", result, read_at=1000 + i)
        context.add(result["context_key"], f"p{i}", result["page_index"], [ContextLine(b["source"], b["translation"]) for b in result["bubbles"]])

    for result in cache.evict(max_bytes=2500):
        context.forget(result)

    fresh = ContextStore()
    fresh.rebuild(cache)
    assert context.pages == fresh.pages
    assert [line.translation for line in context.memory("work")] == ["Third page"]
    assert context.names("work") == {}
    assert context.recall("work", ["一"]) == [None]


def test_delete_cache_clears_the_current_version_and_its_context(client, tmp_path, monkeypatch):
    engine = client.app.state.engine
    cache = PageCache(tmp_path / f"v{PIPELINE_VERSION}", "fake")
    monkeypatch.setattr(engine, "cache", cache)
    monkeypatch.setattr(engine, "context", ContextStore())
    for i in range(3):
        stored(cache, f"{i:064x}", page("work", i, ("原", f"line {i}")))
    engine.context.rebuild(cache)

    r = client.delete("/v1/cache")

    assert r.status_code == 200, r.text
    assert r.json() == {"removed": 3}
    assert cache.page_dirs() == []
    assert engine.context.memory("work") == []
    assert client.get(f"/v1/pages/{0:064x}/rendered.webp").status_code == 404


@pytest.mark.models
def test_a_new_page_triggers_eviction_after_the_response(client, synthetic_pages, tmp_path, monkeypatch):
    engine = client.app.state.engine
    cache = PageCache(tmp_path / f"v{PIPELINE_VERSION}", "fake")
    monkeypatch.setattr(engine, "cache", cache)
    monkeypatch.setattr(engine, "context", ContextStore())
    monkeypatch.setattr(engine, "settings", replace(engine.settings, cache_max_bytes=1))
    stored(cache, "f" * 64, page("work", 0, ("古", "Old page")), read_at=1000)
    engine.context.rebuild(cache)

    r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(synthetic_pages[0][0]), "image/png")}, data={"context_key": "work", "page_index": "1"})
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 10
    while (cache.root / ("f" * 64)).exists() and time.monotonic() < deadline:
        time.sleep(0.05)

    assert not (cache.root / ("f" * 64)).exists(), "the least recently read page was not evicted"
    assert cache.load(r.json()["page_id"]) is not None, "the page just written was evicted"
    assert "Old page" not in [line.translation for line in engine.context.memory("work")]


def test_punctuation_and_one_character_lines_are_never_recalled():
    context = ContextStore()
    context.add("work", "p1", 1, [ContextLine("？", "during summer break."), ContextLine("え", "Huh?"), ContextLine("はい", "Yes.")])
    assert context.recall("work", ["？", "!?", "え", "はい"]) == [None, None, None, "Yes."]


@pytest.mark.models
def test_pages_of_one_work_keep_their_context_with_the_same_or_no_page_index(client, synthetic_pages, tmp_path, monkeypatch):
    from make_test_page import make_page

    engine = client.app.state.engine
    cache = PageCache(tmp_path / f"v{PIPELINE_VERSION}", "fake")
    monkeypatch.setattr(engine, "cache", cache)
    monkeypatch.setattr(engine, "context", ContextStore())

    def post(seed, index=None):
        data = {"context_key": "work"} | ({"page_index": str(index)} if index is not None else {})
        r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(make_page(seed)[0]), "image/png")}, data=data)
        assert r.status_code == 200, r.text
        return len([b for b in r.json()["bubbles"] if b["translation"]])

    counts = [post(240, 1), post(241, 1)]
    assert len(engine.context.memory("work")) == sum(counts) > counts[0], "a second page at the same index replaced the first"
    counts += [post(242), post(243)]
    assert len(engine.context.memory("work")) == sum(counts), "pages without an index did not add up"

    order = [line.source for line in engine.context.memory("work")]
    data = png_bytes(make_page(242)[0])
    r = client.post(f"/v1/pages/{hashlib.sha256(data).hexdigest()}/retranslate", files={"image": ("p.png", data, "image/png")}, data={"request": json.dumps({"context_key": "work", "bubbles": [0]})})
    assert r.status_code == 200, r.text
    assert [line.source for line in engine.context.memory("work")] == order, "a retranslated page without an index moved in the work"

    fresh = ContextStore()
    fresh.rebuild(cache)
    assert sorted(map(repr, fresh.memory("work"))) == sorted(map(repr, engine.context.memory("work")))


class Second:
    name = "second"

    async def translate(self, lines, context):
        return [f"second translator {i}" for i in range(len(lines))]

    async def health(self):
        return None


@pytest.fixture(scope="module")
def openai_server(client):
    return Engine(replace(client.app.state.engine.settings, translator="openai"))


def rebuilt(cache: PageCache) -> ContextStore:
    context = ContextStore()
    context.rebuild(cache)
    return context


@pytest.mark.models
def test_a_server_with_another_translator_translates_a_cached_page_again(client, openai_server, monkeypatch):
    from make_test_page import make_page

    data = png_bytes(make_page(250)[0])
    r = client.post("/v1/pages", files={"image": ("p.png", data, "image/png")}, data={"context_key": "two translators"})
    assert r.status_code == 200, r.text
    assert rebuilt(client.app.state.engine.cache).memory("two translators"), "the page did not reach its own translator's context"
    assert rebuilt(openai_server.cache).memory("two translators") == [], "another translator's lines reached the context"

    monkeypatch.setattr(openai_server, "translator", Second())
    again = asyncio.run(openai_server.process(data, "two translators", None))
    assert again["cached"] is False, "a page made by another translator was served from the cache"
    assert [b["translation"] for b in again["bubbles"]] == [f"second translator {i}" for i in range(len(again["bubbles"]))]
    assert asyncio.run(openai_server.process(data, "two translators", None))["cached"] is True


@pytest.mark.models
def test_a_page_cached_before_translators_were_recorded_counts_as_an_openai_page(client, openai_server):
    from make_test_page import make_page

    engine = client.app.state.engine
    data = png_bytes(make_page(251)[0])
    r = client.post("/v1/pages", files={"image": ("p.png", data, "image/png")}, data={"context_key": "no translator recorded"})
    assert r.status_code == 200, r.text
    page_id = r.json()["page_id"]
    path = engine.cache.root / page_id / "result.json"
    result = json.loads(path.read_text())
    result.pop("translator", None)
    path.write_text(json.dumps(result, ensure_ascii=False))

    assert openai_server.cache.load(page_id) is not None
    assert rebuilt(openai_server.cache).memory("no translator recorded")
    assert engine.cache.load(page_id) is None, "the fake translator was served a page made before translators were recorded"
    assert rebuilt(engine.cache).memory("no translator recorded") == []


class Counting:
    def __init__(self, inner):
        self.inner, self.name, self.lines = inner, inner.name, 0

    async def translate(self, lines, context):
        self.lines += len(lines)
        return await self.inner.translate(lines, context)

    async def health(self):
        return None


@pytest.mark.models
@pytest.mark.parametrize(("stamped", "after"), [(7, 7), (7, 8)])
def test_a_page_opened_again_after_a_version_bump_reuses_its_translations_unless_they_changed(client, synthetic_pages, tmp_path, monkeypatch, stamped, after):
    import yomikae.pipeline

    engine = client.app.state.engine
    counting = Counting(engine.translator)
    monkeypatch.setattr(engine, "translator", counting)
    monkeypatch.setattr(yomikae.pipeline, "TRANSLATION_VERSION", stamped)
    image = {"image": ("p.png", png_bytes(synthetic_pages[0][0]), "image/png")}
    form = {"context_key": "work", "page_index": "1"}

    def open_page_under(version):
        cache = PageCache(tmp_path / f"v{version}", counting.name)
        monkeypatch.setattr(engine, "cache", cache)
        monkeypatch.setattr(engine, "context", ContextStore())
        engine.context.rebuild(cache)
        r = client.post("/v1/pages", files=image, data=form)
        assert r.status_code == 200, r.text
        return r.json()

    first = open_page_under(1)
    asked = counting.lines
    assert asked > 0
    prune_stale(tmp_path / "v2", translation_version=after)
    again = open_page_under(2)

    assert again["cached"] is False, "the old render was served after the bump"
    assert [b["translation"] for b in again["bubbles"]] == [b["translation"] for b in first["bubbles"]]
    assert counting.lines == (asked if stamped == after else 2 * asked)


@pytest.mark.models
def test_a_line_the_reader_fixed_stays_fixed_through_two_version_bumps(client, synthetic_pages, tmp_path, monkeypatch):
    engine = client.app.state.engine
    image = {"image": ("p.png", png_bytes(synthetic_pages[0][0]), "image/png")}

    def open_page_under(version, key="work"):
        cache = PageCache(tmp_path / f"v{version}", engine.translator.name)
        monkeypatch.setattr(engine, "cache", cache)
        monkeypatch.setattr(engine, "context", ContextStore())
        engine.context.rebuild(cache)
        r = client.post("/v1/pages", files=image, data={"context_key": key, "page_index": "1"})
        assert r.status_code == 200, r.text
        return json.loads((cache.root / r.json()["page_id"] / "result.json").read_text())

    first = open_page_under(1)
    first["bubbles"][0]["translation"], first["fixed"] = "Fixed by the reader", [0]
    (tmp_path / "v1" / first["page_id"] / "result.json").write_text(json.dumps(first))

    for version in (2, 3):
        prune_stale(tmp_path / f"v{version}", translation_version=1)
        again = open_page_under(version)
        assert again["bubbles"][0]["translation"] == "Fixed by the reader"
        assert again["fixed"] == [0], f"the fix lost its priority after bump {version - 1}"


@pytest.mark.models
def test_a_carried_page_opened_in_another_work_leaves_its_old_work(client, synthetic_pages, tmp_path, monkeypatch):
    engine = client.app.state.engine
    image = {"image": ("p.png", png_bytes(synthetic_pages[0][0]), "image/png")}
    for version, key in ((1, "work"), (2, "other")):
        if version == 2:
            prune_stale(tmp_path / "v2", translation_version=1)
        cache = PageCache(tmp_path / f"v{version}", engine.translator.name)
        monkeypatch.setattr(engine, "cache", cache)
        monkeypatch.setattr(engine, "context", ContextStore())
        engine.context.rebuild(cache)
        assert client.post("/v1/pages", files=image, data={"context_key": key, "page_index": "1"}).status_code == 200

    assert engine.context.memory("work") == []
    assert engine.context.memory("other") != []
