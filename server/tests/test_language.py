from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from conftest import png_bytes
from make_test_page import make_korean_page, make_page

from yomikae import ocr
from yomikae.cache import ContextStore
from yomikae.config import KEMOLATE_DIR
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


def test_a_missing_kemolate_model_names_the_files_to_download(tmp_path):
    with pytest.raises(ocr.ReaderMissing, match="encoder_model.onnx, decoder_model.onnx, tokenizer.json"):
        ocr.KemolateReader("cpu", tmp_path)


def test_a_japanese_page_keeps_the_sha256_of_its_bytes_as_its_id():
    data = png_bytes(make_page(0)[0])
    assert page_id_of(data, LANGUAGES["ja"]) == hashlib.sha256(data).hexdigest()


def test_the_same_bytes_read_in_another_language_are_another_page():
    data = png_bytes(make_page(0)[0])
    ids = {page_id_of(data, language) for language in LANGUAGES.values()}
    assert len(ids) == len(LANGUAGES)
    assert all(len(i) == 64 and set(i) <= set("0123456789abcdef") for i in ids)


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
@pytest.mark.skipif(not (KEMOLATE_DIR / "encoder_model.onnx").exists(), reason="Kemolate OCR is not in server/models/kemolate-ocr")
def test_a_korean_page_is_read_horizontally_through_kemolate(client):
    img, truth = make_korean_page(3)
    data = png_bytes(img)
    r = client.post("/v1/pages", files={"image": ("p.png", data, "image/png")}, data={"source_lang": "ko"})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["page_id"] == page_id_of(data, KOREAN)
    assert sorted(b["source"] for b in page["bubbles"]) == sorted(t.text.replace(" ", "") for t in truth)
    assert not any(b["vertical"] for b in page["bubbles"])
