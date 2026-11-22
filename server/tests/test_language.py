from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from conftest import decode, png_bytes
from make_test_page import FONT_PATH, KR_FONT_INDEX, make_korean_page, make_page
from PIL import Image, ImageDraw, ImageFont

from yomikae import animetext, detect, ocr, pipeline
from yomikae.cache import ContextStore
from yomikae.config import ANIMETEXT_PATH, PADDLE_DIR, PADDLE_FILES
from yomikae.language import JAPANESE, KOREAN, LANGUAGES
from yomikae.model import ContextLine
from yomikae.pipeline import page_id_of, translate_page
from yomikae.translate import build_messages, untranslated


@pytest.mark.parametrize("path", ["/v1/pages", "/v1/reports"])
def test_an_unknown_source_language_is_a_bad_request(client, path):
    fields = {"source_lang": "xx", "page_url": "https://example.com/1", "image_url": "https://example.com/1.png"}
    r = client.post(path, files={"image": ("p.png", b"not read", "image/png")}, data=fields)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "bad_request"
    assert "source_lang" in r.json()["detail"]


def test_a_report_without_source_lang_is_a_japanese_page_and_a_korean_report_is_another_page(client):
    data = png_bytes(make_page(0)[0])
    fields = {"page_url": "https://example.com/1", "image_url": "https://example.com/1.png"}
    saved = {}
    for lang in (None, "ko"):
        r = client.post("/v1/reports", files={"image": ("p.png", data, "image/png")}, data=fields | ({"source_lang": lang} if lang else {}))
        assert r.status_code == 200, r.text
        saved[lang] = json.loads((Path(r.json()["path"]) / "report.json").read_text())
    assert (saved[None]["source_lang"], saved[None]["page_id"]) == ("ja", hashlib.sha256(data).hexdigest())
    assert (saved["ko"]["source_lang"], saved["ko"]["page_id"]) == ("ko", page_id_of(data, KOREAN))


@pytest.mark.parametrize("lang", ["xx", "ko"])
def test_a_retranslate_whose_source_lang_is_unknown_or_disagrees_with_the_page_id_is_a_bad_request(client, lang):
    data = png_bytes(make_page(0)[0])
    r = client.post(
        f"/v1/pages/{page_id_of(data, JAPANESE)}/retranslate",
        files={"image": ("p.png", data, "image/png")},
        data={"request": json.dumps({"bubbles": [0]}), "source_lang": lang},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "bad_request"


@pytest.mark.parametrize("name, file", [("paddle", "korean_PP-OCRv5_rec_mobile.onnx"), ("animetext", "animetext_yolo12m.onnx")])
def test_a_korean_page_without_its_models_answers_reader_missing_every_time(client, monkeypatch, name, file):
    engine = client.app.state.engine

    def missing(device):
        raise detect.ModelMissing(f"Korean pages need {file} in server/models")

    for model in ("paddle", "animetext"):
        monkeypatch.setitem(pipeline.MODELS, model, missing if model == name else lambda device: object())
        for device in {engine.device, engine.detect_device, "cpu"}:
            monkeypatch.delitem(engine.models, (model, device), raising=False)
    data = png_bytes(make_korean_page(5)[0])
    for _ in range(2):
        r = client.post("/v1/pages", files={"image": ("p.png", data, "image/png")}, data={"source_lang": "ko", "context_key": "w/missing"})
        assert r.status_code == 500, r.text
        assert r.json()["error"] == "reader_missing"
        assert file in r.json()["detail"]


def test_a_missing_animetext_model_names_the_file_to_download(tmp_path):
    with pytest.raises(detect.ModelMissing, match="animetext_yolo12m.onnx in server/models"):
        animetext.AnimeText("cpu", tmp_path / "animetext_yolo12m.onnx")


def test_a_missing_paddle_model_names_the_files_to_download(tmp_path):
    with pytest.raises(detect.ModelMissing, match="PP-OCRv6_det_small.onnx, korean_PP-OCRv5_rec_mobile.onnx in server/models/"):
        ocr.PaddleReader("cpu", tmp_path)


def test_a_japanese_page_keeps_the_sha256_of_its_bytes_as_its_id():
    data = png_bytes(make_page(0)[0])
    assert page_id_of(data, LANGUAGES["ja"]) == hashlib.sha256(data).hexdigest()


def test_the_same_bytes_read_in_another_language_are_another_page():
    data = png_bytes(make_page(0)[0])
    ids = {page_id_of(data, language) for language in LANGUAGES.values()}
    assert len(ids) == len(LANGUAGES)
    assert all(len(i) == 64 and set(i) <= set("0123456789abcdef") for i in ids)


paddle_files = pytest.mark.skipif(not all((PADDLE_DIR / name).exists() for name in PADDLE_FILES), reason="the PaddleOCR models are not in server/models/paddleocr")
animetext_file = pytest.mark.skipif(not ANIMETEXT_PATH.exists(), reason="AnimeText is not in server/models")


class Recorder:
    name = "recorder"

    def __init__(self):
        self.calls = []

    async def translate(self, lines, context):
        self.calls.append((lines, context))
        return ["Hi."] * len(lines)

    async def health(self):
        return None


def test_a_korean_page_reaches_the_translator_with_the_korean_prompt_and_its_hangul_lines():
    translator, store = Recorder(), ContextStore()
    store.add("w/1", "p1", 1, [ContextLine("오늘날씨정말좋다", "Nice weather today.")])
    asyncio.run(translate_page(translator, store, ["빨리집에가자"], "w/1", 2, language=KOREAN))
    lines, context = translator.calls[0]
    assert lines == ["빨리집에가자"]
    system, user = build_messages(lines, context)
    assert system["content"].startswith("You translate Korean webtoon (manhwa) dialogue into English")
    assert "Minjun-ssi" in system["content"] and "hyung" in system["content"]
    payload = json.loads(user["content"])
    assert payload["lines"] == [{"id": 0, "text": "빨리집에가자"}]
    assert payload["previous_pages"] == [[{"ko": "오늘날씨정말좋다", "en": "Nice weather today."}]]


def test_a_japanese_page_still_gets_the_japanese_prompt():
    translator = Recorder()
    asyncio.run(translate_page(translator, ContextStore(), ["こんにちは"], None, None))
    system, _ = build_messages(*translator.calls[0])
    assert system["content"] == JAPANESE.prompt


def test_leftover_hangul_counts_as_untranslated_only_for_korean():
    assert ocr.is_source_text("그게정말이야?", KOREAN)
    assert not ocr.is_source_text("그게정말이야?", JAPANESE)
    assert untranslated("그게정말이야?", "그게정말이야?", KOREAN)
    assert not untranslated("그게정말이야?", "Is that true?", KOREAN)


@pytest.mark.models
@paddle_files
@animetext_file
def test_a_korean_page_is_read_horizontally_with_its_spaces(client):
    img, truth = make_korean_page(3)
    data = png_bytes(img)
    r = client.post("/v1/pages", files={"image": ("p.png", data, "image/png")}, data={"source_lang": "ko"})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["page_id"] == page_id_of(data, KOREAN)
    assert sorted("".join(b["source"].split()) for b in page["bubbles"]) == sorted(t.text.replace(" ", "") for t in truth)
    assert sum(len(b["source"].split()) for b in page["bubbles"]) >= 0.8 * sum(len(t.text.split()) for t in truth)
    assert not any(b["vertical"] for b in page["bubbles"])


@pytest.mark.models
@paddle_files
@animetext_file
def test_a_narrow_korean_bubble_of_stacked_short_rows_is_read_as_horizontal_text(client):
    img = Image.new("RGB", (800, 900), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 36, index=KR_FONT_INDEX)
    rows = ["그게", "정말", "이야?"]
    draw.ellipse((300, 250, 444, 532), outline="black", width=4)
    for r, row in enumerate(rows):
        draw.text((372, 328 + 54 * r), row, font=font, fill="black", anchor="mm")
    r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(img), "image/png")}, data={"source_lang": "ko"})
    assert r.status_code == 200, r.text
    assert [(b["source"].split(), b["vertical"]) for b in r.json()["bubbles"]] == [(rows, False)]


class FullGpuOnce:
    def __init__(self, engine):
        self.engine = engine
        self.calls = 0

    def __call__(self, *args):
        from rapidocr.inference_engine.onnxruntime.main import ONNXRuntimeError

        self.calls += 1
        if self.calls == 1:
            raise ONNXRuntimeError("Non-zero status code returned while running Conv node. Failed to allocate memory for requested buffer of size 6662656")
        return self.engine(*args)


@pytest.mark.models
@paddle_files
@animetext_file
def test_a_korean_page_whose_reader_runs_out_of_gpu_memory_is_read_again(client):
    engine = client.app.state.engine
    reader = engine.model("paddle", engine.device)
    full = FullGpuOnce(reader.engine)
    reader.engine = full
    try:
        img, truth = make_korean_page(5)
        r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(img), "image/png")}, data={"source_lang": "ko"})
    finally:
        reader.engine = full.engine
    assert r.status_code == 200, r.text
    assert full.calls > 1
    assert sorted("".join(b["source"].split()) for b in r.json()["bubbles"]) == sorted(t.text.replace(" ", "") for t in truth)


class FullGpuSessionOnce:
    def __init__(self, session):
        self.session = session
        self.calls = 0

    def run(self, *args):
        from onnxruntime.capi.onnxruntime_pybind11_state import Fail

        self.calls += 1
        if self.calls == 1:
            raise Fail("Non-zero status code returned while running Conv node. Failed to allocate memory for requested buffer of size 6662656")
        return self.session.run(*args)


@pytest.mark.models
@paddle_files
@animetext_file
def test_a_korean_page_whose_detector_runs_out_of_gpu_memory_is_found_again(client):
    engine = client.app.state.engine
    finder = engine.model("animetext", engine.detect_device)
    full = FullGpuSessionOnce(finder.session)
    finder.session = full
    try:
        img, truth = make_korean_page(6)
        r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(img), "image/png")}, data={"source_lang": "ko"})
    finally:
        finder.session = full.session
    assert r.status_code == 200, r.text
    assert full.calls > 1
    assert sorted("".join(b["source"].split()) for b in r.json()["bubbles"]) == sorted(t.text.replace(" ", "") for t in truth)


@pytest.mark.models
@paddle_files
@animetext_file
def test_a_korean_line_the_text_mask_misses_is_still_erased(client, monkeypatch):
    monkeypatch.setattr(detect, "text_mask", lambda session, image: np.zeros(image.shape[:2], np.uint8))
    img, truth = make_korean_page(7)
    r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(img), "image/png")}, data={"source_lang": "ko"})
    assert r.status_code == 200, r.text
    assert len(r.json()["bubbles"]) == len(truth)
    rendered = Image.fromarray(decode(client.get(r.json()["rendered_url"]).content))
    reader = client.app.state.engine.model("paddle", "cpu")
    for t in truth:
        text, _, _ = reader.recognize(rendered.crop(tuple(t.text_box)))
        assert not KOREAN.letters.search(text), (t.text, text)
