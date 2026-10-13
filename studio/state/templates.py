"""Templates: saved revisions, plus review and approval bound to one saved revision.

A template's state is a head pointer plus immutable revision objects:
- `templates/{id}/revisions/{hash}.json` holds one saved revision: its source, policy and fields,
  its number and its parent. It is named by the hash of its content and written only if absent.
- `templates/{id}/head.json` names the current revision and its binding, and records the review
  and approval given to it. It changes only by a conditional replace of the version just read.

A save names the revision it started from. If another save has moved the head, the save is
refused with the current head (409), so a stale editor never overwrites newer work; a review or
approval given to the base revision meanwhile does not refuse it, and the save clears them. A save
is acknowledged only after the head moved, and a storage failure is not reported as saved. A
revision object left by a lost race or a failed head write is never referenced.

Review and approval record the revision and its binding: the hash of the source, policy, fields
and details (the title and sponsor account the operator typed; never inferred from a file name).
Every new revision starts with neither. Every save makes a new revision, even one whose content
matches the last (Alex, October 12, 2026), so any save after a review or approval clears
them, including undo, redo and policy changes, which all reach the server as saves. The one
exception is a retried request that already landed: a save may carry a `save_id` chosen by its
client, and if the head is one past the base and was made by that save_id with the same binding,
that revision is returned and no other is made. Another editor's save with the same content has
another save_id, so it is refused as stale. A review or approval sent for any
revision or binding other than the head's is refused as stale.

A source whose page inventory is not COMPLETE can be worked on, but it blocks review, approval
and create: every snapshot shows the source's inventory and flags, so the user sees what is
missing (Alex's rule: anything incomplete is flagged to the user). A template keeps its source,
so such a template stays blocked; a complete source needs a new template.

Pages that AI drafting left without a checked answer stay on the record of every later revision
until a newer draft is applied (Alex, October 13, 2026): a save made by hand keeps them. A review
must confirm each of them as checked by hand, and approval is refused, naming the pages, while any
is not confirmed. A revision saved before this record existed has none.

`summaries` lists every template for the library from its head, its current revision and its
signing operation, without reading any source.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from studio.state import sources
from studio.state.errors import Conflict, Invalid, NotFound
from studio.state.policy import Policy, PolicyError, field_errors, issues, parse_policy
from studio.state.store import (
    ObjectStore,
    PreconditionFailed,
    StorageError,
    canonical,
    digest,
    log_failure,
)

TEMPLATE_ID = re.compile(r"[0-9a-f]{32}")
SAVE_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
ACTOR = "anonymous"  # no app identity: local loopback use (guide §6)
MOVED = "the template was saved since this revision; reload it"
INCOMPLETE = "the source's page inventory is INCOMPLETE; see its flags"
SAVE_ATTEMPTS = 3  # a head moved only by sign-offs (at most review and approval) is retried
DETAILS = {
    "title": "",
    "sponsor_name": "",
    "sponsor_account_id": "",
}  # a revision saved before them
DETAIL_LIMITS = {"title": 200, "sponsor_name": 200, "sponsor_account_id": 64}
ACCOUNT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")
LIST_READERS = 8  # templates read at once for the library


def binding(source_sha256: str, policy: dict, fields: list[dict], details: dict) -> str:
    parts = {
        "source": source_sha256,
        "policy": digest(policy),
        "fields": digest(fields),
        "details": digest(details),
    }
    return digest(parts)


def parse_details(raw: object) -> dict:
    """A title and sponsor account as the operator typed them: trimmed, bounded, printable."""
    if not isinstance(raw, dict) or set(raw) != set(DETAILS):
        raise Invalid([f"details has exactly the keys {sorted(DETAILS)}"])
    errors, details = [], {}
    for key, limit in DETAIL_LIMITS.items():
        value = raw[key]
        if not isinstance(value, str):
            errors.append(f"details.{key} must be text")
            continue
        value = " ".join(value.split())
        if len(value) > limit or not value.isprintable():
            errors.append(f"details.{key} is printable text of at most {limit} characters")
        details[key] = value
    if "sponsor_name" in details and not details["sponsor_name"]:
        errors.append("details.sponsor_name names the sponsor account")
    account = details.get("sponsor_account_id")
    if account and not ACCOUNT_ID.fullmatch(account):
        errors.append("details.sponsor_account_id is letters, digits, '_', '.', ':' or '-'")
    if errors:
        raise Invalid(errors)
    return details


def _unchecked(left: dict, pages: list[int], sentence: str) -> str:
    """`sentence` with {pages} as 'page 2' or 'pages 2 and 5', then why they need checking."""
    if pages[1:]:
        named, them = "pages " + ", ".join(map(str, pages[:-1])) + f" and {pages[-1]}", "them"
    else:
        named, them = f"page {pages[0]}", "it"
    why = f"AI drafting (revision {left['revision']}) left {them} without a checked answer"
    return f"{sentence.format(pages=named)}: {why}"


class Templates:
    def __init__(self, store: ObjectStore, clock: Callable[[], datetime]) -> None:
        self.store = store
        self.clock = clock

    def get(self, template_id: str) -> dict:
        return self.snapshot(self.head(template_id)[0])

    def create(self, source_sha256: object, policy: object, details: object = None) -> dict:
        record = self._source(source_sha256)
        parsed = self._policy(policy)
        typed = DETAILS if details is None else parse_details(details)
        head = self._commit(uuid.uuid4().hex, None, record["sha256"], parsed, [], typed)
        return self.snapshot(head)

    def summaries(self) -> list[dict]:
        """Every template, newest save first. One that cannot be read is listed as unreadable."""
        ids = [name for name in self.store.children("templates/") if TEMPLATE_ID.fullmatch(name)]
        with ThreadPoolExecutor(max_workers=LIST_READERS) as pool:
            rows = list(pool.map(self._summary, ids))
        return sorted(rows, key=lambda row: row["saved_at"] or "", reverse=True)

    def _summary(self, template_id: str) -> dict:
        row = {"id": template_id, "saved_at": None, "error": None}
        try:
            head, _ = self.head(template_id)
            revision = self._revision(head)
            operation = self.store.get(f"templates/{template_id}/operation.json")
            return {
                **row,
                "revision": head["revision"],
                "binding": head["binding"],
                "details": revision.get("details", DETAILS),
                "countersigner": revision["policy"]["countersigner"],
                "field_count": len(revision["fields"]),
                "saved_at": revision["saved_at"],
                "review": head["review"],
                "approval": head["approval"],
                "operation": None if operation is None else json.loads(operation.data),
            }
        except StorageError as error:
            log_failure(error)  # its keys and provider answer stay in the server log
            return {**row, "error": "storage failed"}
        except NotFound as error:
            return {**row, "error": str(error) or "missing"}
        except (ValueError, KeyError, TypeError) as error:  # a malformed stored record
            return {**row, "error": f"a stored record is malformed ({type(error).__name__})"}

    def save(
        self,
        template_id: str,
        base_revision: int,
        policy: object,
        fields: object,
        save_id: str | None = None,
        details: object = None,
        unchecked: list[int] | None = None,
    ) -> tuple[dict, bool]:
        """Returns the saved snapshot and whether this call made a new revision. Without
        details, the base revision's details are kept. `unchecked` is given only by an applied
        draft: the pages it left without a checked answer. Without it, the base revision's
        record of them is kept."""
        if save_id is not None and not (isinstance(save_id, str) and SAVE_ID.fullmatch(save_id)):
            raise Invalid(["save_id is 8 to 64 letters, digits, '-' or '_'"])
        head, etag = self.head(template_id)
        parsed = self._policy(policy)
        typed = None if details is None else parse_details(details)
        record = self._source(head["source_sha256"])
        errors = field_errors(fields, parsed, sources.page_index(record))
        if errors:
            raise Invalid(errors)
        for _ in range(SAVE_ATTEMPTS):
            base = self._revision(head)
            kept = typed if typed is not None else base.get("details", DETAILS)
            left = base.get("unchecked") if unchecked is None else None
            if unchecked:
                left = {"revision": head["revision"] + 1, "pages": sorted(set(unchecked))}
            bound = binding(head["source_sha256"], parsed.to_dict(), fields, kept)
            if head["revision"] != base_revision:
                landed = head["revision"] == base_revision + 1 and head["binding"] == bound
                if landed and save_id is not None and head.get("save_id") == save_id:
                    return self.snapshot(head), False
                raise Conflict(MOVED, self.snapshot(head))
            try:
                current = (head, etag)
                args = (template_id, current, None, parsed, fields, kept, save_id, left)
                committed = self._commit(*args)
            except PreconditionFailed:
                head, etag = self.head(template_id)
                continue
            return self.snapshot(committed), True
        raise Conflict(MOVED, self.snapshot(head))

    def confirm_review(
        self,
        template_id: str,
        revision: int,
        bound: str,
        by: str = ACTOR,
        checked_pages: Sequence[int] = (),
    ) -> dict:
        """`checked_pages`: the pages drafting left unchecked that the reviewer checked by hand.
        They must be exactly those pages."""
        return self._sign_off(template_id, revision, bound, "review", by, list(checked_pages))

    def approve(self, template_id: str, revision: int, bound: str, by: str = ACTOR) -> dict:
        return self._sign_off(template_id, revision, bound, "approval", by)

    def require_approved(self, template_id: str, revision: int, bound: str) -> dict:
        head, _ = self.head(template_id)
        snapshot = self._current(head, revision, bound)
        if snapshot["issues"]:
            raise Invalid(snapshot["issues"])
        if head["approval"] is None:
            raise Invalid(["this revision is not approved"])
        return snapshot

    def head(self, template_id: str) -> tuple[dict, str]:
        if not TEMPLATE_ID.fullmatch(template_id):
            raise NotFound(template_id)
        stored = self.store.get(f"templates/{template_id}/head.json")
        if stored is None:
            raise NotFound(template_id)
        return json.loads(stored.data), stored.etag

    def snapshot(self, head: dict) -> dict:
        revision = self._revision(head)
        record = sources.load(self.store, head["source_sha256"])
        if record is None:
            raise StorageError(f"the sealed source of revision {head['revision']} is missing")
        found = issues(revision["fields"], parse_policy(revision["policy"]))
        if record["inventory"] != "COMPLETE":
            found.insert(0, INCOMPLETE)
        return {
            "id": head["template_id"],
            "revision": head["revision"],
            "binding": head["binding"],
            "source_sha256": head["source_sha256"],
            "source": {"inventory": record["inventory"], "flags": record["flags"]},
            "details": revision.get("details", DETAILS),
            "policy": revision["policy"],
            "fields": revision["fields"],
            "issues": found,
            "saved_at": revision["saved_at"],
            "review": head["review"],
            "approval": head["approval"],
            "unchecked": revision.get("unchecked"),
        }

    def _sign_off(
        self,
        template_id: str,
        revision: int,
        bound: str,
        kind: str,
        by: str,
        checked: list[int] | None = None,
    ) -> dict:
        head, etag = self.head(template_id)
        snapshot = self._current(head, revision, bound)
        if snapshot["issues"]:
            raise Invalid(snapshot["issues"])
        if kind == "approval" and head["review"] is None:
            raise Invalid(["the review of this revision is not confirmed"])
        left = snapshot["unchecked"] or {"revision": None, "pages": []}
        if kind == "approval":
            missing = sorted(set(left["pages"]) - set(head["review"].get("checked_pages", [])))
            if missing:
                sentence = "the review did not confirm {pages} as checked by hand"
                raise Invalid([_unchecked(left, missing, sentence)])
        if head[kind] is not None:
            return snapshot  # already given to this revision
        if kind == "review" and sorted(set(checked or [])) != left["pages"]:
            if not left["pages"]:
                raise Invalid(["no page of this revision is waiting to be checked by hand"])
            sentence = "confirm that you checked {pages} by hand"
            raise Invalid([_unchecked(left, left["pages"], sentence)])
        given = {"revision": revision, "binding": bound, "at": self._now(), "by": by}
        if kind == "review" and left["pages"]:
            given["checked_pages"] = left["pages"]
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
        details: dict,
        save_id: str | None = None,
        unchecked: dict | None = None,
    ) -> dict:
        previous = current[0] if current else None
        source = previous["source_sha256"] if previous else source_sha256
        revision = {
            "template_id": template_id,
            "revision": previous["revision"] + 1 if previous else 1,
            "parent": previous["object"] if previous else None,
            "source_sha256": source,
            "details": details,
            "policy": policy.to_dict(),
            "fields": fields,
            "saved_at": self._now(),
        }
        if unchecked:
            revision["unchecked"] = unchecked
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
            "binding": binding(source, revision["policy"], fields, details),
            "source_sha256": source,
            "save_id": save_id,
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
            raise  # the caller reads the head again
        return head

    def _revision(self, head: dict) -> dict:
        """The revision `head` points to, checked against its hash."""
        key = f"templates/{head['template_id']}/revisions/{head['object']}.json"
        stored = self.store.get(key)
        if stored is None or hashlib.sha256(stored.data).hexdigest() != head["object"]:
            raise StorageError(f"revision {head['revision']} is missing or altered")
        return json.loads(stored.data)

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
