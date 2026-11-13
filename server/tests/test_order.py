from __future__ import annotations

import asyncio
import time
from contextlib import suppress

import pytest
from conftest import png_bytes
from PIL import Image

from make_test_page import make_page
from yomikae.cache import ContextStore, PageCache
from yomikae.model import ContextLine
from yomikae.pipeline import PIPELINE_VERSION
from yomikae.translate import TranslatorFailed


class Recording:
    name = "recording"

    def __init__(self, before_reply):
        self.before_reply = before_reply
        self.calls = []

    async def translate(self, lines, context):
        self.calls.append((list(lines), context))
        call = len(self.calls)
        await self.before_reply(call)
        return [f"call {call} line {i}" for i in range(len(lines))]

    async def health(self):
        return None


@pytest.fixture
def engine(client, tmp_path, monkeypatch):
    engine = client.app.state.engine
    monkeypatch.setattr(engine, "cache", PageCache(tmp_path / f"v{PIPELINE_VERSION}", "fake"))
    monkeypatch.setattr(engine, "context", ContextStore())
    return engine


def page(seed):
    return png_bytes(make_page(seed)[0])


def pages_adding_a_line_each(count, seed):
    seen, pages = set(), []
    while len(pages) < count:
        img, truth = make_page(seed)
        new = {t.text for t in truth} - seen
        if len(new) == 1 or not pages:
            seen |= new
            pages.append(png_bytes(img))
        seed += 1
    return pages


def lines(result):
    return [ContextLine(b["source"], b["translation"]) for b in result["bubbles"]]


def run(scenario):
    return asyncio.run(asyncio.wait_for(scenario, 120))


async def until_detection_and_ocr_are_done(engine, another_page):
    await engine.process(another_page, None, None)


def holding_the_first_call():
    opened, release = asyncio.Event(), asyncio.Event()

    async def hold_first(call):
        if call == 1:
            opened.set()
            await release.wait()

    return Recording(hold_first), opened, release


def test_a_page_sent_while_the_page_before_it_is_in_flight_gets_it_as_context(engine, monkeypatch):
    async def slow(call):
        await asyncio.sleep(0.3)

    translator = Recording(slow)
    monkeypatch.setattr(engine, "translator", translator)
    pages = pages_adding_a_line_each(6, 900)

    async def read():
        slots = asyncio.Semaphore(2)

        async def send(index, data):
            async with slots:
                return await engine.process(data, "work", index)

        return await asyncio.gather(*(send(index, data) for index, data in enumerate(pages)))

    results = run(read())
    assert len(translator.calls) == len(pages)
    for index in range(1, len(pages)):
        _, context = translator.calls[index]
        assert context.pages[-1:] == [lines(results[index - 1])], f"page {index} was translated without page {index - 1}"


def test_pages_of_different_works_are_translated_at_the_same_time(engine, monkeypatch):
    spans = []

    async def read():
        both = asyncio.Event()

        async def until_both_are_open(call):
            start = time.perf_counter()
            if call == 2:
                both.set()
            with suppress(TimeoutError):
                await asyncio.wait_for(both.wait(), 30)
            spans.append((start, time.perf_counter()))

        monkeypatch.setattr(engine, "translator", Recording(until_both_are_open))
        return await asyncio.gather(engine.process(page(910), "work", 0), engine.process(page(911), "other work", 0))

    results = run(read())
    assert all(b["translation"] for result in results for b in result["bubbles"])
    (first_start, first_end), (second_start, second_end) = spans
    assert max(first_start, second_start) < min(first_end, second_end), "the two calls did not overlap"


def test_a_page_whose_translator_fails_does_not_hold_up_the_next_page_of_its_work(engine, monkeypatch):
    async def fail_first(call):
        await asyncio.sleep(0.3)
        if call == 1:
            raise TranslatorFailed("refused")

    monkeypatch.setattr(engine, "translator", Recording(fail_first))

    async def read():
        return await asyncio.gather(engine.process(page(920), "work", 0), engine.process(page(921), "work", 1), return_exceptions=True)

    failed, translated = run(read())
    assert isinstance(failed, TranslatorFailed)
    assert translated["cached"] is False and all(b["translation"] for b in translated["bubbles"])


def test_cancelling_a_page_that_waits_leaves_the_pages_before_and_after_it_alone(engine, monkeypatch):
    first, second, third = pages_adding_a_line_each(3, 930)
    unrelated = page(940)

    async def read():
        translator, opened, release = holding_the_first_call()
        monkeypatch.setattr(engine, "translator", translator)
        held = asyncio.create_task(engine.process(first, "work", 0))
        await opened.wait()
        waiting = asyncio.create_task(engine.process(second, "work", 1))
        await until_detection_and_ocr_are_done(engine, unrelated)
        waiting.cancel()
        release.set()
        before = await held
        with pytest.raises(asyncio.CancelledError):
            await waiting
        after = await engine.process(third, "work", 2)
        return translator, before, after

    translator, before, after = run(read())
    assert all(b["translation"] for b in before["bubbles"])
    assert after["cached"] is False and all(b["translation"] for b in after["bubbles"])
    assert translator.calls[-1][1].pages == [lines(before)]


def test_the_wait_for_the_page_before_counts_in_the_total_time_and_not_in_translate(engine, monkeypatch):
    first, second = pages_adding_a_line_each(2, 950)
    unrelated = page(960)

    async def read():
        translator, opened, release = holding_the_first_call()
        monkeypatch.setattr(engine, "translator", translator)
        held = asyncio.create_task(engine.process(first, "work", 0))
        await opened.wait()
        sent = time.perf_counter()
        waiting = asyncio.create_task(engine.process(second, "work", 1))
        await until_detection_and_ocr_are_done(engine, unrelated)
        await asyncio.sleep(1)
        assert not waiting.done(), "the page did not wait for the page before it"
        release.set()
        timings = (await waiting)["timings_ms"]
        took = (time.perf_counter() - sent) * 1000
        await held
        return timings, took

    timings, took = run(read())
    assert timings["translate"] < 1000
    assert took - timings["total"] < 500


def test_a_page_behind_a_page_without_text_still_waits_for_the_page_before_both(engine, monkeypatch):
    first, third = pages_adding_a_line_each(2, 970)
    blank = png_bytes(Image.new("RGB", (800, 1100), (255, 255, 255)))
    unrelated = page(980)

    async def read():
        translator, opened, release = holding_the_first_call()
        monkeypatch.setattr(engine, "translator", translator)
        held = asyncio.create_task(engine.process(first, "work", 0))
        await opened.wait()
        await engine.process(blank, "work", 1)
        waiting = asyncio.create_task(engine.process(third, "work", 2))
        await until_detection_and_ocr_are_done(engine, unrelated)
        release.set()
        await waiting
        return translator, await held

    translator, before = run(read())
    assert translator.calls[-1][1].pages == [lines(before)]


class Listed:
    def __init__(self, results):
        self.results = results

    def all_results(self):
        return iter(self.results)


def test_pages_without_a_page_index_keep_their_order_after_a_restart(engine, monkeypatch):
    async def at_once(call):
        pass

    monkeypatch.setattr(engine, "translator", Recording(at_once))

    async def read():
        return [await engine.process(page(seed), "work", None) for seed in range(990, 994)]

    results = run(read())
    live = engine.context.before("work", None)
    assert live == [lines(result) for result in results[-2:]]
    on_disk = list(engine.cache.all_results())
    for listing in (on_disk, on_disk[::-1]):
        restarted = ContextStore()
        restarted.rebuild(Listed(listing))
        assert restarted.before("work", None) == live, "a restart changed which pages come before a new page"
