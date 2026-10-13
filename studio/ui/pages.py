"""The studio's pages: the library, setup, field drafting, a template's record and its field
editor, rendered on the server.

Every page answers like the state API (studio/state/api.py) and shows only what it can read:
- state not configured: a page saying so (503), with no sample data;
- local: 403 unless the request is a loopback one, as for the API;
- hosted: without a valid session, a redirect to Sign in with Vercel. A page is only ever read
  (GET), and every change it makes goes through the API, which checks the Origin.

Pages carry a Content-Security-Policy that allows only this origin's scripts and styles, plus the
R2 host for a direct upload. Values typed by people (titles, sponsor names) are escaped by Jinja.

The tab icon is the Homebase mark (static/favicon.*), served at /favicon.ico as well, without a
session, like the rest of static/.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from math import ceil
from pathlib import Path
from urllib.parse import urlencode

import jinja2
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.requests import cookie_parser

from studio.services.fields import MERGE_NAMES
from studio.state import identity, lifecycle, sources
from studio.state.api import NOT_CONFIGURED, NOT_LOOPBACK, Services, _loopback
from studio.state.drafts import DRAFT_ID
from studio.state.errors import NotFound
from studio.state.ledger import CAP_USD
from studio.state.policy import ROLES
from studio.state.r2 import R2ObjectStore
from studio.state.store import StorageError
from studio.state.templates import TEMPLATE_ID

ROOT = Path(__file__).resolve().parents[2]
PAGE_SIZE = 10
MODES = {"lp": "LP signature only", "countersign": "LP + sponsor countersignature"}
SORTS = {"updated": "desc", "title": "asc"}  # each column's first direction
TYPE_LABELS = {
    "signature": "signature",
    "initials": "initials",
    "date_signed": "date signed",
    "text": "text",
    "checkbox": "checkbox",
}

environment = jinja2.Environment(
    loader=jinja2.FileSystemLoader(ROOT / "templates"),
    autoescape=True,
    undefined=jinja2.StrictUndefined,
)
templates = Jinja2Templates(env=environment)
pages = APIRouter()


def _asset_version() -> str:
    """A digest of the static files, so a deploy's pages never run an earlier deploy's scripts or
    styles from a browser's cache."""
    digest = hashlib.sha256()
    for path in sorted((ROOT / "static").rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()[:12]


ASSET_VERSION = _asset_version()
environment.globals["asset"] = lambda name: f"/static/{name}?v={ASSET_VERSION}"


@pages.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """The Homebase mark, where browsers and other tools look for a site's icon."""
    return FileResponse(ROOT / "static" / "favicon.ico", media_type="image/x-icon")


def mode_label(countersigner: bool) -> str:
    return MODES["countersign" if countersigner else "lp"]


def when(stamp: str | None) -> str:
    if not stamp:
        return "Not recorded"
    moment = datetime.fromisoformat(stamp)
    return f"{moment:%b} {moment.day}, {moment:%H:%M} UTC"


environment.filters["when"] = when
environment.globals["mode_label"] = mode_label


def _security_headers(response: Response, services: Services | None) -> Response:
    connect = "'self'"
    if services is not None and isinstance(services.store, R2ObjectStore):
        connect += f" https://{services.store.credentials.host}"
    response.headers["content-security-policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        f"connect-src {connect}; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["referrer-policy"] = "same-origin"
    response.headers["cache-control"] = "no-store"
    return response


def _viewer(request: Request) -> tuple[Services | None, dict | None, Response | None]:
    """The services, who is looking, or the response that answers instead of the page."""
    services: Services | None = getattr(request.app.state, "services", None)
    if services is None:
        return None, None, _message(request, None, 503, "Nothing can be saved here", NOT_CONFIGURED)
    if services.auth is None:
        if not _loopback(request.scope):
            return None, None, _message(request, None, 403, "Not available", NOT_LOOPBACK)
        return services, {"local": True}, None
    cookie = cookie_parser(request.headers.get("cookie", "")).get(identity.SESSION_COOKIE)
    actor = identity.session_actor(services.auth, cookie, services.clock())
    if actor is None:
        return None, None, RedirectResponse("/auth/login", 303)
    return services, {"local": False, "name": actor.name, "email": actor.email}, None


def _render(
    request: Request, services: Services | None, name: str, context: dict, status: int = 200
) -> Response:
    response = templates.TemplateResponse(request, name, context, status_code=status)
    return _security_headers(response, services)


def _message(
    request: Request, viewer: dict | None, status: int, title: str, detail: str
) -> Response:
    context = {"viewer": viewer, "title": title, "detail": detail}
    return _render(request, None, "message.html", context, status)


@pages.get("/", response_class=HTMLResponse)
async def library(
    request: Request,
    tab: str = "all",
    q: str = "",
    mode: str = "all",
    sort: str = "updated",
    order: str = "",
    page: int = 1,
) -> Response:
    services, viewer, refusal = _viewer(request)
    if refusal is not None:
        return refusal
    tab = tab if tab in dict(lifecycle.TABS) else "all"
    mode = mode if mode in MODES else "all"
    sort = sort if sort in SORTS else "updated"
    order = order if order in ("asc", "desc") else SORTS[sort]
    query = " ".join(q.split())[:200]
    view = {"tab": tab, "q": query, "mode": mode, "sort": sort, "order": order}
    try:
        summaries = await run_in_threadpool(services.templates.summaries)
    except StorageError:
        context = {"viewer": viewer, "view": view, "failed": True, "total": 0}
        return _render(request, services, "library.html", context, 503)
    rows = [_row(summary) for summary in summaries]
    matching = [row for row in rows if _matches(row, query, mode)]
    counts = {key: 0 for key, _ in lifecycle.TABS}
    for row in matching:
        counts["all"] += 1
        counts[row["status"].tab] += 1
    shown = [row for row in matching if tab == "all" or row["status"].tab == tab]
    key = (lambda row: row["title"].casefold()) if sort == "title" else (lambda r: r["updated"])
    shown.sort(key=key, reverse=order == "desc")
    pages_total = max(1, ceil(len(shown) / PAGE_SIZE))
    page = min(max(page, 1), pages_total)
    start = (page - 1) * PAGE_SIZE

    def link(**changes: object) -> str:
        merged = {**view, "page": page, **changes}
        kept = {k: v for k, v in merged.items() if v not in ("", "all", None, 1)}
        return "/?" + urlencode(kept) if kept else "/"

    context = {
        "viewer": viewer,
        "view": view,
        "failed": False,
        "total": len(rows),
        "tabs": lifecycle.TABS,
        "counts": counts,
        "modes": MODES,
        "rows": shown[start : start + PAGE_SIZE],
        "matching": len(shown),
        "first": start + 1,
        "last": min(start + PAGE_SIZE, len(shown)),
        "page": page,
        "pages_total": pages_total,
        "link": link,
    }
    return _render(request, services, "library.html", context)


def _row(summary: dict) -> dict:
    details = summary.get("details") or {}
    approval, review = summary.get("approval"), summary.get("review")
    if approval:
        attribution = f"Reviewed by {review['by']} · approved by {approval['by']}"
    elif review:
        attribution = f"Reviewed by {review['by']}"
    else:
        attribution = "Not reviewed"
    return {
        "id": summary["id"],
        "title": details.get("title") or "Untitled",
        "sponsor_name": details.get("sponsor_name", ""),
        "sponsor_account_id": details.get("sponsor_account_id", ""),
        "countersigner": summary.get("countersigner"),
        "status": lifecycle.status(summary),
        "updated": summary.get("saved_at") or "",
        "attribution": attribution,
        "error": summary.get("error"),
    }


def _matches(row: dict, query: str, mode: str) -> bool:
    if mode != "all" and (row["countersigner"] is None or row["countersigner"] != (mode != "lp")):
        return False
    if not query:
        return True
    haystack = " ".join([row["title"], row["id"], row["sponsor_name"], row["sponsor_account_id"]])
    return query.casefold() in haystack.casefold()


@pages.get("/new", response_class=HTMLResponse)
def setup(request: Request) -> Response:
    services, viewer, refusal = _viewer(request)
    if refusal is not None:
        return refusal
    context = {
        "viewer": viewer,
        "upload": "direct" if isinstance(services.store, R2ObjectStore) else "server",
        "max_bytes": sources.MAX_SOURCE_BYTES,
    }
    return _render(request, services, "setup.html", context)


@pages.get("/templates/{template_id}/analyzing", response_class=HTMLResponse)
async def analyzing(request: Request, template_id: str, start: str = "") -> Response:
    """Field drafting for one template. The browser drives it through the drafts API; in local
    mode the page says where drafting runs instead."""
    services, viewer, refusal = _viewer(request)
    if refusal is not None:
        return refusal
    if not TEMPLATE_ID.fullmatch(template_id):
        return _message(request, viewer, 404, "No such template", "Check the link.")
    try:
        snapshot = await run_in_threadpool(services.templates.get, template_id)
        source = await run_in_threadpool(sources.load, services.store, snapshot["source_sha256"])
    except NotFound:
        return _message(request, viewer, 404, "No such template", "Check the link.")
    except StorageError:
        detail = "Something went wrong reading this template. Nothing was changed; reload to retry."
        return _message(request, viewer, 503, "This template could not be loaded", detail)
    context = {
        "viewer": viewer,
        "template": snapshot,
        "source": source,
        "cap": f"{CAP_USD:.2f}",
        "autostart": start == "1",
    }
    return _render(request, services, "analyzing.html", context)


@pages.get("/templates/{template_id}/edit", response_class=HTMLResponse)
async def editor(request: Request, template_id: str, drafted: str = "") -> Response:
    """The field editor. The page carries the saved revision and the source's page sizes; every
    save, review, approval and create goes through the API. `drafted` names the draft the
    drafting page just finished, so a draft that found no fields can say so here."""
    services, viewer, refusal = _viewer(request)
    if refusal is not None:
        return refusal
    if not TEMPLATE_ID.fullmatch(template_id):
        return _message(request, viewer, 404, "No such template", "Check the link.")
    try:
        snapshot = await run_in_threadpool(services.templates.get, template_id)
        source = await run_in_threadpool(sources.load, services.store, snapshot["source_sha256"])
        operation = await run_in_threadpool(_operation, services, template_id)
        outcome = await run_in_threadpool(
            _drafted, services, template_id, snapshot["revision"], drafted
        )
    except NotFound:
        return _message(request, viewer, 404, "No such template", "Check the link.")
    except StorageError:
        detail = "Something went wrong reading this template. Nothing was changed; reload to retry."
        return _message(request, viewer, 503, "This template could not be loaded", detail)
    if source is None:
        detail = "The sealed source of this template is missing, so its pages cannot be shown."
        return _message(request, viewer, 503, "This template could not be loaded", detail)
    if not source["pages"]:
        detail = "The source of this template has no pages, so there is nothing to place fields on."
        return _message(request, viewer, 422, "This template cannot be edited", detail)
    data = {
        "template": snapshot,
        "pages": source["pages"],
        "operation": operation,
        "drafted": outcome,
        "roles": list(ROLES),
        "merge_names": list(MERGE_NAMES),
        "modes": MODES,
    }
    context = {"viewer": viewer, "template": snapshot, "data": data}
    return _render(request, services, "editor.html", context)


@pages.get("/templates/{template_id}", response_class=HTMLResponse)
async def record(request: Request, template_id: str) -> Response:
    services, viewer, refusal = _viewer(request)
    if refusal is not None:
        return refusal
    if not TEMPLATE_ID.fullmatch(template_id):
        return _message(request, viewer, 404, "No such template", "Check the link.")
    try:
        snapshot = await run_in_threadpool(services.templates.get, template_id)
        source = await run_in_threadpool(sources.load, services.store, snapshot["source_sha256"])
        operation = await run_in_threadpool(_operation, services, template_id)
    except NotFound:
        return _message(request, viewer, 404, "No such template", "Check the link.")
    except StorageError:
        detail = "Something went wrong reading this template. Nothing was changed; reload to retry."
        return _message(request, viewer, 503, "This template could not be loaded", detail)
    summary = {
        "revision": snapshot["revision"],
        "approval": snapshot["approval"],
        "field_count": len(snapshot["fields"]),
        "operation": operation,
    }
    by_role: dict[str, int] = {}
    for field in snapshot["fields"]:
        role = "Merge (sender)" if field["signer"] == "sender" else ROLES[field["signer"]]
        by_role[role] = by_role.get(role, 0) + 1
    required = [
        f"{ROLES[item['signer']]} {TYPE_LABELS.get(item['type'], item['type'])}"
        for item in snapshot["policy"]["required"]
    ]
    context = {
        "viewer": viewer,
        "template": snapshot,
        "source": source,
        "operation": operation,
        "status": lifecycle.status(summary),
        "by_role": by_role,
        "required": required,
    }
    return _render(request, services, "record.html", context)


def _drafted(services: Services, template_id: str, revision: int, hint: str) -> dict | None:
    """What drafting left on the editor's revision: from the draft that saved it, or from the
    draft the drafting page names when that draft found no fields to save."""
    if services.drafts is None:
        return None
    head, _ = services.templates.head(template_id)
    made = head.get("save_id") or ""
    draft_id = made.removeprefix("draft-") if made.startswith("draft-") else hint
    if head["revision"] != revision or not DRAFT_ID.fullmatch(draft_id):
        return None
    try:
        return services.drafts.outcome(template_id, draft_id, revision)
    except NotFound:
        return None


def _operation(services: Services, template_id: str) -> dict | None:
    try:
        return services.operations.get(template_id)
    except NotFound:
        return None
