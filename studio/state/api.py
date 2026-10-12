"""HTTP routes for sources, templates, review and approval, and the signing operation.

The routes answer 503 unless saved state is configured, and only local disk can be configured. A
deployment's disk is ephemeral, so on Vercel nothing is configured: saving there would be a false
Saved. The upload route is the local stand-in for the direct R2 upload, which waits on Alex's R2
decisions (guide §10).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict

from studio.state import sources
from studio.state.errors import Conflict, Invalid, NotFound
from studio.state.signing import Operations, SimulatedSigningAdapter
from studio.state.store import LocalObjectStore, ObjectStore, StorageError
from studio.state.templates import Templates

STATE_DIR_ENV = "STUDIO_STATE_DIR"
MAX_SOURCE_BYTES = sources.MAX_SOURCE_BYTES


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
    resolved = Path(root).resolve()
    if resolved.is_relative_to(repository.resolve()):
        raise RuntimeError(f"{STATE_DIR_ENV} must be outside the repository")
    return local_services(resolved)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NewTemplate(_Body):
    source_sha256: str
    policy: dict[str, Any]


class Save(_Body):
    base_revision: int
    policy: dict[str, Any]
    fields: list[Any]


class Bound(_Body):
    revision: int
    binding: str


router = APIRouter(prefix="/api")


def _services(request: Request) -> Services:
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(503, "saved state is not configured here; nothing can be saved")
    return services


def _call(action: Callable[..., Any], *args: Any) -> Any:
    try:
        return action(*args)
    except NotFound:
        raise HTTPException(404, "no such template or operation") from None
    except Invalid as error:
        raise HTTPException(422, {"errors": error.errors}) from None
    except Conflict as error:
        raise HTTPException(409, {"reason": error.reason, "current": error.current}) from None
    except StorageError as error:
        raise HTTPException(503, f"storage failed, nothing was saved: {error}") from None


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
    return _call(_services(request).templates.get, template_id)


@router.put("/templates/{template_id}")
def save_template(template_id: str, body: Save, request: Request) -> dict:
    templates = _services(request).templates
    args = (template_id, body.base_revision, body.policy, body.fields)
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
    return _call(_services(request).operations.get, template_id)


@router.post("/templates/{template_id}/operation/status-check")
def check_operation(template_id: str, request: Request) -> dict:
    return _call(_services(request).operations.check_status, template_id)
