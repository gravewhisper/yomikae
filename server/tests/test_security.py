from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
from conftest import png_bytes
from fastapi.testclient import TestClient
from PIL import Image
from starlette.responses import PlainTextResponse

from yomikae.app import app
from yomikae.guard import MAX_BODY_BYTES, RequestGuard

EXTENSION = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
WEB = "https://attacker.example"


def plain_image(value: int) -> bytes:
    return png_bytes(Image.fromarray(np.full((40, 30, 3), value, np.uint8)))


def page_count(client) -> int:
    return len(client.app.state.engine.cache.page_dirs())


def report_count(client) -> int:
    root = client.app.state.engine.settings.reports_dir
    return len(list(root.iterdir())) if root.exists() else 0


def assert_forbidden(r):
    assert r.status_code == 403, r.text
    assert r.json()["error"] == "forbidden"


@pytest.mark.parametrize("origin", [WEB, "http://localhost:3000", "http://127.0.0.1:8765", "null"])
def test_a_web_page_cannot_post_a_page(client, origin):
    before = page_count(client)
    r = client.post("/v1/pages", files={"image": ("p.png", plain_image(11), "image/png")}, headers={"Origin": origin})
    assert_forbidden(r)
    assert page_count(client) == before


def test_a_web_page_cannot_file_a_report(client):
    before = report_count(client)
    fields = {"page_url": "https://a.example/1", "image_url": "https://a.example/1.png"}
    r = client.post("/v1/reports", files={"image": ("p.png", plain_image(12), "image/png")}, data=fields, headers={"Origin": WEB})
    assert_forbidden(r)
    assert report_count(client) == before


def test_a_web_page_cannot_rewrite_names_through_retranslate(client):
    data = plain_image(13)
    ask = {"context_key": "victim.example/work", "bubbles": [0], "names": [{"ja": "山田", "en": "Pwned"}]}
    r = client.post(
        f"/v1/pages/{hashlib.sha256(data).hexdigest()}/retranslate",
        files={"image": ("p.png", data, "image/png")},
        data={"request": json.dumps(ask)},
        headers={"Origin": WEB},
    )
    assert_forbidden(r)
    assert client.get("/v1/names", params={"context_key": "victim.example/work"}).json()["names"] == []


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_a_web_preflight_is_refused(client, method):
    r = client.options("/v1/cache", headers={"Origin": WEB, "Access-Control-Request-Method": method})
    assert_forbidden(r)


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_web_page_gets_without_an_origin_are_refused(client, translated, site):
    page = translated[0][3]
    assert_forbidden(client.get(page["rendered_url"], headers={"Sec-Fetch-Site": site}))
    assert_forbidden(client.get("/v1/names", params={"context_key": "x"}, headers={"Sec-Fetch-Site": site}))
    assert_forbidden(client.get("/v1/health", headers={"Sec-Fetch-Site": site}))


@pytest.mark.parametrize("host", ["attacker.example:7878", "attacker.example", "192.168.1.20:7878", "127.0.0.1.attacker.example"])
def test_a_rebound_host_name_is_refused(client, host):
    assert_forbidden(client.get("/v1/health", headers={"Host": host}))
    assert_forbidden(client.get("/v1/names", params={"context_key": "x"}, headers={"Host": host, "Origin": EXTENSION}))


@pytest.mark.parametrize("host", ["127.0.0.1:7878", "localhost:7878", "LOCALHOST", "[::1]:7878"])
def test_loopback_host_names_are_accepted(client, host):
    assert client.get("/v1/health", headers={"Host": host}).status_code == 200


def test_a_request_from_another_machine_is_refused(client):
    remote = TestClient(app, base_url="http://127.0.0.1:7878", client=("192.168.1.30", 50000))
    assert_forbidden(remote.get("/v1/health"))


def test_the_extension_reaches_every_endpoint(client, translated):
    data, _, _, page = translated[0]
    ext = {"Origin": EXTENSION, "Sec-Fetch-Site": "none"}
    assert client.get("/v1/health", headers={"Sec-Fetch-Site": "none"}).status_code == 200
    assert client.get(page["rendered_url"], headers={"Sec-Fetch-Site": "none"}).status_code == 200
    r = client.post("/v1/pages", files={"image": ("p.png", data, "image/png")}, headers=ext)
    assert r.status_code == 200 and r.json()["cached"]
    r = client.put("/v1/names/entry", params={"context_key": "ext.example/w"}, json={"ja": "鈴木", "en": "Suzuki"}, headers=ext)
    assert r.status_code == 200, r.text
    assert [n["en"] for n in r.json()["names"]] == ["Suzuki"]
    pre = client.options("/v1/names", headers={"Origin": EXTENSION, "Access-Control-Request-Method": "PUT"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == EXTENSION


def test_a_same_origin_page_of_the_server_can_read(client):
    assert client.get("/openapi.json", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_a_pinned_extension_id_refuses_other_extensions():
    inner = PlainTextResponse("ok")
    guarded = TestClient(RequestGuard(inner, extension_ids=("abcdefghijklmnopabcdefghijklmnop",)), base_url="http://127.0.0.1:7878", client=("127.0.0.1", 1))
    assert guarded.post("/", headers={"Origin": EXTENSION}).status_code == 200
    assert guarded.post("/", headers={"Origin": "chrome-extension://ponmlkjihgfedcbaponmlkjihgfedcba"}).status_code == 403
    unpinned = TestClient(RequestGuard(inner), base_url="http://127.0.0.1:7878", client=("127.0.0.1", 1))
    assert unpinned.post("/", headers={"Origin": "chrome-extension://ponmlkjihgfedcbaponmlkjihgfedcba"}).status_code == 200


def test_an_upload_over_the_size_limit_is_refused_before_it_is_read(client):
    before = page_count(client)
    r = client.post("/v1/pages", files={"image": ("p.png", b"\0" * MAX_BODY_BYTES, "image/png")})
    assert r.status_code == 413, r.text
    assert r.json()["error"] == "bad_request"
    assert page_count(client) == before


def test_a_body_without_a_length_is_refused(client):
    r = client.put("/v1/names", params={"context_key": "x"}, content=iter([b'{"names": []}']), headers={"Content-Type": "application/json"})
    assert r.status_code == 411, r.text
    assert r.json()["error"] == "bad_request"
