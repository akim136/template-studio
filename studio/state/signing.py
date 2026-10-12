"""Creating the signing template: a simulated adapter, and operations that survive an unknown
outcome.

This repository holds no signing-provider credentials and no adapter that reaches a provider. The
only adapter is simulated: it records a template in the local store and sends nothing. There is no
send method at all.

An operation is recorded before the adapter is called, and moves through these states:
- CREATING: the create call is being made.
- VERIFYING: the adapter reported a create; it is being read back.
- CREATED: read back, with the payload that was sent.
- FAILED: the adapter refused, or a status check found nothing after the grace period (from the
  call's start or, for an unknown outcome, from when it returned). Nothing exists, so a new create
  is allowed.
- NEEDS_STATUS_CHECK: the outcome is unknown (an error, a timeout, a crash mid-call, a create that
  cannot be read back). The operation keeps its ID, and only a status check is offered.

A template has one operation slot. A create is allowed when the slot is empty, FAILED, or CREATED
for an earlier revision: each approved revision is created once (Alex, October 12, 2026), and the
operations it replaces are listed in the new one's `replaced`, carried forward through later
creates. A create is never repeated blindly: an open operation refuses it, and so does a CREATED one
for the same revision.

A create the adapter reports, or a status check finds at the provider, is written onto the stored
operation even if a concurrent status check moved it meanwhile (to FAILED, say), so it is never
lost or left FAILED. An unknown outcome is written the same way: a check that declared the
operation FAILED looked before the create may have landed, so the operation is reopened for a
status check. If the operation was replaced by another meanwhile, a create reported, found or left
unknown takes the slot back when the replacement is FAILED and nothing was created since it was
replaced (the replacement lists the same `replaced`); an orphan record an earlier report left for
it then says so and no longer counts. Otherwise the provider may hold both, and it is kept in the
orphan list (templates/{id}/orphans/index.json) with a note to resolve it by hand; a replacement
declared FAILED whose create lands later finds the slot taken and is kept the same way. An
unresolved orphan is shown with the operation and refuses every create until someone checks the
provider and resolves it with a note (Alex, October 12, 2026); a create checks again after it is
recorded, and is not attempted if an orphan was kept meanwhile (one kept after that check, while
the call is made, cannot stop it: no stored check is atomic with an external call). A status check
that would change nothing writes nothing, so repeated checks cannot crowd out a create being
recorded. If recording the operation fails before the adapter is called, the operation is marked
FAILED (not attempted) where it landed; if recording the outcome fails after the call, the error
says to check its status, unless an orphan could not be kept, which a status check cannot find:
that error names the operation instead.

Retries belong to the store: the R2 store (on `feat/hosted-login`) retries a request without a
clear answer, and the local store does not retry. A write that still fails leaves the operation as
it was recorded before the call, which refuses a new create and offers a status check: that record,
with the orphan list, is the dead-letter queue. The one exception is an orphan that cannot be
written: its replacement holds the slot, so it is named only in the error, and someone must check
the provider by hand.
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
from studio.state.errors import Conflict, Invalid, NotFound
from studio.state.policy import parse_policy
from studio.state.store import ObjectStore, PreconditionFailed, StorageError, canonical, digest
from studio.state.templates import ACTOR, Templates

CREATING, VERIFYING, CREATED = "CREATING", "VERIFYING", "CREATED"
FAILED, NEEDS_STATUS_CHECK = "FAILED", "NEEDS_STATUS_CHECK"
# Not finding a create counts as "nothing was created" only this long after the call started, or
# returned with an unknown outcome. It must exceed any real adapter's create timeout, or a slow
# create could land after it was declared failed.
GRACE = timedelta(minutes=2)
RECORD_ATTEMPTS = 10  # concurrent writes a create being recorded can lose to before it gives up
REPLACED = (
    "this operation's create was reported, found or left unknown after the operation was "
    "replaced by another; the provider may hold both, so resolve by hand"
)
RECLAIMED = "this operation took its slot back after its replacement failed; see operation.json"
UNRESOLVED, RESOLVED, TOOK_SLOT_BACK = "unresolved", "resolved", "took_slot_back"
ORPHANED = (
    "a create landed, or may have landed, after its operation was replaced, so the provider may "
    "hold an extra template: check the provider, then resolve each orphan before creating again"
)
MAX_NOTE = 500


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

    def orphans(self, template_id: str) -> list[dict]:
        """Creates kept after their operation was replaced, not yet resolved; oldest first."""
        self.templates.head(template_id)
        stored = self.store.get(self._key(template_id))
        return self._unresolved(template_id, None if stored is None else json.loads(stored.data))

    def resolve(self, template_id: str, operation_id: str, note: str, by: str = ACTOR) -> dict:
        """Records that someone checked the provider for an orphan, and what they found."""
        self.templates.head(template_id)
        note = note.strip()
        if not note or len(note) > MAX_NOTE:
            raise Invalid([f"say what the provider holds, in 1 to {MAX_NOTE} characters"])

        def settle(kept: dict) -> tuple[dict, bool]:
            orphan = kept.get(operation_id)
            if orphan is None:
                raise NotFound(operation_id)
            if orphan.get("status", UNRESOLVED) != UNRESOLVED:
                return orphan, False
            now = self._now()
            resolution = {"note": note, "by": by, "at": now}
            kept[operation_id] = {**orphan, "status": RESOLVED, "resolution": resolution}
            return kept[operation_id], True

        return self._change_orphans(template_id, settle)

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
        current = self.store.get(key)  # read before the orphans, so one kept after is caught below
        holder = None if current is None else json.loads(current.data)
        unresolved = self._unresolved(template_id, holder)
        if unresolved:
            raise Conflict(ORPHANED, {"orphans": unresolved})
        now = self._now()
        replaced: list[dict] = []
        if holder is not None:
            existing = holder
            if existing["state"] == CREATED and existing["revision"] == revision:
                raise Conflict("this revision was already created", existing)
            if existing["state"] not in (CREATED, FAILED):
                reason = "an operation is open: check its status, do not create again"
                raise Conflict(reason, existing)
            replaced = list(existing.get("replaced", []))
            if existing["state"] == CREATED:
                fields = ("id", "revision", "binding", "external_id")
                replaced.append({**{k: existing[k] for k in fields}, "replaced_at": now})
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
            "replaced": replaced,
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
            unresolved = self._unresolved(template_id, operation)  # kept while this was recorded
        except StorageError:
            self._not_attempted(key, operation)
            raise
        if unresolved:
            note = "not attempted: an orphan was kept while this create was recorded"
            try:
                self._move(key, operation, etag, FAILED, note=note)
            except StorageError:
                pass  # left CREATING with no call made, a status check finds nothing and fails it
            raise Conflict(ORPHANED, {"orphans": unresolved})
        try:
            external_id = self.adapter.create(operation["id"], payload)
        except Refused as error:
            return self._after_call(key, operation, etag, FAILED, note=f"refused: {error}")
        except Exception as error:  # a timeout or any error: the create may have happened
            outcome = type(error).__name__
            try:
                return self._unknown(key, operation, etag, outcome)
            except Unkept:
                raise
            except StorageError as failure:
                raise _unrecorded(failure) from None
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
        since = operation.get("returned_at") or operation["started_at"]
        if self.clock() - datetime.fromisoformat(since) < GRACE:
            # Too early to conclude, and writing now could clobber a create still in flight.
            return {**operation, "note": "not found yet; the create may still be running"}
        note = "nothing was created; a new create is allowed"
        return self._move(key, operation, etag, FAILED, note=note)[0]

    def _record(
        self, key: str, operation: dict, etag: str, external_id: str, state: str
    ) -> tuple[dict, str | None]:
        """Writes a create the adapter reported (VERIFYING) or the provider holds (CREATED) onto
        the stored operation, whatever a concurrent status check did to it meanwhile. A CREATED
        operation stands; one replaced by an operation that FAILED takes the slot back if nothing
        was created since, and one replaced otherwise is kept as an orphan. The etag is None when
        nothing was written here."""
        current = operation
        for _ in range(RECORD_ATTEMPTS):
            replaced = current["id"] != operation["id"]
            if replaced and operation["id"] in _ids(current):
                return current, None  # created, then replaced by a later revision's create
            if replaced and (current["state"] != FAILED or _ids(current) != _ids(operation)):
                # The replacement holds a create, or one was made after this was replaced (the
                # slot's list moved on), so the provider may hold both.
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
                try:
                    self._reclaimed(operation)
                except StorageError:
                    pass  # best effort: an orphan whose operation holds the slot is not counted
            return recorded, etag
        raise StorageError("the operation kept changing while its create was recorded")

    def _unknown(self, key: str, operation: dict, etag: str, error: str) -> dict:
        """Records that the create's outcome is unknown, like `_record` records a create,
        whatever a concurrent status check did meanwhile. One that declared it FAILED (the call
        outlasted the grace period) looked before the create may have landed, so the operation
        is reopened, and the grace period runs again from now (`returned_at`)."""
        now = self._now()
        changes = {"note": f"create outcome unknown ({error})", "returned_at": now}
        current = operation
        for _ in range(RECORD_ATTEMPTS):
            replaced = current["id"] != operation["id"]
            if replaced and operation["id"] in _ids(current):
                return current  # found, created, then replaced by a later revision's create
            if replaced and (current["state"] != FAILED or _ids(current) != _ids(operation)):
                return self._orphan(operation, None, note=f"{changes['note']}; {REPLACED}")
            if not replaced and current["state"] not in (CREATING, FAILED):
                return current  # found, or already waiting on a status check: its state stands
            mine = operation if replaced else current
            reopened = {**mine, **changes, "state": NEEDS_STATUS_CHECK, "updated_at": now}
            try:
                self.store.replace(key, canonical(reopened), etag)
            except PreconditionFailed:
                stored = self.store.get(key)
                current, etag = json.loads(stored.data), stored.etag
                continue
            if replaced:  # a status check may have kept it as an orphan meanwhile
                try:
                    self._reclaimed(operation)
                except StorageError:
                    pass  # best effort: an orphan whose operation holds the slot is not counted
            return reopened
        raise StorageError("the operation kept changing while its outcome was recorded")

    def _orphan(self, operation: dict, external_id: str | None, note: str = REPLACED) -> dict:
        reported = external_id is not None
        changes = {"external_id": external_id, "reported_created": reported, "note": note}
        orphan = {**operation, **changes, "state": NEEDS_STATUS_CHECK, "updated_at": self._now()}
        orphan["status"] = UNRESOLVED

        def keep(kept: dict) -> tuple[dict, bool]:
            if operation["id"] in kept:  # kept already, by the report or by a status check
                return kept[operation["id"]], False
            kept[operation["id"]] = orphan
            return orphan, True

        try:
            return self._change_orphans(operation["template_id"], keep)
        except StorageError as error:  # status checks now see the replacement, so name this one
            detail = f"operation {operation['id']} was not kept: {REPLACED} ({error})"
            raise Unkept(detail) from None

    def _reclaimed(self, operation: dict) -> None:
        """An orphan record left for an operation that now holds its slot says so."""

        def mark(kept: dict) -> tuple[None, bool]:
            orphan = kept.get(operation["id"])
            if orphan is None or orphan.get("status", UNRESOLVED) != UNRESOLVED:
                return None, False
            changes = {"note": RECLAIMED, "status": TOOK_SLOT_BACK, "updated_at": self._now()}
            kept[operation["id"]] = {**orphan, **changes}
            return None, True

        self._change_orphans(operation["template_id"], mark)

    def _unresolved(self, template_id: str, holder: dict | None) -> list[dict]:
        """Unresolved orphans, except any that took the slot back: the operation in the slot, or
        one it lists as replaced (marking its record is best effort)."""
        stored = self.store.get(self._orphans_key(template_id))
        kept = [] if stored is None else json.loads(stored.data)["orphans"]
        settled = set() if holder is None else {holder["id"], *_ids(holder)}
        return [
            o for o in kept if o.get("status", UNRESOLVED) == UNRESOLVED and o["id"] not in settled
        ]

    def _change_orphans(self, template_id: str, change: Callable[[dict], tuple]) -> object:
        """Applies `change` to the orphan list (by operation ID), writing it conditionally."""
        key = self._orphans_key(template_id)
        for _ in range(RECORD_ATTEMPTS):
            stored = self.store.get(key)
            orphans = [] if stored is None else json.loads(stored.data)["orphans"]
            kept = {o["id"]: o for o in orphans}
            result, changed = change(kept)
            if not changed:
                return result
            data = canonical({"orphans": list(kept.values())})
            try:
                if stored is None:
                    self.store.put_new(key, data)
                else:
                    self.store.replace(key, data, stored.etag)
            except PreconditionFailed:
                continue
            return result
        raise StorageError("the orphan list kept changing")

    def _after_call(self, key: str, operation: dict, etag: str, state: str, note: str) -> dict:
        try:  # a refusal: nothing exists, so a concurrent request's state can stand
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

    def _orphans_key(self, template_id: str) -> str:
        return f"templates/{template_id}/orphans/index.json"

    def _now(self) -> str:
        return self.clock().isoformat()


def _ids(operation: dict) -> list[str]:
    return [r["id"] for r in operation.get("replaced", [])]


def _unrecorded(error: StorageError) -> StorageError:
    return StorageError(
        f"the create was sent, but its outcome was not recorded; check its status ({error})"
    )
