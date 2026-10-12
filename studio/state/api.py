"""HTTP routes for sources, templates, review and approval, and the signing operation.

The routes answer 503 unless saved state is configured, and only local disk can be configured. A
deployment's disk is ephemeral, so on Vercel nothing is configured: saving there would be a false
Saved. The upload route is the local stand-in for the direct R2 upload, which waits on Alex's R2
decisions (guide §10).

`StateGate` answers before FastAPI reads or validates a body:
- 503 when state is not configured;
- 403 unless the request comes from a loopback address, is addressed to a loopback host, and any
  page that sent it is served from one;
- 413 when a JSON body is over MAX_JSON_BYTES, by its declared length or by the bytes that arrive.
The upload route bounds its own body at MAX_SOURCE_BYTES.

The loopback check keeps other machines (even when the server listens on every interface), other
sites' pages and rebound host names away from saved state. There is no proxy support. uvicorn
trusts X-Forwarded-For from a proxy on this machine by default, so a forwarded request is judged
by the address the proxy reports. It is not an identity: anyone who can run a request on this
machine is answered, and the studio has no login of its own (Alex's access decision, guide §10).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import Headers
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from studio.state import sources
from studio.state.errors import Conflict, Invalid, NotFound
from studio.state.signing import Operations, SimulatedSigningAdapter
from studio.state.store import LocalObjectStore, ObjectStore, StorageError
from studio.state.templates import Templates

STATE_DIR_ENV = "STUDIO_STATE_DIR"
MAX_SOURCE_BYTES = sources.MAX_SOURCE_BYTES
MAX_JSON_BYTES = 1024 * 1024  # a template at its 2,000-field limit is about 0.5 MB
NOT_CONFIGURED = "saved state is not configured here; nothing can be saved"
UPLOAD_PATH = "/api/sources"
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
NOT_LOOPBACK = "saved state answers only loopback requests from loopback pages"


@dataclass(frozen=True)
class Services:
    store: ObjectStore
    templates: Templates
    operations: Operations


def utc_now() -> datetime:
    return datetime.now(UTC)


def local_services(root: Path, clock: Callable[[], datetime] = utc_now) -> Services:
    store = LocalObjectStore(root)
    templates = Templates(store, clock)
    operations = Operations(store, templates, SimulatedSigningAdapter(store), clock)
    return Services(store, templates, operations)


def services_from_environment(environ: Mapping[str, str], repository: Path) -> Services | None:
    if environ.get("VERCEL"):
        return None
    root = environ.get(STATE_DIR_ENV)
    if not root:
        return None
    if Path(root).resolve().is_relative_to(repository.resolve()):
        raise RuntimeError(f"{STATE_DIR_ENV} must be outside the repository")
    return local_services(Path(root).absolute())  # unresolved, so a link is seen and refused


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NewTemplate(_Body):
    source_sha256: str
    policy: dict[str, Any]


class Save(_Body):
    base_revision: int
    policy: dict[str, Any]
    fields: list[Any]
    save_id: str  # chosen by the client per save; a retry of the same request reuses it


class Bound(_Body):
    revision: int
    binding: str


router = APIRouter(prefix="/api")


class StateGate:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = _state_route_path(scope)
        if path is None:
            await self.app(scope, receive, send)
            return
        if getattr(scope["app"].state, "services", None) is None:
            await _answer(scope, receive, send, 503, NOT_CONFIGURED)
            return
        if not _loopback(scope):
            await _answer(scope, receive, send, 403, NOT_LOOPBACK)
            return
        if path == UPLOAD_PATH:
            await self.app(scope, receive, send)
            return
        too_large = f"a request body is at most {MAX_JSON_BYTES} bytes"
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_JSON_BYTES:
            await _answer(scope, receive, send, 413, too_large)
            return
        body, more = bytearray(), True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return  # the client left; there is no one to answer
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > MAX_JSON_BYTES:
                await _answer(scope, receive, send, 413, too_large)
                return
        await self.app(scope, _replay(bytes(body), receive), send)


def _state_route_path(scope: Scope) -> str | None:
    if scope["type"] != "http":
        return None
    for route in router.routes:
        if route.matches(scope)[0] is Match.FULL:
            return route.path
    return None


def _loopback(scope: Scope) -> bool:
    peer, headers = scope.get("client"), Headers(scope=scope)
    try:
        if peer is None or not ipaddress.ip_address(peer[0]).is_loopback:
            return False
        host = urlsplit(f"//{headers.get('host', '')}").hostname
        origin = headers.get("origin")
        if origin is None:
            return host in LOOPBACK
        page = urlsplit(origin)
        return host in LOOPBACK and page.scheme in ("http", "https") and page.hostname in LOOPBACK
    except ValueError:  # a peer that is not an address, or a malformed host or port
        return False


async def _answer(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
    await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)


def _replay(body: bytes, receive: Receive) -> Receive:
    replayed = False

    async def replay() -> Message:
        nonlocal replayed
        if replayed:
            return await receive()
        replayed = True
        return {"type": "http.request", "body": body, "more_body": False}

    return replay


def _services(request: Request) -> Services:
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(503, NOT_CONFIGURED)
    return services


def _call(action: Callable[..., Any], *args: Any, writes: bool = True) -> Any:
    try:
        return action(*args)
    except NotFound:
        raise HTTPException(404, "no such template or operation") from None
    except Invalid as error:
        raise HTTPException(422, {"errors": error.errors}) from None
    except Conflict as error:
        raise HTTPException(409, {"reason": error.reason, "current": error.current}) from None
    except StorageError as error:
        if not writes:
            raise HTTPException(503, f"storage failed; reload and try again: {error}") from None
        detail = "storage failed; this request may or may not have been saved, so reload"
        raise HTTPException(503, f"{detail} before retrying: {error}") from None


@router.post("/sources", status_code=201)
async def upload_source(request: Request) -> dict:
    services = _services(request)
    too_large = f"a source is at most {MAX_SOURCE_BYTES} bytes"
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_SOURCE_BYTES:
        raise HTTPException(413, too_large)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_SOURCE_BYTES:
            raise HTTPException(413, too_large)
    try:
        return await run_in_threadpool(_call, sources.seal, services.store, bytes(body))
    except sources.SourceError as error:
        raise HTTPException(422, {"errors": [str(error)]}) from None


@router.post("/templates", status_code=201)
def create_template(body: NewTemplate, request: Request) -> dict:
    templates = _services(request).templates
    return _call(templates.create, body.source_sha256, body.policy)


@router.get("/templates/{template_id}")
def get_template(template_id: str, request: Request) -> dict:
    return _call(_services(request).templates.get, template_id, writes=False)


@router.put("/templates/{template_id}")
def save_template(template_id: str, body: Save, request: Request) -> dict:
    templates = _services(request).templates
    args = (template_id, body.base_revision, body.policy, body.fields, body.save_id)
    snapshot, changed = _call(templates.save, *args)
    return {"saved": True, "new_revision": changed, "template": snapshot}


@router.post("/templates/{template_id}/review")
def confirm_review(template_id: str, body: Bound, request: Request) -> dict:
    templates = _services(request).templates
    return _call(templates.confirm_review, template_id, body.revision, body.binding)


@router.post("/templates/{template_id}/approval")
def approve(template_id: str, body: Bound, request: Request) -> dict:
    templates = _services(request).templates
    return _call(templates.approve, template_id, body.revision, body.binding)


@router.post("/templates/{template_id}/operation")
def create_signing_template(template_id: str, body: Bound, request: Request) -> dict:
    operations = _services(request).operations
    return _call(operations.create, template_id, body.revision, body.binding)


@router.get("/templates/{template_id}/operation")
def get_operation(template_id: str, request: Request) -> dict:
    return _call(_services(request).operations.get, template_id, writes=False)


@router.post("/templates/{template_id}/operation/status-check")
def check_operation(template_id: str, request: Request) -> dict:
    return _call(_services(request).operations.check_status, template_id)
