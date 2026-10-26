from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from PIL import Image
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import Settings
from .cache import KINDS, SAVED_SOURCES, prune_stale
from .model import CastEntry
from .pipeline import FORMATS, PIPELINE_VERSION, BadImage, Engine, decode_image
from .reports import BadReport, image_extension, image_size, json_bytes, new_report_id, parse_areas, render_marked, resolve_areas, write_report
from .translate import TranslatorFailed, TranslatorUnavailable

log = logging.getLogger("yomikae")
PAGE_ID_CHARS = set("0123456789abcdef")
INTERNAL_FIELDS = ("context_key", "page_index", "font_sizes", "skipped")
MAX_NAMES = 500
NAME_FIELDS = {"ja": 200, "en": 100, "reading": 100}


def error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status)


@asynccontextmanager
async def lifespan(app: FastAPI):
    t = time.perf_counter()
    settings = Settings.from_env()
    pruned = prune_stale(settings.cache_dir / f"v{PIPELINE_VERSION}")
    if pruned:
        log.warning("removed %d stale cache folders", pruned)
    engine = Engine(settings)
    engine.warmup()
    log.warning("yomikae ready in %.1fs (translator %s, gpu %s)", time.perf_counter() - t, engine.translator.name, engine.gpu_name)
    app.state.engine = engine
    app.state.eviction = None
    yield


app = FastAPI(title="Yomikae", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


@app.exception_handler(BadImage)
async def on_bad_image(_: Request, e: BadImage):
    return error(400, "bad_image", str(e))


@app.exception_handler(BadReport)
async def on_bad_report(_: Request, e: BadReport):
    return error(400, "bad_request", str(e))


@app.exception_handler(RequestValidationError)
async def on_validation(_: Request, e: RequestValidationError):
    return error(400, "bad_image", f"invalid request: {e.errors()}")


@app.exception_handler(TranslatorUnavailable)
async def on_translator(_: Request, e: TranslatorUnavailable):
    return error(503, "translator_unavailable", str(e))


@app.exception_handler(TranslatorFailed)
async def on_translator_failed(_: Request, e: TranslatorFailed):
    return error(500, "translator_error", str(e))


@app.exception_handler(StarletteHTTPException)
async def on_http(_: Request, e: StarletteHTTPException):
    return error(e.status_code, "internal" if e.status_code >= 500 else "not_found", str(e.detail))


@app.exception_handler(Exception)
async def on_internal(_: Request, e: Exception):
    log.exception("internal error")
    return error(500, "internal", repr(e))


@app.get("/v1/health")
async def health(request: Request):
    engine: Engine = request.app.state.engine
    problem = await engine.translator.health()
    body = {"ok": problem is None, "version": __version__, "translator": engine.translator.name, "gpu": engine.gpu_name}
    if problem:
        body["detail"] = problem
    return body


@app.post("/v1/pages")
async def post_page(
    request: Request,
    image: Annotated[UploadFile, File()],
    context_key: Annotated[str | None, Form()] = None,
    page_index: Annotated[int | None, Form()] = None,
):
    data = await image.read()
    if not data:
        raise BadImage("empty image")
    result = await request.app.state.engine.process(data, context_key or None, page_index)
    if not result["cached"]:
        schedule_eviction(request.app)
    return {k: v for k, v in result.items() if k not in INTERNAL_FIELDS}


def schedule_eviction(app: FastAPI) -> None:
    engine: Engine = app.state.engine
    running = app.state.eviction
    if (running is not None and not running.done()) or not engine.cache.over(engine.settings.cache_max_bytes):
        return
    app.state.eviction = asyncio.create_task(evict(engine))


async def evict(engine: Engine) -> None:
    evicted = await asyncio.to_thread(engine.cache.evict, engine.settings.cache_max_bytes)
    for result in evicted:
        engine.context.forget(result)
    if evicted:
        log.warning("evicted %d cached pages", len(evicted))


@app.delete("/v1/cache")
async def clear_cache(request: Request):
    engine: Engine = request.app.state.engine
    removed = await asyncio.to_thread(engine.cache.clear)
    for result in removed:
        engine.context.forget(result)
    return {"removed": len(removed)}


def cast_body(engine: Engine, key: str) -> dict:
    rows = [{"ja": e.ja, "en": e.en, "reading": e.reading, "source": e.source, "kind": e.kind} for e in engine.context.cast(key)]
    return {"context_key": key, "names": rows}


def parse_entry(row) -> CastEntry:
    if not isinstance(row, dict):
        raise BadReport("each name must be an object")
    values = {}
    for name, limit in NAME_FIELDS.items():
        value = row.get(name, "")
        if not isinstance(value, str) or len(value) > limit:
            raise BadReport(f"{name} must be a string of at most {limit} characters")
        values[name] = value.strip()
    if not values["en"]:
        raise BadReport("en must not be empty")
    source = row.get("source") or "user"
    if source not in (*SAVED_SOURCES, "auto"):
        raise BadReport(f"source must be one of auto, {', '.join(SAVED_SOURCES)}")
    kind = row.get("kind") or "name"
    if kind not in KINDS:
        raise BadReport(f"kind must be one of {', '.join(KINDS)}")
    return CastEntry(values["ja"], values["en"], values["reading"], source, kind)


async def json_body(request: Request):
    try:
        return await request.json()
    except ValueError as e:
        raise BadReport(f"body is not JSON: {e}") from e


@app.get("/v1/names")
async def get_names(request: Request, context_key: str = ""):
    if not context_key:
        raise BadReport("context_key is required")
    return cast_body(request.app.state.engine, context_key)


@app.put("/v1/names")
async def put_names(request: Request, context_key: str = ""):
    if not context_key:
        raise BadReport("context_key is required")
    body = await json_body(request)
    rows = body.get("names") if isinstance(body, dict) else None
    if not isinstance(rows, list) or len(rows) > MAX_NAMES:
        raise BadReport(f"names must be a list of at most {MAX_NAMES} entries")
    entries = [e for e in map(parse_entry, rows) if e.source != "auto"]
    engine: Engine = request.app.state.engine
    engine.context.save_names(context_key, entries)
    return cast_body(engine, context_key)


@app.put("/v1/names/entry")
async def put_name(request: Request, context_key: str = ""):
    if not context_key:
        raise BadReport("context_key is required")
    entry = parse_entry(await json_body(request))
    engine: Engine = request.app.state.engine
    engine.context.set_name(context_key, CastEntry(entry.ja, entry.en, entry.reading, "user", entry.kind))
    return cast_body(engine, context_key)


@app.get("/v1/pages/{page_id}/rendered.{ext}")
async def rendered(request: Request, page_id: str, ext: str):
    if len(page_id) != 64 or not set(page_id) <= PAGE_ID_CHARS:
        return error(404, "not_found", f"no rendered page {page_id}")
    path = request.app.state.engine.cache.rendered_path(page_id)
    fmt = next((f for f in FORMATS if f"rendered.{f.ext}" == path.name), None)
    if fmt is None or fmt.ext != ext:
        return error(404, "not_found", f"no rendered page {page_id}")
    try:
        data = path.read_bytes()
    except OSError:
        return error(404, "not_found", f"no rendered page {page_id}")
    return Response(data, media_type=fmt.media_type)


def marked_png(data: bytes, areas: list[dict]) -> bytes | None:
    try:
        image = decode_image(data)
    except BadImage:
        return None
    return render_marked(Image.fromarray(image), areas)


@app.post("/v1/reports")
async def post_report(
    request: Request,
    image: Annotated[UploadFile, File()],
    page_url: Annotated[str, Form()],
    image_url: Annotated[str, Form()],
    context_key: Annotated[str | None, Form()] = None,
    page_index: Annotated[int | None, Form()] = None,
    note: Annotated[str, Form()] = "",
    job_state: Annotated[str | None, Form()] = None,
    job_error: Annotated[str | None, Form()] = None,
    areas: Annotated[str, Form()] = "[]",
):
    picked = parse_areas(areas)
    data = await image.read()
    if not data:
        raise BadImage("empty image")
    engine: Engine = request.app.state.engine
    page_id = hashlib.sha256(data).hexdigest()
    result = engine.cache.load(page_id)
    size = image_size(data)
    resolved = resolve_areas(picked, size, result["bubbles"] if result else [])
    original = f"original.{image_extension(data)}"
    created = datetime.now(timezone.utc)
    files = {original: data}
    if result is not None:
        try:
            rendered_path = engine.cache.rendered_path(page_id)
            files[rendered_path.name] = rendered_path.read_bytes()
            files["result.json"] = json_bytes(result)
        except OSError:
            result = None
    if resolved and size is not None:
        marked = await asyncio.to_thread(marked_png, data, resolved)
        if marked is not None:
            files["marked.png"] = marked
    files["report.json"] = json_bytes(
        {
            "page_url": page_url,
            "image_url": image_url,
            "context_key": context_key or None,
            "page_index": page_index,
            "page_id": page_id,
            "pipeline_version": PIPELINE_VERSION,
            "note": note,
            "job_state": job_state,
            "job_error": job_error,
            "areas": resolved,
            "areas_units": "pixels" if size is not None else "fraction",
            "created_at": created.isoformat(timespec="milliseconds"),
            "original": original,
            "result": "result.json" if result is not None else None,
            "rendered": rendered_path.name if result is not None else None,
            "marked": "marked.png" if "marked.png" in files else None,
        }
    )
    path = await asyncio.to_thread(write_report, engine.settings.reports_dir, new_report_id(page_id, created), files)
    log.warning("report saved to %s", path)
    return {"report_id": path.name, "path": str(path)}
