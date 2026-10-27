from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import replace

from yomikae.cache import ContextStore, PageCache, prune_stale
from yomikae.model import ContextLine
from yomikae.pipeline import PIPELINE_VERSION

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

    assert prune_stale(current) == len(gone)

    assert all(not d.exists() for d in gone), "an older version or legacy hash folder survived"
    assert all((d / "x").exists() for d in keep), "a folder outside the stale patterns was touched"
    assert (tmp_path / ("cd" * 32)).is_file() and target.exists(), "a hash-named file or a symlink target was touched"


def test_eviction_under_a_tiny_cap_drops_the_least_recently_read_pages(tmp_path):
    cache = PageCache(tmp_path / "v1")
    for i, page_id in enumerate(["a", "b", "c", "d"]):
        stored(cache, page_id, page(None, i), read_at=1000 + i)

    evicted = cache.evict(max_bytes=2500)

    assert sorted(r["page_index"] for r in evicted) == [0, 1]
    assert [p for p in "abcd" if cache.load(p) is not None] == ["c", "d"]
    assert not cache.over(2500)


def test_a_cache_hit_refreshes_recency(tmp_path):
    cache = PageCache(tmp_path / "v1")
    for i, page_id in enumerate(["a", "b", "c"]):
        stored(cache, page_id, page(None, i), read_at=1000 + i)

    assert cache.load("a") is not None
    cache.evict(max_bytes=2500)

    assert [p for p in "abc" if cache.load(p) is not None] == ["a", "c"]


def test_eviction_never_removes_a_page_mid_write(tmp_path):
    cache = PageCache(tmp_path / "v1")
    for i, page_id in enumerate(["a", "b"]):
        stored(cache, page_id, page(None, i), read_at=1000 + i)
    cache.writing.add("a")

    cache.evict(max_bytes=0)

    assert (tmp_path / "v1" / "a" / "result.json").exists()


def test_context_drops_evicted_pages_and_matches_a_rebuild(tmp_path):
    cache = PageCache(tmp_path / "v1")
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
    cache = PageCache(tmp_path / f"v{PIPELINE_VERSION}")
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


def test_a_new_page_triggers_eviction_after_the_response(client, synthetic_pages, tmp_path, monkeypatch):
    engine = client.app.state.engine
    cache = PageCache(tmp_path / f"v{PIPELINE_VERSION}")
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


def test_pages_of_one_work_keep_their_context_with_the_same_or_no_page_index(client, synthetic_pages, tmp_path, monkeypatch):
    from make_test_page import make_page

    engine = client.app.state.engine
    cache = PageCache(tmp_path / f"v{PIPELINE_VERSION}")
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
