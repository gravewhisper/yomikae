from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR / "tools"))

from make_test_page import make_page  # noqa: E402


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def decode(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("YOMIKAE_TRANSLATOR", "fake")
    mp.setenv("YOMIKAE_CACHE_DIR", str(tmp_path_factory.mktemp("cache")))
    mp.setenv("YOMIKAE_REPORTS_DIR", str(tmp_path_factory.mktemp("reports")))
    from yomikae.app import app

    with TestClient(app, base_url="http://127.0.0.1:7878", client=("127.0.0.1", 50000)) as c:
        yield c
    mp.undo()


@pytest.fixture(scope="session")
def synthetic_pages():
    return {seed: make_page(seed) for seed in (0, 1, 2)}


@pytest.fixture(scope="session")
def translated(client, synthetic_pages):
    out = {}
    for seed, (img, truth) in synthetic_pages.items():
        data = png_bytes(img)
        r = client.post("/v1/pages", files={"image": ("p.png", data, "image/png")})
        assert r.status_code == 200, r.text
        out[seed] = (data, img, truth, r.json())
    return out
