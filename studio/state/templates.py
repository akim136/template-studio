"""Templates: saved revisions, plus review and approval bound to one saved revision.

A template's state is a head pointer plus immutable revision objects:
- `templates/{id}/revisions/{hash}.json` holds one saved revision: its source, policy and fields,
  its number and its parent. It is named by the hash of its content and written only if absent.
- `templates/{id}/head.json` names the current revision and its binding, and records the review
  and approval given to it. It changes only by a conditional replace of the version just read.

A save names the revision it started from. If the head has moved, the save is refused with the
current head (409), so a stale editor never overwrites newer work. A save is acknowledged only
after the head moved, and a storage failure is reported as not saved. A revision object left by a
lost race or a failed head write is never referenced.

Review and approval record the revision and its binding: the hash of the source, policy and
fields. Every new revision starts with neither. Any consequential edit therefore clears them,
including undo, redo and policy changes, which all reach the server as saves. A save that changes
nothing makes no new revision. A review or approval sent for any revision or binding other than
the head's is refused as stale.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime

from studio.state import sources
from studio.state.errors import Conflict, Invalid, NotFound
from studio.state.policy import Policy, PolicyError, field_errors, issues, parse_policy
from studio.state.store import ObjectStore, PreconditionFailed, StorageError, canonical, digest

TEMPLATE_ID = re.compile(r"[0-9a-f]{32}")
ACTOR = "anonymous"  # Vercel admission is not app identity (guide §6)
MOVED = "the template was saved since this revision; reload it"


def binding(source_sha256: str, policy: dict, fields: list[dict]) -> str:
    parts = {"source": source_sha256, "policy": digest(policy), "fields": digest(fields)}
    return digest(parts)


class Templates:
    def __init__(self, store: ObjectStore, clock: Callable[[], datetime]) -> None:
        self.store = store
        self.clock = clock

    def get(self, template_id: str) -> dict:
        return self.snapshot(self.head(template_id)[0])

    def create(self, source_sha256: object, policy: object) -> dict:
        record = self._source(source_sha256)
        parsed = self._policy(policy)
        head = self._commit(uuid.uuid4().hex, None, record["sha256"], parsed, [])
        return self.snapshot(head)

    def save(
        self, template_id: str, base_revision: int, policy: object, fields: object
    ) -> tuple[dict, bool]:
        """Returns the saved snapshot and whether this call made a new revision."""
        head, etag = self.head(template_id)
        parsed = self._policy(policy)
        record = self._source(head["source_sha256"])
        errors = field_errors(fields, parsed, sources.page_index(record))
        if errors:
            raise Invalid(errors)
        bound = binding(head["source_sha256"], parsed.to_dict(), fields)
        if head["revision"] != base_revision:
            if head["revision"] == base_revision + 1 and head["binding"] == bound:
                return self.snapshot(head), False  # a retry of a save that already landed
            raise Conflict(MOVED, self.snapshot(head))
        if head["binding"] == bound:
            return self.snapshot(head), False  # nothing changed: no new revision
        return self.snapshot(self._commit(template_id, (head, etag), None, parsed, fields)), True

    def confirm_review(self, template_id: str, revision: int, bound: str) -> dict:
        return self._sign_off(template_id, revision, bound, "review")

    def approve(self, template_id: str, revision: int, bound: str) -> dict:
        return self._sign_off(template_id, revision, bound, "approval")

    def require_approved(self, template_id: str, revision: int, bound: str) -> dict:
        head, _ = self.head(template_id)
        snapshot = self._current(head, revision, bound)
        if head["approval"] is None:
            raise Invalid(["this revision is not approved"])
        if snapshot["issues"]:
            raise Invalid(snapshot["issues"])
        return snapshot

    def head(self, template_id: str) -> tuple[dict, str]:
        if not TEMPLATE_ID.fullmatch(template_id):
            raise NotFound(template_id)
        stored = self.store.get(f"templates/{template_id}/head.json")
        if stored is None:
            raise NotFound(template_id)
        return json.loads(stored.data), stored.etag

    def snapshot(self, head: dict) -> dict:
        key = f"templates/{head['template_id']}/revisions/{head['object']}.json"
        stored = self.store.get(key)
        if stored is None or hashlib.sha256(stored.data).hexdigest() != head["object"]:
            raise StorageError(f"revision {head['revision']} is missing or altered")
        revision = json.loads(stored.data)
        return {
            "id": head["template_id"],
            "revision": head["revision"],
            "binding": head["binding"],
            "source_sha256": head["source_sha256"],
            "policy": revision["policy"],
            "fields": revision["fields"],
            "issues": issues(revision["fields"], parse_policy(revision["policy"])),
            "saved_at": revision["saved_at"],
            "review": head["review"],
            "approval": head["approval"],
        }

    def _sign_off(self, template_id: str, revision: int, bound: str, kind: str) -> dict:
        head, etag = self.head(template_id)
        snapshot = self._current(head, revision, bound)
        if snapshot["issues"]:
            raise Invalid(snapshot["issues"])
        if kind == "approval" and head["review"] is None:
            raise Invalid(["the review of this revision is not confirmed"])
        if head[kind] is not None:
            return snapshot  # already given to this revision
        given = {"revision": revision, "binding": bound, "at": self._now(), "by": ACTOR}
        updated = {**head, kind: given}
        try:
            self.store.replace(f"templates/{template_id}/head.json", canonical(updated), etag)
        except PreconditionFailed:
            latest, _ = self.head(template_id)
            current = self._current(latest, revision, bound)  # stale if a save moved the head
            if latest[kind] is None:
                raise Conflict("the template changed; reload it", current) from None
            return current  # a concurrent request gave the same sign-off
        return {**snapshot, kind: given}

    def _current(self, head: dict, revision: int, bound: str) -> dict:
        snapshot = self.snapshot(head)
        if revision != head["revision"] or bound != head["binding"]:
            raise Conflict("stale: this is not the current saved revision; reload it", snapshot)
        return snapshot

    def _commit(
        self,
        template_id: str,
        current: tuple[dict, str] | None,
        source_sha256: str | None,
        policy: Policy,
        fields: list[dict],
    ) -> dict:
        previous = current[0] if current else None
        source = previous["source_sha256"] if previous else source_sha256
        revision = {
            "template_id": template_id,
            "revision": previous["revision"] + 1 if previous else 1,
            "parent": previous["object"] if previous else None,
            "source_sha256": source,
            "policy": policy.to_dict(),
            "fields": fields,
            "saved_at": self._now(),
        }
        name = digest(revision)
        try:
            key = f"templates/{template_id}/revisions/{name}.json"
            self.store.put_new(key, canonical(revision))
        except PreconditionFailed:
            pass  # the same content is already stored under its hash
        head = {
            "template_id": template_id,
            "revision": revision["revision"],
            "object": name,
            "binding": binding(source, revision["policy"], fields),
            "source_sha256": source,
            "review": None,
            "approval": None,
        }
        key = f"templates/{template_id}/head.json"
        try:
            if current is None:
                self.store.put_new(key, canonical(head))
            else:
                self.store.replace(key, canonical(head), current[1])
        except PreconditionFailed:
            if current is None:
                raise StorageError("a new template id collided") from None
            raise Conflict(MOVED, self.snapshot(self.head(template_id)[0])) from None
        return head

    def _source(self, sha256: object) -> dict:
        try:
            record = sources.load(self.store, sha256) if isinstance(sha256, str) else None
        except sources.SourceError as error:
            raise Invalid([str(error)]) from None
        if record is None:
            raise Invalid(["the source is not sealed; upload it first"])
        return record

    def _policy(self, raw: object) -> Policy:
        try:
            return parse_policy(raw)
        except PolicyError as error:
            raise Invalid([f"policy: {error}"]) from None

    def _now(self) -> str:
        return self.clock().isoformat()
