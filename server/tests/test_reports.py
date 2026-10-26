from __future__ import annotations

import json

import numpy as np
import pytest
from conftest import png_bytes
from PIL import Image

from yomikae import reports
from yomikae.pipeline import PIPELINE_VERSION

FIELDS = {"page_url": "https://example.com/g/1/2", "image_url": "https://img.example.com/1/2.png", "context_key": "example.com/g/1", "page_index": "2"}


def reports_root(client):
    return client.app.state.engine.settings.reports_dir


def post_report(client, data: bytes, name: str = "p.png", **extra):
    return client.post("/v1/reports", files={"image": (name, data, "application/octet-stream")}, data={**FIELDS, **extra})


def test_report_of_a_translated_page_saves_everything(client, translated):
    data, _, _, page = translated[0]
    r = post_report(client, data, note="bubble 2 reads wrong")
    assert r.status_code == 200, r.text
    body = r.json()
    path = reports_root(client) / body["report_id"]
    assert body["path"] == str(path)
    assert body["report_id"].endswith(page["page_id"][:12])
    assert sorted(p.name for p in path.iterdir()) == ["original.png", "rendered.webp", "report.json", "result.json"]
    assert (path / "original.png").read_bytes() == data
    assert (path / "rendered.webp").read_bytes() == client.get(page["rendered_url"]).content
    result = json.loads((path / "result.json").read_text())
    assert result["bubbles"] == page["bubbles"] and result["page_id"] == page["page_id"]
    assert "context_key" in result and "page_index" in result
    meta = json.loads((path / "report.json").read_text())
    assert meta["page_url"] == FIELDS["page_url"] and meta["image_url"] == FIELDS["image_url"]
    assert meta["context_key"] == FIELDS["context_key"] and meta["page_index"] == 2
    assert meta["page_id"] == page["page_id"] and meta["pipeline_version"] == PIPELINE_VERSION
    assert meta["note"] == "bubble 2 reads wrong" and meta["created_at"]
    assert meta["result"] == "result.json" and meta["rendered"] == "rendered.webp"


def test_report_without_a_cached_result_keeps_the_original(client):
    data = png_bytes(Image.fromarray(np.full((64, 48, 3), 200, np.uint8)))
    r = post_report(client, data, job_state="failed", job_error="Local server not reachable.")
    assert r.status_code == 200, r.text
    path = reports_root(client) / r.json()["report_id"]
    assert sorted(p.name for p in path.iterdir()) == ["original.png", "report.json"]
    meta = json.loads((path / "report.json").read_text())
    assert meta["result"] is None and meta["rendered"] is None
    assert meta["job_state"] == "failed" and meta["job_error"] == "Local server not reachable."


def test_undecodable_original_is_still_reported(client):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1300"></svg>'
    r = post_report(client, svg, name="page")
    assert r.status_code == 200, r.text
    path = reports_root(client) / r.json()["report_id"]
    assert (path / "original.svg").read_bytes() == svg


def test_failed_write_leaves_no_partial_directory(client, monkeypatch):
    root = reports_root(client)
    before = sorted(p.name for p in root.iterdir())

    def boom(*_):
        raise OSError("disk full")

    monkeypatch.setattr(reports.os, "rename", boom)
    data = png_bytes(Image.fromarray(np.full((64, 64, 3), 10, np.uint8)))
    with pytest.raises(OSError, match="disk full"):
        post_report(client, data)
    assert sorted(p.name for p in root.iterdir()) == before


def test_write_report_cleans_up_when_a_file_fails(tmp_path):
    with pytest.raises(TypeError):
        reports.write_report(tmp_path, "r1", {"original.png": b"x", "report.json": None})
    assert list(tmp_path.iterdir()) == []


def test_areas_convert_fractions_to_original_pixels_and_snap_to_bubbles():
    bubbles = [{"box": [10, 20, 110, 220]}, {"box": [500, 600, 700, 900]}]
    areas = [
        {"box": [0.05, 0.05, 0.2, 0.15], "bubble_index": 1},
        {"box": [0.1, 0.1, 0.3, 0.2], "bubble_index": None},
        {"box": [0.9, 0.95, 1.4, -0.2], "bubble_index": None},
        {"box": [0.5, 0.5, 0.6, 0.6], "bubble_index": 7},
    ]
    out = reports.resolve_areas(areas, (1280, 1816), bubbles)
    assert out == [
        {"box": [500, 600, 700, 900], "bubble_index": 1},
        {"box": [128, 182, 384, 363], "bubble_index": None},
        {"box": [1152, 0, 1280, 1725], "bubble_index": None},
        {"box": [640, 908, 768, 1090], "bubble_index": None},
    ]
    assert reports.resolve_areas(areas[1:2], None, []) == [{"box": [0.1, 0.1, 0.3, 0.2], "bubble_index": None}]


def test_report_with_areas_writes_marked_png(client, translated):
    data, img, _, page = translated[1]
    w, h = page["width"], page["height"]
    bubble = page["bubbles"][0]["box"]
    areas = [
        {"box": [bubble[0] / w, bubble[1] / h, bubble[2] / w, bubble[3] / h], "bubble_index": 0},
        {"box": [0.1, 0.5, 0.3, 0.6], "bubble_index": None},
    ]
    r = post_report(client, data, areas=json.dumps(areas))
    assert r.status_code == 200, r.text
    path = reports_root(client) / r.json()["report_id"]
    meta = json.loads((path / "report.json").read_text())
    free = [round(0.1 * w), round(0.5 * h), round(0.3 * w), round(0.6 * h)]
    assert meta["areas"] == [{"box": bubble, "bubble_index": 0}, {"box": free, "bubble_index": None}]
    assert meta["areas_units"] == "pixels" and meta["marked"] == "marked.png"
    marked = np.asarray(Image.open(path / "marked.png").convert("RGB"))
    original = np.asarray(img.convert("RGB"))
    assert marked.shape == original.shape
    mid_y = (free[1] + free[3]) // 2
    assert tuple(marked[mid_y, free[0]]) == reports.MARK_COLOR
    assert tuple(marked[mid_y, free[2]]) == reports.MARK_COLOR
    assert tuple(marked[(bubble[1] + bubble[3]) // 2, bubble[0]]) == reports.MARK_COLOR
    inside = (slice(free[1] + 20, free[3] - 20), slice(free[0] + 20, free[2] - 20))
    assert (marked[inside] == original[inside]).all()


def test_report_without_areas_has_no_marked_png(client, translated):
    data = translated[2][0]
    meta = json.loads((reports_root(client) / post_report(client, data).json()["report_id"] / "report.json").read_text())
    assert meta["areas"] == [] and meta["marked"] is None


def test_malformed_areas_are_rejected(client):
    r = post_report(client, b"x", areas='[{"box": [1, 2]}]')
    assert r.status_code == 400 and r.json()["error"] == "bad_request"


def test_truncated_original_with_areas_is_still_reported(client, translated):
    data = translated[0][0]
    cut = data[: len(data) // 2]
    r = post_report(client, cut, areas=json.dumps([{"box": [0.1, 0.1, 0.2, 0.2], "bubble_index": None}]))
    assert r.status_code == 200, r.text
    path = reports_root(client) / r.json()["report_id"]
    meta = json.loads((path / "report.json").read_text())
    assert (path / "original.png").read_bytes() == cut
    assert meta["marked"] is None and len(meta["areas"]) == 1
