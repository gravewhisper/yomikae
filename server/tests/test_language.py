from __future__ import annotations

import hashlib

import pytest
from conftest import png_bytes
from make_test_page import make_page

from yomikae.language import LANGUAGES
from yomikae.pipeline import page_id_of


@pytest.mark.parametrize("path", ["/v1/pages", "/v1/reports"])
def test_an_unknown_source_language_is_a_bad_request(client, path):
    fields = {"source_lang": "xx", "page_url": "https://example.com/1", "image_url": "https://example.com/1.png"}
    r = client.post(path, files={"image": ("p.png", b"not read", "image/png")}, data=fields)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "bad_request"
    assert "source_lang" in r.json()["detail"]


def test_a_japanese_page_keeps_the_sha256_of_its_bytes_as_its_id():
    data = png_bytes(make_page(0)[0])
    assert page_id_of(data, LANGUAGES["ja"]) == hashlib.sha256(data).hexdigest()


def test_the_same_bytes_read_in_another_language_are_another_page():
    data = png_bytes(make_page(0)[0])
    ids = {page_id_of(data, language) for language in LANGUAGES.values()}
    assert len(ids) == len(LANGUAGES)
    assert all(len(i) == 64 and set(i) <= set("0123456789abcdef") for i in ids)
