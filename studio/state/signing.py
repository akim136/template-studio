"""Creating the signing template: a simulated adapter, and operations that survive an unknown
outcome.

This repository holds no signing-provider credentials and no adapter that reaches a provider. The
only adapter is simulated: it records a template in the local store and sends nothing. There is no
send method at all.

An operation is recorded before the adapter is called, and moves through these states:
- CREATING: the create call is being made.
- VERIFYING: the adapter reported a create; it is being read back.
- CREATED: read back, with the payload that was sent.
- FAILED: the adapter refused, or a status check found nothing after the grace period. Nothing
  exists, so a new create is allowed.
- NEEDS_STATUS_CHECK: the outcome is unknown (an error, a timeout, a crash mid-call, a create that
  cannot be read back). The operation keeps its ID, and only a status check is offered.

A template has at most one operation that is not FAILED, so a create is never repeated blindly.

A create the adapter reports, or a status check finds at the provider, is written onto the stored
operation even if a concurrent status check moved it meanwhile (to FAILED, say), so it is never
lost or left FAILED. If the operation was replaced by another meanwhile, it takes the slot back
when the replacement is FAILED, and an orphan record an earlier report left for it says so.
Otherwise the provider may hold both, and the earlier one is kept under templates/{id}/orphans/
with a note to resolve it by hand; a replacement declared FAILED whose create lands later finds
the slot taken and is kept the same way. A status check that would change nothing writes nothing,
so repeated checks cannot crowd out a create being recorded. If recording the operation fails
before the adapter is called, the operation is marked FAILED (not attempted) where it landed; if
recording the outcome fails after the call, the error says to check its status, unless an orphan
could not be kept, which a status check cannot find: that error names the operation instead.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from studio.services.fields import COORDINATE_SPACE
from studio.state import sources
from studio.state.errors import Conflict, NotFound
from studio.state.policy import parse_policy
from studio.state.store import ObjectStore, PreconditionFailed, StorageError, canonical, digest
from studio.state.templates import Templates

CREATING, VERIFYING, CREATED = "CREATING", "VERIFYING", "CREATED"
FAILED, NEEDS_STATUS_CHECK = "FAILED", "NEEDS_STATUS_CHECK"
# Not finding a create counts as "nothing was created" only after this long. It must exceed any
# real adapter's create timeout, or a slow create could land after it was declared failed.
GRACE = timedelta(minutes=2)
RECORD_ATTEMPTS = 10  # concurrent writes a create being recorded can lose to before it gives up
REPLACED = (
    "this operation's create was reported or found after the operation was replaced by another; "
    "the provider may hold both, so resolve by hand"
)
RECLAIMED = "this operation took its slot back after its replacement failed; see operation.json"


class Refused(Exception):
    """The provider definitely did not create the template."""


class OutcomeUnknown(Exception):
    """The adapter cannot tell whether the create happened."""


class Unkept(StorageError):
    """A replaced operation's create could not be kept as an orphan. A status check cannot find
    it, so the error names it."""


@dataclass(frozen=True)
class Found:
    external_id: str
    payload_sha256: str


class SigningAdapter(Protocol):
    def create(self, operation_id: str, payload: dict) -> str: ...

    def find(self, operation_id: str) -> Found | None: ...


class SimulatedSigningAdapter:
    """Records a 'created' template in the local store. Sends nothing; holds no credentials."""

    def __init__(self, store: ObjectStore) -> None:
        self.store = store

    def create(self, operation_id: str, payload: dict) -> str:
        record = {"external_id": f"simulated-{operation_id}", "payload_sha256": digest(payload)}
        try:
            self.store.put_new(f"simulated-signing/{operation_id}.json", canonical(record))
        except PreconditionFailed:
            pass  # this operation was created before: creating is idempotent per operation
        except StorageError as error:
            raise OutcomeUnknown(str(error)) from None
        return record["external_id"]

    def find(self, operation_id: str) -> Found | None:
        try:
            stored = self.store.get(f"simulated-signing/{operation_id}.json")
        except StorageError as error:
            raise OutcomeUnknown(str(error)) from None
        return None if stored is None else Found(**json.loads(stored.data))


class Operations:
    def __init__(
        self,
        store: ObjectStore,
        templates: Templates,
        adapter: SigningAdapter,
        clock: Callable[[], datetime],
    ) -> None:
        self.store = store
        self.templates = templates
        self.adapter = adapter
        self.clock = clock

    def get(self, template_id: str) -> dict:
        self.templates.head(template_id)  # 404 for an unknown template
        stored = self.store.get(self._key(template_id))
        if stored is None:
            raise NotFound(template_id)
        return json.loads(stored.data)

    def create(self, template_id: str, revision: int, bound: str) -> dict:
        snapshot = self.templates.require_approved(template_id, revision, bound)
        sources.source_bytes(self.store, snapshot["source_sha256"])  # sealed and intact
        payload = {
            "source_sha256": snapshot["source_sha256"],
            "coordinate_space": COORDINATE_SPACE,
            "roles": parse_policy(snapshot["policy"]).roles,
            "fields": [{k: v for k, v in f.items() if k != "id"} for f in snapshot["fields"]],
        }
        key = self._key(template_id)
        current = self.store.get(key)
        if current is not None:
            existing = json.loads(current.data)
            if existing["state"] == CREATED:
                raise Conflict("the template was already created", existing)
            if existing["state"] != FAILED:
                reason = "an operation is open: check its status, do not create again"
                raise Conflict(reason, existing)
        now = self._now()
        operation = {
            "id": uuid.uuid4().hex,
            "template_id": template_id,
            "revision": revision,
            "binding": bound,
            "payload_sha256": digest(payload),
            "state": CREATING,
            "external_id": None,
            "reported_created": False,
            "started_at": now,
            "updated_at": now,
            "note": "",
        }
        try:
            if current is None:
                etag = self.store.put_new(key, canonical(operation))
            else:
                etag = self.store.replace(key, canonical(operation), current.etag)
        except PreconditionFailed:
            reason = "another create started: check its status"
            raise Conflict(reason, self.get(template_id)) from None
        except StorageError:
            self._not_attempted(key, operation)
            raise
        try:
            external_id = self.adapter.create(operation["id"], payload)
        except Refused as error:
            return self._after_call(key, operation, etag, FAILED, note=f"refused: {error}")
        except Exception as error:  # a timeout or any error: the create may have happened
            note = f"create outcome unknown ({type(error).__name__})"
            return self._after_call(key, operation, etag, NEEDS_STATUS_CHECK, note=note)
        try:
            operation, etag = self._record(key, operation, etag, external_id, VERIFYING)
            if etag is None:
                return operation  # created already, or replaced: its state stands
            return self._settle(key, operation, etag)
        except Unkept:
            raise
        except StorageError as error:
            raise _unrecorded(error) from None

    def check_status(self, template_id: str) -> dict:
        self.templates.head(template_id)
        key = self._key(template_id)
        stored = self.store.get(key)
        if stored is None:
            raise NotFound(template_id)
        operation = json.loads(stored.data)
        if operation["state"] in (CREATED, FAILED):
            return operation
        return self._settle(key, operation, stored.etag)

    def _settle(self, key: str, operation: dict, etag: str) -> dict:
        try:
            found = self.adapter.find(operation["id"])
        except Exception as error:
            note = f"status unknown ({type(error).__name__}); check again"
            return self._move(key, operation, etag, NEEDS_STATUS_CHECK, note=note)[0]
        if found is not None:
            if found.payload_sha256 != operation["payload_sha256"]:
                note = "the provider holds different content for this operation; resolve by hand"
                return self._move(key, operation, etag, NEEDS_STATUS_CHECK, note=note)[0]
            return self._record(key, operation, etag, found.external_id, CREATED)[0]
        if operation["reported_created"]:
            note = "the adapter reported a create that cannot be found yet; check again"
            return self._move(key, operation, etag, NEEDS_STATUS_CHECK, note=note)[0]
        if self.clock() - datetime.fromisoformat(operation["started_at"]) < GRACE:
            # Too early to conclude, and writing now could clobber a create still in flight.
            return {**operation, "note": "not found yet; the create may still be running"}
        note = "nothing was created; a new create is allowed"
        return self._move(key, operation, etag, FAILED, note=note)[0]

    def _record(
        self, key: str, operation: dict, etag: str, external_id: str, state: str
    ) -> tuple[dict, str | None]:
        """Writes a create the adapter reported (VERIFYING) or the provider holds (CREATED) onto
        the stored operation, whatever a concurrent status check did to it meanwhile. A CREATED
        operation stands; one replaced by an operation that FAILED takes the slot back, and one
        replaced by any other is kept as an orphan. The etag is None when nothing was written
        here."""
        current = operation
        for _ in range(RECORD_ATTEMPTS):
            replaced = current["id"] != operation["id"]
            if replaced and current["state"] != FAILED:
                return self._orphan(operation, external_id), None
            if current["state"] == CREATED:
                return current, None
            changes = {"external_id": external_id, "reported_created": True, "note": ""}
            mine = operation if replaced else current
            recorded = {**mine, **changes, "state": state, "updated_at": self._now()}
            try:
                etag = self.store.replace(key, canonical(recorded), etag)
            except PreconditionFailed:
                stored = self.store.get(key)
                current, etag = json.loads(stored.data), stored.etag
                continue
            if replaced:
                self._reclaimed(operation)
            return recorded, etag
        raise StorageError("the operation kept changing while its create was recorded")

    def _orphan(self, operation: dict, external_id: str) -> dict:
        changes = {"external_id": external_id, "reported_created": True, "note": REPLACED}
        orphan = {**operation, **changes, "state": NEEDS_STATUS_CHECK, "updated_at": self._now()}
        key = self._orphan_key(operation)
        try:
            self.store.put_new(key, canonical(orphan))
        except PreconditionFailed:  # kept already, by the report or by a status check
            return json.loads(self.store.get(key).data)
        except StorageError as error:  # status checks now see the replacement, so name this one
            detail = f"operation {operation['id']} was not kept: {REPLACED} ({error})"
            raise Unkept(detail) from None
        return orphan

    def _reclaimed(self, operation: dict) -> None:
        """An orphan record left for an operation that now holds its slot says so."""
        key = self._orphan_key(operation)
        stored = self.store.get(key)
        if stored is None:
            return
        kept = {**json.loads(stored.data), "note": RECLAIMED, "updated_at": self._now()}
        try:
            self.store.replace(key, canonical(kept), stored.etag)
        except PreconditionFailed:
            pass  # only another reclaim rewrites an orphan record

    def _after_call(self, key: str, operation: dict, etag: str, state: str, note: str) -> dict:
        try:
            return self._move(key, operation, etag, state, note=note)[0]
        except StorageError as error:
            raise _unrecorded(error) from None

    def _not_attempted(self, key: str, operation: dict) -> None:
        """The operation's record failed to write, but may have landed: mark it FAILED, since
        the adapter was never called. Best effort: the caller reports the original error."""
        try:
            stored = self.store.get(key)
            if stored is not None and json.loads(stored.data)["id"] == operation["id"]:
                note = "not attempted: the operation could not be recorded"
                self._move(key, operation, stored.etag, FAILED, note=note)
        except (StorageError, ValueError, KeyError):
            pass

    def _move(
        self, key: str, operation: dict, etag: str, state: str, **changes: object
    ) -> tuple[dict, str | None]:
        if operation["state"] == state and all(operation[k] == v for k, v in changes.items()):
            return operation, etag  # nothing would change, and a write would race other writers
        moved = {**operation, **changes, "state": state, "updated_at": self._now()}
        try:
            return moved, self.store.replace(key, canonical(moved), etag)
        except PreconditionFailed:
            stored = self.store.get(key)  # another request moved it first; its state stands
            return json.loads(stored.data), None

    def _key(self, template_id: str) -> str:
        return f"templates/{template_id}/operation.json"

    def _orphan_key(self, operation: dict) -> str:
        return f"templates/{operation['template_id']}/orphans/{operation['id']}.json"

    def _now(self) -> str:
        return self.clock().isoformat()


def _unrecorded(error: StorageError) -> StorageError:
    return StorageError(
        f"the create was sent, but its outcome was not recorded; check its status ({error})"
    )
