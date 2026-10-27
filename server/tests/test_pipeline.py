from __future__ import annotations

import io
from dataclasses import replace

import httpx
import numpy as np
import pytest
import torch
from conftest import decode, png_bytes
from PIL import Image

from make_test_page import make_page
from yomikae.config import Settings
from yomikae.translate import OpenAICompatible


def iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def matched(truth, bubbles):
    return [(t, max(bubbles, key=lambda b: iou(b["box"], t["text_box"]))) for t in truth]


def test_detects_every_synthetic_bubble(translated):
    for _, _, truth, page in translated.values():
        truth = [t.__dict__ for t in truth]
        assert len(page["bubbles"]) == len(truth)
        for t, b in matched(truth, page["bubbles"]):
            assert iou(b["box"], t["text_box"]) > 0.7, (t, b)
            assert b["vertical"] is True


def test_ocr_reads_the_drawn_text(translated):
    for _, _, truth, page in translated.values():
        for t, b in matched([t.__dict__ for t in truth], page["bubbles"]):
            assert edit_distance(b["source"], t["text"]) <= 1, (b["source"], t["text"])
            assert b["translation"]


def test_original_text_is_removed_and_english_drawn(client, translated):
    for data, img, truth, page in translated.values():
        rendered = decode(client.get(page["rendered_url"]).content)
        assert rendered.shape[:2] == (page["height"], page["width"])
        original = np.asarray(img.convert("L"))
        out = np.asarray(Image.fromarray(rendered).convert("L"))
        for t in truth:
            x0, y0, x1, y1 = t.text_box
            ink = original[y0:y1, x0:x1] < 128
            still_dark = out[y0:y1, x0:x1] < 128
            on_ink = still_dark[ink].mean()
            elsewhere = still_dark[~ink].mean()
            assert on_ink < 0.35 and on_ink < elsewhere + 0.15, (t.text, on_ink, elsewhere)
            ex0, ey0, ex1, ey1 = t.ellipse
            inner = out[ey0 + 20 : ey1 - 20, ex0 + 20 : ex1 - 20]
            assert (inner < 128).mean() > 0.01, f"no English drawn in bubble {t.text}"


def test_repost_is_served_from_cache(client, translated):
    data, _, _, first = translated[0]
    r = client.post("/v1/pages", files={"image": ("p.png", data, "image/png")})
    assert r.status_code == 200
    again = r.json()
    assert again["cached"] is True and first["cached"] is False
    assert again["page_id"] == first["page_id"]
    assert again["bubbles"] == first["bubbles"]
    assert again["timings_ms"]["total"] == 0


def test_cache_is_scoped_to_the_pipeline_version(client, translated):
    from yomikae.pipeline import PIPELINE_VERSION

    _, _, _, page = translated[0]
    root = client.app.state.engine.settings.cache_dir
    assert (root / f"v{PIPELINE_VERSION}" / page["page_id"] / "rendered.webp").exists()
    assert not (root / page["page_id"]).exists()


def test_page_without_text_returns_unchanged_copy(client):
    blank = Image.new("RGB", (800, 1100), (255, 255, 255))
    r = client.post("/v1/pages", files={"image": ("b.png", png_bytes(blank), "image/png")})
    assert r.status_code == 200
    page = r.json()
    assert page["bubbles"] == []
    rendered = decode(client.get(page["rendered_url"]).content)
    assert rendered.shape == (1100, 800, 3)
    assert rendered.min() > 245


def test_bad_image_error_shape(client):
    r = client.post("/v1/pages", files={"image": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_image"
    r = client.post("/v1/pages", data={"context_key": "k"})
    assert r.status_code == 400 and r.json()["error"] == "bad_image"


def test_unreachable_translator_yields_translator_unavailable(client):
    engine = client.app.state.engine
    original = engine.translator
    engine.translator = OpenAICompatible(replace(Settings.from_env(), openai_base_url="http://127.0.0.1:9/v1"))
    try:
        img, _ = make_page(101)
        data = png_bytes(img)
        r = client.post("/v1/pages", files={"image": ("p.png", data, "image/png")})
        assert r.status_code == 503
        body = r.json()
        assert body["error"] == "translator_unavailable" and body["detail"]
        health = client.get("/v1/health").json()
        assert health["ok"] is False and health["detail"]
    finally:
        engine.translator = original
    r = client.post("/v1/pages", files={"image": ("p.png", data, "image/png")})
    assert r.status_code == 200 and r.json()["cached"] is False, "a translator outage must not be cached"


def test_translator_sees_earlier_pages_of_same_work(client):
    engine = client.app.state.engine
    original = engine.translator
    seen = []

    class Recording:
        name = "recording"

        async def translate(self, lines, context):
            seen.append(context)
            return [f"page line {i}" for i in range(len(lines))]

        async def health(self):
            return None

    engine.translator = Recording()
    try:
        for idx, seed in enumerate((201, 202, 203, 204)):
            data = png_bytes(make_page(seed)[0])
            r = client.post(
                "/v1/pages",
                files={"image": ("p.png", data, "image/png")},
                data={"context_key": "example.com/g/1", "page_index": str(idx)},
            )
            assert r.status_code == 200
    finally:
        engine.translator = original
    assert seen[0].pages == []
    assert len(seen[1].pages) == 1 and len(seen[3].pages) == 2
    assert all(line.translation.startswith("page line") for page in seen[3].pages for line in page)


def test_english_print_the_ocr_misreads_keeps_its_original_pixels(client):
    from yomikae.config import FONT_PATH
    from PIL import ImageDraw, ImageFont

    img, truth = make_page(5)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT_PATH), 26)
    notice = (60, img.height - 220, 700, img.height - 80)
    draw.rectangle(notice, fill="white", outline="black", width=3)
    for i, line in enumerate(["When you dispose of it, please discard it", "as burnable garbage and treat it as though", "you do not know the contents. No reprints."]):
        draw.text((80, notice[1] + 20 + i * 32), line, font=font, fill="black")

    r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(img), "image/png")})
    assert r.status_code == 200, r.text
    page = r.json()
    assert len(page["bubbles"]) == len(truth), [b["source"] for b in page["bubbles"]]
    assert len(client.app.state.engine.cache.load(page["page_id"])["skipped"]) == 1, "the notice was not detected and gated"
    before = np.asarray(img.convert("L")).astype(int)
    after = np.asarray(Image.fromarray(decode(client.get(page["rendered_url"]).content)).convert("L")).astype(int)
    x0, y0, x1, y1 = notice
    assert np.abs(after[y0:y1, x0:x1] - before[y0:y1, x0:x1]).mean() < 2
    for t in truth:
        ex0, ey0, ex1, ey1 = t.ellipse
        assert np.abs(after[ey0:ey1, ex0:ex1] - before[ey0:ey1, ex0:ex1]).mean() > 5, f"{t.text} was not re-lettered"


def test_health_and_cors(client):
    r = client.get("/v1/health", headers={"Origin": "chrome-extension://abcdefghijklmnop"})
    body = r.json()
    assert body["ok"] is True and body["version"] and body["gpu"]
    assert r.headers["access-control-allow-origin"] == "chrome-extension://abcdefghijklmnop"


def lm_studio_up() -> bool:
    s = Settings.from_env()
    try:
        r = httpx.get(f"{s.openai_base_url}/models", timeout=2)
        return r.status_code == 200 and s.openai_model in {m.get("id") for m in r.json().get("data", [])}
    except (httpx.HTTPError, ValueError):
        return False


@pytest.mark.skipif(not lm_studio_up(), reason="OpenAI-compatible endpoint not reachable or YOMIKAE_OPENAI_MODEL not listed")
def test_lm_studio_translates_a_page(client):
    engine = client.app.state.engine
    original = engine.translator
    engine.translator = OpenAICompatible(replace(Settings.from_env(), translator="openai"))
    try:
        img, truth = make_page(301)
        r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(img), "image/png")})
    finally:
        engine.translator = original
    assert r.status_code == 200, r.text
    bubbles = r.json()["bubbles"]
    assert len(bubbles) == len(truth)
    for b in bubbles:
        assert b["translation"] and b["translation"].isascii(), b


def short_line_and_sfx_page() -> tuple[Image.Image, tuple[int, int, int, int], tuple[int, int, int, int]]:
    import random

    from make_test_page import FONT_PATH, draw_screentone, draw_vertical_text
    from PIL import ImageDraw, ImageFont

    img = Image.new("RGB", (1280, 1816), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    panel = (40, 40, 600, 900)
    draw_screentone(img, panel, random.Random(7))
    draw.rectangle(panel, outline=(0, 0, 0), width=5)
    bubble = (230, 300, 380, 560)
    draw.ellipse(bubble, fill=(255, 255, 255), outline=(0, 0, 0), width=4)
    draw_vertical_text(img, (280, 386), ["けど"], ImageFont.truetype(FONT_PATH, 40))
    sfx = (720, 200, 880, 480)
    draw_vertical_text(img, (750, 220), ["ゴゴッ"], ImageFont.truetype(FONT_PATH, 70))
    return img, bubble, sfx


def test_short_line_in_a_bubble_is_translated_and_sfx_on_art_is_not(client):
    from yomikae import detect, ocr

    img, bubble, sfx = short_line_and_sfx_page()
    engine = client.app.state.engine
    image = np.asarray(img)
    blocks = detect.detect(engine.detector, image)
    in_sfx = [b for b in blocks if sfx[0] <= b.box.center[0] <= sfx[2] and sfx[1] <= b.box.center[1] <= sfx[3]]
    assert in_sfx, "the detector did not see the SFX, so this test would prove nothing"
    kept, _ = ocr.read(engine.ocr, image, blocks)
    assert [b.source for b in kept] == ["けど"]

    r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(img), "image/png")})
    assert r.status_code == 200, r.text
    page = r.json()
    assert [b["source"] for b in page["bubbles"]] == ["けど"] and page["bubbles"][0]["translation"]
    before = np.asarray(img.convert("L")).astype(int)
    after = np.asarray(Image.fromarray(decode(client.get(page["rendered_url"]).content)).convert("L")).astype(int)
    x0, y0, x1, y1 = bubble
    assert np.abs(after[y0:y1, x0:x1] - before[y0:y1, x0:x1]).mean() > 3, "the bubble was not re-lettered"
    x0, y0, x1, y1 = sfx
    assert np.abs(after[y0:y1, x0:x1] - before[y0:y1, x0:x1]).mean() < 1, "the SFX was touched"


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


def post_in_work(client, translator, seed, key, idx):
    engine = client.app.state.engine
    original = engine.translator
    engine.translator = translator
    try:
        r = client.post(
            "/v1/pages",
            files={"image": ("p.png", png_bytes(make_page(seed)[0]), "image/png")},
            data={"context_key": key, "page_index": str(idx)},
        )
    finally:
        engine.translator = original
    assert r.status_code == 200, r.text
    return r.json()


def test_a_line_seen_earlier_in_the_work_keeps_its_translation(client):
    translator = Scripted(lambda call, lines, _: [f"call {call} line {i}" for i in range(len(lines))])
    first = post_in_work(client, translator, 206, "example.com/g/memory", 0)
    second = post_in_work(client, translator, 403, "example.com/g/memory", 1)
    other_work = post_in_work(client, translator, 208, "example.com/g/other", 0)
    earlier = {b["source"]: b["translation"] for b in first["bubbles"]}
    shared = [b for b in second["bubbles"] if b["source"] in earlier]
    assert len(shared) == 2, [b["source"] for b in second["bubbles"]]
    for b in shared:
        assert b["translation"] == earlier[b["source"]], b
    for b in other_work["bubbles"]:
        assert b["translation"] not in earlier.values(), b


def test_a_name_keeps_its_first_spelling_on_later_pages(client):
    def reply(call, lines, context):
        name = f"{next(iter(context.names))}-nee" if context.names else ("Nagisa-nee" if call == 1 else "Nagisaki-nee")
        return [f"{name}, wait {i}!" for i in range(len(lines))]

    translator = Scripted(reply)
    post_in_work(client, translator, 207, "example.com/g/names", 0)
    later = post_in_work(client, translator, 211, "example.com/g/names", 1)
    assert later["bubbles"]
    for b in later["bubbles"]:
        assert "Nagisa-nee" in b["translation"] and "Nagisaki" not in b["translation"], b


def test_context_from_far_away_pages_is_dropped_but_names_stay(client):
    translator = Scripted(lambda call, lines, _: [f"Yuki-chan said {call}.{i}" for i in range(len(lines))])
    post_in_work(client, translator, 212, "example.com/g/far", 0)
    post_in_work(client, translator, 213, "example.com/g/far", 20)
    _, context = translator.calls[-1]
    assert context.pages == []
    assert "Yuki" in context.names


def test_an_echoed_line_is_retried_once_then_left_untouched(client):
    def reply(call, lines, _):
        return [line if line.startswith("それは") else f"English {i}" for i, line in enumerate(lines)]

    translator = Scripted(reply)
    img, truth = make_page(209)
    page = post_in_work(client, translator, 209, "example.com/g/echo", 0)
    echoed = [b for b in page["bubbles"] if b["source"].startswith("それは")]
    assert len(echoed) == 1 and echoed[0]["translation"] == ""
    assert all(b["translation"] for b in page["bubbles"] if b not in echoed)
    assert [lines for lines, _ in translator.calls] == [[b["source"] for b in page["bubbles"]], [echoed[0]["source"]]]
    assert client.app.state.engine.cache.load(page["page_id"]) is not None, "a page with a skipped line was not cached"
    t = next(t for t in truth if t.text.startswith("それは"))
    before = np.asarray(img.convert("L")).astype(int)
    after = np.asarray(Image.fromarray(decode(client.get(page["rendered_url"]).content)).convert("L")).astype(int)
    ex0, ey0, ex1, ey1 = t.ellipse
    assert np.abs(after[ey0:ey1, ex0:ex1] - before[ey0:ey1, ex0:ex1]).mean() < 1, "the echoed bubble was touched"


def test_a_misaligned_reply_is_retried_in_halves(client):
    import json

    sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        lines = json.loads(json.loads(request.content)["messages"][-1]["content"])["lines"]
        sizes.append(len(lines))
        items = [f"EN {line['text'].encode().hex()}" for line in lines]
        if len(lines) == 4:
            items = items[:3]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"translations": items}, ensure_ascii=False)}}]})

    translator = OpenAICompatible(replace(Settings.from_env(), translator="openai"))
    translator.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    page = post_in_work(client, translator, 214, "example.com/g/split", 0)
    assert sizes == [4, 2, 2]
    assert [b["translation"] for b in page["bubbles"]] == [f"EN {b['source'].encode().hex()}" for b in page["bubbles"]]



def lm_studio(replies):
    import json

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        lines = json.loads(json.loads(request.content)["messages"][-1]["content"])["lines"]
        calls.append(len(lines))
        status, error = replies[min(len(calls), len(replies)) - 1]
        if status != 200:
            return httpx.Response(status, json={"error": error})
        items = [f"EN {line['text'].encode().hex()}" for line in lines]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"translations": items}, ensure_ascii=False)}}]})

    translator = OpenAICompatible(replace(Settings.from_env(), translator="openai"))
    translator.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator.retry_delays = (0, 0)
    return translator, calls


def post_page_with(client, translator, seed):
    engine = client.app.state.engine
    original = engine.translator
    engine.translator = translator
    try:
        return client.post("/v1/pages", files={"image": ("p.png", png_bytes(make_page(seed)[0]), "image/png")})
    finally:
        engine.translator = original


def test_a_channel_error_from_lm_studio_is_resent_and_the_page_translates(client):
    translator, calls = lm_studio([(400, "Channel Error"), (200, None)])
    r = post_page_with(client, translator, 220)
    assert r.status_code == 200, r.text
    assert len(calls) == 2
    assert [b["translation"] for b in r.json()["bubbles"]] == [f"EN {b['source'].encode().hex()}" for b in r.json()["bubbles"]]


def test_a_channel_error_that_persists_fails_the_page_with_a_clear_error(client):
    translator, calls = lm_studio([(400, "Channel Error")])
    r = post_page_with(client, translator, 221)
    assert len(calls) == 3
    assert r.status_code >= 500 and r.status_code not in (502, 503, 504), r.text
    body = r.json()
    assert body["error"] == "translator_error"
    assert "Channel Error" in body["detail"] and "HTTPStatusError" not in body["detail"], body


def test_a_failed_fetch_inside_lm_studio_is_resent_and_the_page_translates(client):
    translator, calls = lm_studio([(400, "Engine protocol predict request failed: fetch failed"), (200, None)])
    r = post_page_with(client, translator, 223)
    assert r.status_code == 200, r.text
    assert len(calls) == 2


def test_a_request_over_the_context_size_is_not_resent(client):
    translator, calls = lm_studio([(400, "Engine protocol predict request returned 400: request (9001 tokens) exceeds the available context size (8192 tokens)")])
    r = post_page_with(client, translator, 224)
    assert len(calls) == 1
    assert r.status_code == 500 and r.json()["error"] == "translator_error"


def test_another_400_from_the_translator_is_not_resent(client):
    translator, calls = lm_studio([(400, "'messages' field is required")])
    r = post_page_with(client, translator, 222)
    assert len(calls) == 1
    assert r.status_code == 500 and r.json()["error"] == "translator_error"
    assert "'messages' field is required" in r.json()["detail"], r.text


def test_a_connection_closed_mid_reply_means_the_translator_is_unavailable(client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")

    translator = OpenAICompatible(replace(Settings.from_env(), translator="openai"))
    translator.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = post_page_with(client, translator, 225)
    assert r.status_code == 503 and r.json()["error"] == "translator_unavailable", r.text
    assert "RemoteProtocolError" in r.json()["detail"]


def test_a_model_the_translator_does_not_know_fails_the_page_instead_of_waiting(client):
    translator, calls = lm_studio([(404, "model 'nope' not found")])
    r = post_page_with(client, translator, 226)
    assert len(calls) == 1
    assert r.status_code == 500 and r.json()["error"] == "translator_error", r.text
    assert translator.model in r.json()["detail"] and "model 'nope' not found" in r.json()["detail"], r.text


def english(source):
    return f"EN {source.encode().hex()}"


def reply_server(texts):
    import json

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        lines = [line["text"] for line in json.loads(json.loads(request.content)["messages"][-1]["content"])["lines"]]
        calls.append(lines)
        items = [texts(calls, source) for source in lines]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"translations": items}, ensure_ascii=False)}}]})

    translator = OpenAICompatible(replace(Settings.from_env(), translator="openai"))
    translator.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return translator, calls


def test_json_syntax_leaked_into_a_reply_is_retried_not_drawn(client):
    def texts(calls, source):
        return 'The "Sensei" of "Schale."}, {' if len(calls) == 1 and source == calls[0][0] else english(source)

    translator, calls = reply_server(texts)
    page = post_in_work(client, translator, 215, "example.com/g/leak", 0)
    assert len(calls) > 1
    assert [b["translation"] for b in page["bubbles"]] == [english(b["source"]) for b in page["bubbles"]]


@pytest.mark.parametrize("seed, fragment", [(216, "}, {"), (217, '",'), (218, "]"), (219, 'Wait", "No')])
def test_a_line_that_only_ever_gets_json_syntax_back_is_left_blank(client, seed, fragment):
    translator, _ = reply_server(lambda calls, source: fragment if source == calls[0][0] else english(source))
    page = post_in_work(client, translator, seed, f"example.com/g/stuck{seed}", 0)
    translations = [b["translation"] for b in page["bubbles"]]
    assert translations[0] == "", translations
    assert translations[1:] == [english(b["source"]) for b in page["bubbles"][1:]]


def strip(pages: int, seed: int) -> tuple[Image.Image, list[dict]]:
    image = Image.new("RGB", (720, 1430 * pages), "white")
    truth = []
    for i in range(pages):
        page, bubbles = make_page(seed + i, 720, 1430)
        image.paste(page, (0, 1430 * i))
        truth += [{**t.__dict__, "text_box": [t.text_box[0], t.text_box[1] + 1430 * i, t.text_box[2], t.text_box[3] + 1430 * i]} for t in bubbles]
    return image, truth


def test_every_bubble_in_a_strip_taller_than_webp_allows_is_found_and_translated(client):
    import json

    img, truth = strip(12, 600)
    sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        lines = json.loads(json.loads(request.content)["messages"][-1]["content"])["lines"]
        sizes.append(len(lines))
        items = [{"id": line["id"], "text": english(line["text"])} for line in lines]
        reply = {"translations": items[:-1] if len(lines) == len(truth) else items}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]})

    translator = OpenAICompatible(replace(Settings.from_env(), translator="openai"))
    translator.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = client.app.state.engine
    original = engine.translator
    engine.translator = translator
    try:
        r = client.post("/v1/pages", files={"image": ("s.png", png_bytes(img), "image/png")})
    finally:
        engine.translator = original
    assert r.status_code == 200, r.text
    page = r.json()
    assert len(truth) > 30 and len(page["bubbles"]) == len(truth), [b["source"] for b in page["bubbles"]]
    for t, b in matched(truth, page["bubbles"]):
        assert iou(b["box"], t["text_box"]) > 0.7, (t, b)
        assert edit_distance(b["source"], t["text"]) <= 1, (b["source"], t["text"])
        assert b["translation"] == english(b["source"])
    assert sizes == [len(truth), len(truth) // 2, len(truth) - len(truth) // 2]
    assert page["rendered_url"].endswith("/rendered.jpg")
    rendered = client.get(page["rendered_url"])
    assert rendered.status_code == 200 and rendered.headers["content-type"] == "image/jpeg"
    assert decode(rendered.content).shape[:2] == (img.height, img.width) == (page["height"], page["width"])
    assert client.get(page["rendered_url"].replace(".jpg", ".webp")).status_code == 404


def test_a_bubble_cut_by_a_tile_edge_is_found_once_and_whole(client):
    from make_test_page import FONT_PATH, draw_vertical_text
    from PIL import ImageDraw, ImageFont

    from yomikae.tiles import tiles

    img = Image.new("RGB", (720, 5000), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 34)
    cover = tiles(*img.size)
    seams = [cover[0].window.y1, cover[2].owned.y0]
    texts = ["今日はいい天気だね", "あの店で待ってる"]
    for seam, text in zip(seams, texts):
        draw.ellipse((220, seam - 150, 500, seam + 150), fill="white", outline="black", width=4)
        draw_vertical_text(img, (300, seam - 100), [text[:5], text[5:]], font)
    r = client.post("/v1/pages", files={"image": ("s.png", png_bytes(img), "image/png")})
    assert r.status_code == 200, r.text
    bubbles = r.json()["bubbles"]
    assert len(cover) > 2 and [b["source"] for b in bubbles] == texts, bubbles
    for seam, b in zip(seams, bubbles):
        assert b["box"][1] < seam - 80 and b["box"][3] > seam + 60, (seam, b["box"])


class FullGpu(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.weight = torch.nn.Parameter(torch.zeros(1))
        self.calls = 0

    def forward(self, x, mask):
        self.calls += 1
        raise torch.OutOfMemoryError("CUDA out of memory. Tried to allocate 114.00 MiB.")


def test_a_page_whose_inpainting_runs_out_of_gpu_memory_is_inpainted_on_the_cpu(client, monkeypatch):
    engine = client.app.state.engine
    full = FullGpu()
    monkeypatch.setattr(engine, "lama", full)
    monkeypatch.setattr("yomikae.inpaint.flat_fill", lambda *args: None)
    img, truth = make_page(230)
    r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(img), "image/png")})
    assert r.status_code == 200, r.text
    assert full.calls >= 1
    out = np.asarray(Image.fromarray(decode(client.get(r.json()["rendered_url"]).content)).convert("L"))
    original = np.asarray(img.convert("L"))
    for t in truth:
        x0, y0, x1, y1 = t.text_box
        ink = original[y0:y1, x0:x1] < 128
        assert (out[y0:y1, x0:x1] < 128)[ink].mean() < 0.35, t.text


def test_every_page_runs_its_gpu_work_on_one_thread_so_gpu_memory_stays_flat(client, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from yomikae import detect, ocr

    seen = set()

    def recording(fn):
        def run(*args, **kwargs):
            seen.add(threading.get_ident())
            return fn(*args, **kwargs)

        return run

    monkeypatch.setattr(detect, "detect", recording(detect.detect))
    monkeypatch.setattr(ocr, "read", recording(ocr.read))
    pages = [png_bytes(make_page(seed)[0]) for seed in range(240, 246)]

    def post(data):
        return client.post("/v1/pages", files={"image": ("p.png", data, "image/png")})

    with ThreadPoolExecutor(6) as pool:
        responses = list(pool.map(post, pages))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert len(seen) == 1
