"""HTTP routes for sources, templates, review and approval, the signing operation, and sign-in.

Saved state runs in one of two modes, or not at all (`configure`):
- Local: STUDIO_STATE_DIR names a directory outside the repository. Only loopback requests are
  answered, and nobody signs in: sign-offs are recorded `by: anonymous`.
- Hosted: R2 storage and Sign in with Vercel. This is the only mode on Vercel, where local disk is
  ephemeral and saving there would be a false Saved. It needs every HOSTED_ENV setting; a missing
  or malformed one leaves state unconfigured, and /healthz names it (never its value). A Vercel
  preview may leave STUDIO_PUBLIC_ORIGIN unset and use its branch URL instead.

`StateGate` answers before FastAPI reads or validates a body:
- 503 when state is not configured;
- local: 403 unless the request comes from a loopback address, is addressed to a loopback host,
  and any page that sent it is served from one;
- hosted: 401 without a valid session, and 403 for a request that changes anything unless its
  Origin is the studio's own (STUDIO_PUBLIC_ORIGIN);
- 413 when a JSON body is over MAX_JSON_BYTES, by its declared length or by the bytes that arrive.
The upload route bounds its own body at MAX_SOURCE_BYTES.

The loopback check keeps other machines (even when the server listens on every interface), other
sites' pages and rebound host names away from local state. There is no proxy support. uvicorn
trusts X-Forwarded-For from a proxy on this machine by default, so a forwarded request is judged
by the address the proxy reports. It is not an identity: anyone who can run a request on this
machine is answered.

Field drafting (`studio.state.drafts`) runs only in hosted mode: its model credential is the OIDC
token Vercel sends with each request, and its spend ledger is the one in R2. Each step request
makes at most one model call.

Hosted uploads go straight from the browser to R2: POST /api/uploads returns a presigned PUT for
one hash and one length (a Vercel function accepts at most 4.5 MB of request body), and POST
/api/uploads/{sha256}/seal reads the bytes back, re-hashes them and seals them.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from studio.services import gateway
from studio.state import identity, lifecycle, sources
from studio.state.drafts import Drafts, Unavailable
from studio.state.errors import Conflict, Invalid, NotFound
from studio.state.r2 import UPLOAD_EXPIRY_S, Credentials, R2ObjectStore
from studio.state.signing import ORPHANED, Operations, SimulatedSigningAdapter
from studio.state.store import LocalObjectStore, ObjectStore, StorageError
from studio.state.templates import ACTOR, Templates

STATE_DIR_ENV = "STUDIO_STATE_DIR"
HOSTED_ENV = (
    "R2_ACCOUNT_ID",
    "R2_BUCKET",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "VERCEL_OAUTH_CLIENT_ID",
    "VERCEL_OAUTH_CLIENT_SECRET",
    "STUDIO_SESSION_SECRET",
    "STUDIO_PUBLIC_ORIGIN",
)
BRANCH_URL_ENV = "VERCEL_BRANCH_URL"  # set by Vercel: the branch's host, without https://
MAX_SOURCE_BYTES = sources.MAX_SOURCE_BYTES
MAX_JSON_BYTES = 1024 * 1024  # a template at its 2,000-field limit is about 0.5 MB
NOT_CONFIGURED = "saved state is not configured here; nothing can be saved"
SIGN_IN_NOT_CONFIGURED = "sign-in is not configured here"
UPLOAD_PATH = "/api/sources"
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
NOT_LOOPBACK = "saved state answers only loopback requests from loopback pages"
NOT_SIGNED_IN = "sign in first at /auth/login"
CROSS_ORIGIN = "a change must come from the studio's own pages"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
OIDC_HEADER = "x-vercel-oidc-token"  # sent by Vercel to each function request
HOSTED_ONLY = (
    "field drafting runs on the hosted studio, where one spend ledger holds the $10 cap; "
    "local state has no model credential"
)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Services:
    store: ObjectStore
    templates: Templates
    operations: Operations
    auth: identity.AuthConfig | None = None  # None: local loopback mode, nobody signs in
    clock: Callable[[], datetime] = utc_now
    drafts: Drafts | None = None


@dataclass(frozen=True)
class Configuration:
    services: Services | None
    state: str  # "local", "r2" or "not_configured"
    problems: tuple[str, ...] = field(default=())


def local_services(root: Path, clock: Callable[[], datetime] = utc_now) -> Services:
    return _services_on(LocalObjectStore(root), None, clock)


def hosted_services(
    store: ObjectStore, auth: identity.AuthConfig, clock: Callable[[], datetime] = utc_now
) -> Services:
    return _services_on(store, auth, clock)


def _services_on(
    store: ObjectStore, auth: identity.AuthConfig | None, clock: Callable[[], datetime]
) -> Services:
    templates = Templates(store, clock)
    operations = Operations(store, templates, SimulatedSigningAdapter(store), clock)
    drafts = Drafts(store, templates, clock)
    return Services(store, templates, operations, auth, clock, drafts)


def configure(environ: Mapping[str, str], repository: Path) -> Configuration:
    """Local state off Vercel when STUDIO_STATE_DIR is set; hosted state on Vercel, or off it when
    any hosted setting is present and STUDIO_STATE_DIR is not."""
    on_vercel = bool(environ.get("VERCEL"))
    root = environ.get(STATE_DIR_ENV)
    if not on_vercel and root:
        if Path(root).resolve().is_relative_to(repository.resolve()):
            raise RuntimeError(f"{STATE_DIR_ENV} must be outside the repository")
        local = local_services(Path(root).absolute())  # unresolved, so a link is seen and refused
        return Configuration(local, "local")
    if not on_vercel and not any(environ.get(name) for name in HOSTED_ENV):
        return Configuration(None, "not_configured")
    if on_vercel:
        environ = _with_branch_origin(environ)
    problems = _hosted_problems(environ)
    if problems:
        return Configuration(None, "not_configured", problems)
    credentials = Credentials(
        environ["R2_ACCOUNT_ID"],
        environ["R2_BUCKET"],
        environ["R2_ACCESS_KEY_ID"],
        environ["R2_SECRET_ACCESS_KEY"],
    )
    auth = identity.AuthConfig(
        environ["VERCEL_OAUTH_CLIENT_ID"],
        environ["VERCEL_OAUTH_CLIENT_SECRET"],
        environ["STUDIO_SESSION_SECRET"],
        environ["STUDIO_PUBLIC_ORIGIN"],
    )
    return Configuration(hosted_services(R2ObjectStore(credentials), auth), "r2")


def _with_branch_origin(environ: Mapping[str, str]) -> Mapping[str, str]:
    """On a Vercel preview without STUDIO_PUBLIC_ORIGIN, the branch URL is the origin: one fixed
    origin cannot serve every preview branch, and a branch keeps its URL across deployments."""
    branch = environ.get(BRANCH_URL_ENV)
    if environ.get("STUDIO_PUBLIC_ORIGIN") or environ.get("VERCEL_ENV") != "preview" or not branch:
        return environ
    return {**environ, "STUDIO_PUBLIC_ORIGIN": f"https://{branch}"}


def _hosted_problems(environ: Mapping[str, str]) -> tuple[str, ...]:
    problems = []
    for name in HOSTED_ENV:
        value = environ.get(name, "")
        if not value:
            problems.append(f"{name} is missing")
        elif value != value.strip() or value[:1] in "\"'" or value[-1:] in "\"'":
            problems.append(f"{name} has stray spaces or quotes")
    if problems:
        return tuple(problems)
    if not re.fullmatch(r"[0-9a-f]{32}", environ["R2_ACCOUNT_ID"]):
        problems.append("R2_ACCOUNT_ID is not a 32-character account ID")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", environ["R2_BUCKET"]):
        problems.append("R2_BUCKET is not a bucket name")
    if len(environ["STUDIO_SESSION_SECRET"]) < identity.MIN_SECRET_CHARS:
        problems.append(f"STUDIO_SESSION_SECRET is shorter than {identity.MIN_SECRET_CHARS}")
    if _origin(environ["STUDIO_PUBLIC_ORIGIN"]) != environ["STUDIO_PUBLIC_ORIGIN"]:
        problems.append("STUDIO_PUBLIC_ORIGIN is not an origin like https://host")
    return tuple(problems)


def _origin(value: str) -> str | None:
    """The origin as a browser sends it (lowercase host, no default port), or None. Anything else
    would never equal a request's Origin or Host, and every change would be refused."""
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname
    if not host or parts.username is not None or parts.path or parts.query or parts.fragment:
        return None
    if not (parts.scheme == "https" or (parts.scheme == "http" and host in LOOPBACK)):
        return None
    host = f"[{host}]" if ":" in host else host
    default = 443 if parts.scheme == "https" else 80
    return f"{parts.scheme}://{host}" + ("" if port in (None, default) else f":{port}")


def report(configuration: Configuration) -> None:
    """Says loudly, at startup, why hosted state is off."""
    for problem in configuration.problems:
        print(f"template-studio: saved state is off: {problem}", file=sys.stderr)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NewTemplate(_Body):
    source_sha256: str
    policy: dict[str, Any]
    details: dict[str, Any] | None = None  # title and sponsor account; blank when left out


class Save(_Body):
    base_revision: int
    policy: dict[str, Any]
    fields: list[Any]
    save_id: str  # chosen by the client per save; a retry of the same request reuses it
    details: dict[str, Any] | None = None  # left out: the base revision's details are kept


class Bound(_Body):
    revision: int
    binding: str


class UploadRequest(_Body):
    sha256: str
    size: int


class Resolution(_Body):
    note: str  # what the person found at the provider


class DraftStart(_Body):
    base_revision: int


router = APIRouter(prefix="/api")
auth_router = APIRouter(prefix="/auth")


class StateGate:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = _state_route_path(scope)
        if path is None:
            await self.app(scope, receive, send)
            return
        services = getattr(scope["app"].state, "services", None)
        if services is None:
            await _answer(scope, receive, send, 503, NOT_CONFIGURED)
            return
        if services.auth is None:
            if not _loopback(scope):
                await _answer(scope, receive, send, 403, NOT_LOOPBACK)
                return
        else:
            headers = Headers(scope=scope)
            cookies = cookie_parser(headers.get("cookie", ""))
            cookie = cookies.get(identity.SESSION_COOKIE)
            actor = identity.session_actor(services.auth, cookie, services.clock())
            if actor is None:
                await _answer(scope, receive, send, 401, NOT_SIGNED_IN)
                return
            if scope["method"] not in SAFE_METHODS and not _same_origin(headers, services.auth):
                await _answer(scope, receive, send, 403, CROSS_ORIGIN)
                return
            scope.setdefault("state", {})["actor"] = actor
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


def _same_origin(headers: Headers, auth: identity.AuthConfig) -> bool:
    return headers.get("origin") == auth.origin


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


def _by(request: Request) -> str:
    actor = getattr(request.state, "actor", None)
    return ACTOR if actor is None else actor.email  # an email only from a verified sign-in


def _call(action: Callable[..., Any], *args: Any, writes: bool = True) -> Any:
    try:
        return action(*args)
    except NotFound:
        raise HTTPException(404, "no such template or operation") from None
    except Invalid as error:
        raise HTTPException(422, {"errors": error.errors}) from None
    except Conflict as error:
        raise HTTPException(409, {"reason": error.reason, "current": error.current}) from None
    except Unavailable as error:
        raise HTTPException(503, str(error)) from None
    except StorageError as error:
        if not writes:
            raise HTTPException(503, f"storage failed; reload and try again: {error}") from None
        detail = "storage failed; this request may or may not have been saved, so reload"
        raise HTTPException(503, f"{detail} before retrying: {error}") from None


async def _seal(store: ObjectStore, data: bytes) -> dict:
    try:
        return await run_in_threadpool(_call, sources.seal, store, data)
    except sources.SourceError as error:
        raise HTTPException(422, {"errors": [str(error)]}) from None


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
    return await _seal(services.store, bytes(body))


def _r2(services: Services) -> R2ObjectStore:
    if not isinstance(services.store, R2ObjectStore):
        raise HTTPException(409, "direct upload needs R2 storage; POST the PDF to /api/sources")
    return services.store


def _upload_key(sha256: str) -> str:
    try:
        return sources.upload_key(sha256)
    except sources.SourceError as error:
        raise HTTPException(422, {"errors": [str(error)]}) from None


@router.post("/uploads", status_code=201)
def request_upload(body: UploadRequest, request: Request) -> dict:
    store = _r2(_services(request))
    key = _upload_key(body.sha256)
    if body.size > MAX_SOURCE_BYTES:
        raise HTTPException(413, f"a source is at most {MAX_SOURCE_BYTES} bytes")
    if body.size < 1:
        raise HTTPException(422, {"errors": ["size must be the file's length in bytes"]})
    url = store.presign_put(key, body.size)
    return {"method": "PUT", "url": url, "expires_in": UPLOAD_EXPIRY_S, "size": body.size}


@router.post("/uploads/{sha256}/seal", status_code=201)
async def seal_upload(sha256: str, request: Request) -> dict:
    services = _services(request)
    store = _r2(services)
    key = _upload_key(sha256)
    stored = await run_in_threadpool(_call, store.get, key, writes=False)
    if stored is None:
        raise HTTPException(404, "nothing was uploaded under that hash; upload it first")
    if len(stored.data) > MAX_SOURCE_BYTES:
        raise HTTPException(413, f"a source is at most {MAX_SOURCE_BYTES} bytes")
    if hashlib.sha256(stored.data).hexdigest() != sha256:
        raise HTTPException(422, {"errors": ["the uploaded bytes do not match the hash"]})
    return await _seal(services.store, stored.data)


@router.post("/templates", status_code=201)
def create_template(body: NewTemplate, request: Request) -> dict:
    templates = _services(request).templates
    return _call(templates.create, body.source_sha256, body.policy, body.details)


@router.get("/templates")
def list_templates(request: Request) -> dict:
    rows = _call(_services(request).templates.summaries, writes=False)
    return {"templates": [{**row, "status": lifecycle.status(row).label} for row in rows]}


@router.get("/templates/{template_id}")
def get_template(template_id: str, request: Request) -> dict:
    return _call(_services(request).templates.get, template_id, writes=False)


@router.put("/templates/{template_id}")
def save_template(template_id: str, body: Save, request: Request) -> dict:
    templates = _services(request).templates
    args = (template_id, body.base_revision, body.policy, body.fields, body.save_id, body.details)
    snapshot, changed = _call(templates.save, *args)
    return {"saved": True, "new_revision": changed, "template": snapshot}


@router.post("/templates/{template_id}/review")
def confirm_review(template_id: str, body: Bound, request: Request) -> dict:
    templates = _services(request).templates
    args = (template_id, body.revision, body.binding, _by(request))
    return _call(templates.confirm_review, *args)


@router.post("/templates/{template_id}/approval")
def approve(template_id: str, body: Bound, request: Request) -> dict:
    templates = _services(request).templates
    return _call(templates.approve, template_id, body.revision, body.binding, _by(request))


def _with_orphans(operations: Operations, template_id: str, operation: dict, writes: bool) -> dict:
    """An operation answer lists unresolved orphans, with a warning while any remain. After a
    write, a failure to read them must not read as the write having failed."""
    orphans = _call(operations.orphans, template_id, writes=writes)
    view = {**operation, "orphans": orphans}
    if orphans:
        view["warning"] = ORPHANED
    return view


@router.post("/templates/{template_id}/operation")
def create_signing_template(template_id: str, body: Bound, request: Request) -> dict:
    operations = _services(request).operations
    operation = _call(operations.create, template_id, body.revision, body.binding)
    return _with_orphans(operations, template_id, operation, writes=True)


@router.get("/templates/{template_id}/operation")
def get_operation(template_id: str, request: Request) -> dict:
    operations = _services(request).operations
    operation = _call(operations.get, template_id, writes=False)
    return _with_orphans(operations, template_id, operation, writes=False)


@router.post("/templates/{template_id}/operation/status-check")
def check_operation(template_id: str, request: Request) -> dict:
    operations = _services(request).operations
    operation = _call(operations.check_status, template_id)
    return _with_orphans(operations, template_id, operation, writes=True)


@router.post("/templates/{template_id}/operation/orphans/{operation_id}/resolve")
def resolve_orphan(template_id: str, operation_id: str, body: Resolution, request: Request) -> dict:
    operations = _services(request).operations
    return _call(operations.resolve, template_id, operation_id, body.note, _by(request))


def _drafts(request: Request) -> Drafts:
    services = _services(request)
    if services.auth is None or services.drafts is None:
        raise HTTPException(409, HOSTED_ONLY)
    return services.drafts


def _model_token(request: Request) -> str | None:
    """The request's OIDC token, else the environment's. Never logged, stored or returned."""
    token = request.headers.get(OIDC_HEADER, "").strip()
    if token:
        return token
    try:
        return gateway.read_token()
    except gateway.GatewayError:
        return None


@router.post("/templates/{template_id}/drafts", status_code=201)
def start_draft(template_id: str, body: DraftStart, request: Request) -> dict:
    drafts = _drafts(request)
    return _call(drafts.start, template_id, body.base_revision, _by(request))


@router.get("/templates/{template_id}/drafts/latest")
def latest_draft(template_id: str, request: Request) -> dict:
    return {"draft": _call(_drafts(request).latest, template_id, writes=False)}


@router.get("/templates/{template_id}/drafts/{draft_id}")
def get_draft(template_id: str, draft_id: str, request: Request) -> dict:
    return _call(_drafts(request).view, template_id, draft_id, writes=False)


@router.post("/templates/{template_id}/drafts/{draft_id}/step")
def step_draft(template_id: str, draft_id: str, request: Request) -> dict:
    drafts = _drafts(request)
    return _call(drafts.step, template_id, draft_id, _model_token(request))


@router.get("/templates/{template_id}/drafts/{draft_id}/result")
def draft_result(template_id: str, draft_id: str, request: Request) -> dict:
    return _call(_drafts(request).result, template_id, draft_id, writes=False)


@router.post("/templates/{template_id}/drafts/{draft_id}/apply")
def apply_draft(template_id: str, draft_id: str, request: Request) -> dict:
    drafts = _drafts(request)
    return _call(drafts.apply, template_id, draft_id, _by(request))


def _auth(request: Request) -> tuple[identity.AuthConfig, Services]:
    services = getattr(request.app.state, "services", None)
    if services is None or services.auth is None:
        raise HTTPException(503, SIGN_IN_NOT_CONFIGURED)
    return services.auth, services


def _secure(auth: identity.AuthConfig) -> bool:
    return auth.origin.startswith("https://")


@auth_router.get("/login")
def login(request: Request) -> RedirectResponse:
    auth, services = _auth(request)
    if request.headers.get("host") != urlsplit(auth.origin).netloc:
        return RedirectResponse(f"{auth.origin}/auth/login", 303)  # the registered callback
    url, cookie = identity.start(auth, services.clock())
    response = RedirectResponse(url, 303)
    max_age = int(identity.LOGIN_TTL.total_seconds())
    response.set_cookie(
        identity.LOGIN_COOKIE,
        cookie,
        max_age=max_age,
        path="/auth",
        httponly=True,
        secure=_secure(auth),
        samesite="lax",
    )
    return response


@auth_router.get("/callback")
async def callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    auth, services = _auth(request)
    if error:
        raise HTTPException(400, "sign-in was cancelled or refused at Vercel; sign in again")
    login_cookie = request.cookies.get(identity.LOGIN_COOKIE)
    args = (auth, code, state, login_cookie, services.clock(), identity.urllib_transport)
    try:
        _, session = await run_in_threadpool(identity.finish, *args)
    except identity.SignInFailed as failure:
        raise HTTPException(400, str(failure)) from None
    response = RedirectResponse("/auth/me", 303)
    response.set_cookie(
        identity.SESSION_COOKIE,
        session,
        max_age=int(identity.SESSION_TTL.total_seconds()),
        path="/",
        httponly=True,
        secure=_secure(auth),
        samesite="lax",
    )
    response.delete_cookie(identity.LOGIN_COOKIE, path="/auth")
    return response


@auth_router.get("/me", response_model=None)
def me(request: Request) -> dict | RedirectResponse:
    auth, services = _auth(request)
    cookie = request.cookies.get(identity.SESSION_COOKIE)
    actor = identity.session_actor(auth, cookie, services.clock())
    if actor is None:
        raise HTTPException(401, NOT_SIGNED_IN)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", 303)  # a browser arriving from sign-in goes to the library
    return {"email": actor.email, "name": actor.name, "identity": "Sign in with Vercel"}


@auth_router.post("/logout")
def logout(request: Request) -> JSONResponse:
    auth, _ = _auth(request)
    if not _same_origin(request.headers, auth):
        raise HTTPException(403, CROSS_ORIGIN)
    note = "signed out of the studio; this does not sign you out of Vercel"
    response = JSONResponse({"signed_out": True, "note": note})
    response.delete_cookie(identity.SESSION_COOKIE, path="/")
    return response
