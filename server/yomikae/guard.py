from __future__ import annotations

import ipaddress
import json
import logging
from dataclasses import dataclass

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("yomikae")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
EXTENSION_SCHEME = "chrome-extension://"
BROWSER_SITES = {"none", "same-origin"}
MAX_BODY_BYTES = 64 * 1024**2


@dataclass(frozen=True)
class Refusal:
    status: int
    code: str
    detail: str


def is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def host_name(host: str) -> str:
    name = host[: host.index("]") + 1] if host.startswith("[") and "]" in host else host.rsplit(":", 1)[0]
    return name.lower()


def refusal(headers: Headers, client: str | None, extension_ids: tuple[str, ...]) -> Refusal | None:
    problem = caller_problem(headers, client, extension_ids)
    if problem is not None:
        return Refusal(403, "forbidden", problem)
    if "chunked" in headers.get("transfer-encoding", "").lower():
        return Refusal(411, "bad_request", "send the request body with a Content-Length")
    length = headers.get("content-length", "0")
    if not length.isdigit() or int(length) > MAX_BODY_BYTES:
        return Refusal(413, "bad_request", f"the request body is larger than {MAX_BODY_BYTES // 1024**2} MB")
    return None


def caller_problem(headers: Headers, client: str | None, extension_ids: tuple[str, ...]) -> str | None:
    if client is None or not is_loopback(client):
        return f"requests must come from this computer, not {client}"
    host = headers.get("host", "")
    if host_name(host) not in LOOPBACK_HOSTS:
        return f"Host {host!r} is not this computer"
    origin = headers.get("origin")
    if origin is not None:
        extension = origin.startswith(EXTENSION_SCHEME) and (not extension_ids or origin.removeprefix(EXTENSION_SCHEME) in extension_ids)
        return None if extension else f"requests from {origin} are refused: only the Yomikae extension may call this server"
    site = headers.get("sec-fetch-site")
    if site is not None and site not in BROWSER_SITES:
        return f"a {site} request from a web page is refused: only the Yomikae extension may call this server"
    return None


class RequestGuard:
    def __init__(self, app: ASGIApp, extension_ids: tuple[str, ...] = ()):
        self.app = app
        self.extension_ids = extension_ids

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        client = scope.get("client")
        problem = refusal(Headers(scope=scope), client[0] if client else None, self.extension_ids)
        if problem is None:
            return await self.app(scope, receive, send)
        log.warning("refused %s %s: %s", scope["method"], scope["path"], problem.detail)
        body = json.dumps({"error": problem.code, "detail": problem.detail}).encode()
        await send({"type": "http.response.start", "status": problem.status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
