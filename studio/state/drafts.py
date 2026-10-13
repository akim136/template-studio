"""In-app field drafting, one model call per request, driven page by page from the browser.

A draft belongs to the template revision it started from. Starting it runs the shared analysis
service once and stores each page's record, so later requests read one page instead of analyzing
the document again. Each `step` then makes at most one model call: the route preflight, or stage A
or stage B of one page, with the model route's prompts, validator and route checks
(`studio.services.inference`). That keeps every request well inside a function's time limit, and
lets a browser that was closed pick the draft up where it stopped. Two starts that raced, each
seeing no unfinished draft, take the same write-once start slot, and the one that lost joins the
draft of the one that won.

Before a call is sent it is claimed by writing an object that can be written only once, so two
browsers, or a retried request, never pay for the same call twice. A claim with no result is an
unknown outcome: its reservation stays at the worst case in the ledger, and that page is not
drafted again in this draft. Stage A and stage B results are separate, immutable objects, and a
page whose stage B failed has no final fields; stage A is never substituted for it.

When every page is finished, or the draft has stopped (route, budget or preflight), the operator
applies the stage B fields as one save on the revision the draft started from.

Drafting needs a model credential, which only the hosted studio has: the OIDC token Vercel sends
with each request. It is used for the calls of that request and never logged or stored. The
hosted store keeps the one spend ledger that holds the cap (`studio.state.ledger`).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from studio.services import analysis, gateway, inference
from studio.services.analysis import PageRecord
from studio.services.candidates import page_candidates
from studio.services.fields import Field, field_dict
from studio.services.render import PageImage
from studio.state import sources
from studio.state.errors import Conflict, Invalid, NotFound
from studio.state.ledger import CAP_USD, StoreLedger
from studio.state.policy import parse_policy
from studio.state.store import ObjectStore, PreconditionFailed, StorageError, canonical
from studio.state.templates import Templates

ROUTE = gateway.Route("openai/gpt-5.4-mini", "byok", accept_fallback=True)  # Alex, Oct 12, 2026
# A claim older than this, with no result, is lost: its request outlived any call it could make.
LEASE = timedelta(seconds=gateway.TIMEOUT_S + 120)
DRAFT_ID = re.compile(r"[0-9]{8}t[0-9]{6}-[0-9a-f]{8}")
WRITERS = 8  # parallel writes of page records at start
# A start slot whose draft was not written by then is abandoned, and the next slot is tried.
START_WINDOW = timedelta(minutes=2)
START_SLOTS = 5

RUNNING, DRAFTED, STOPPED, APPLIED = "RUNNING", "DRAFTED", "STOPPED", "APPLIED"


class Unavailable(Exception):
    """Drafting cannot run here: no model credential, or not the hosted store."""


def _mode(policy: dict) -> str:
    return "countersign" if policy["countersigner"] else "lp"


def _page_record(raw: dict) -> PageRecord:
    image = raw.pop("image")
    return PageRecord(**raw, image=None if image is None else PageImage(**image))


def _stored_page(record: PageRecord) -> dict:
    image = record.image
    return {
        "page": record.page,
        "status": record.status,
        "width_pt": record.width_pt,
        "height_pt": record.height_pt,
        "rotation": record.rotation,
        "text": record.text,
        "underscore_runs": record.underscore_runs,
        "reason": record.reason,
        "image": None if image is None else image.__dict__,
        "image_reason": record.image_reason,
    }


def _output(raw: dict) -> inference.StageOutput:
    fields = [Field(**field) for field in raw["fields"]]
    return inference.StageOutput(fields, raw["rejected"], raw["notes"], raw["changes"], raw["raw"])


def _recorded(status: str, evidence: inference.PageEvidence, output) -> dict:
    record = {"status": status, "evidence": evidence.__dict__}
    if output is None:
        return {**record, "fields": [], "rejected": [], "notes": "", "changes": None, "raw": ""}
    return {
        **record,
        "fields": [field_dict(field) for field in output.fields],
        "rejected": output.rejected,
        "notes": output.notes,
        "changes": output.changes,
        "raw": output.raw,
    }


class Drafts:
    def __init__(
        self,
        store: ObjectStore,
        templates: Templates,
        clock: Callable[[], datetime],
        transport=None,
    ):
        self.store, self.templates, self.clock = store, templates, clock
        self.transport = transport  # None: the gateway over HTTPS; tests pass a fake

    # ---- storage ----

    def _prefix(self, template_id: str, draft_id: str) -> str:
        if not DRAFT_ID.fullmatch(draft_id):
            raise NotFound(draft_id)
        return f"templates/{template_id}/drafts/{draft_id}/"

    def _read(self, key: str) -> dict | None:
        stored = self.store.get(key)
        return None if stored is None else json.loads(stored.data)

    def _write_once(self, key: str, value: dict) -> bool:
        try:
            self.store.put_new(key, canonical(value))
        except PreconditionFailed:
            return False
        return True

    def _header(self, template_id: str, draft_id: str) -> dict:
        self.templates.head(template_id)  # 404 for an unknown template
        header = self._read(self._prefix(template_id, draft_id) + "draft.json")
        if header is None:
            raise NotFound(draft_id)
        return header

    def latest(self, template_id: str) -> dict | None:
        """The newest draft of a template, as `view` shows it, or None."""
        self.templates.head(template_id)
        ids = [
            n
            for n in self.store.children(f"templates/{template_id}/drafts/")
            if DRAFT_ID.fullmatch(n)
        ]
        for draft_id in reversed(ids):  # newest first; one whose start never finished is skipped
            if self._read(self._prefix(template_id, draft_id) + "draft.json") is not None:
                return self.view(template_id, draft_id)
        return None

    # ---- start ----

    def start(self, template_id: str, base_revision: int, actor: str) -> dict:
        """Analyze the source and open a draft on `base_revision`. An unfinished draft of the
        same revision is returned instead of starting a second one."""
        snapshot = self.templates.get(template_id)
        if snapshot["revision"] != base_revision:
            raise Conflict("the template has a newer revision; reload it", snapshot)
        current = self.latest(template_id)
        if current and current["base_revision"] == base_revision and current["state"] == RUNNING:
            return current
        source = sources.source_bytes(self.store, snapshot["source_sha256"])
        try:
            result = analysis.analyze(source)
        except (analysis.AnalysisError, analysis.LimitExceeded) as error:
            raise Invalid([f"the source could not be analyzed: {error}"]) from None
        now = self.clock()
        draft_id = f"{now:%Y%m%dt%H%M%S}-{uuid.uuid4().hex[:8]}"
        winner = self._start_slot(template_id, base_revision, current, draft_id, snapshot)
        if winner is not None:
            return winner
        prefix = self._prefix(template_id, draft_id)
        with ThreadPoolExecutor(WRITERS) as pool:
            writes = [
                pool.submit(
                    self.store.put_new,
                    f"{prefix}pages/{p.page:04d}.json",
                    canonical(_stored_page(p)),
                )
                for p in result.pages
            ]
            for write in writes:
                write.result()
        policy = parse_policy(snapshot["policy"])
        header = {
            "id": draft_id,
            "template_id": template_id,
            "base_revision": base_revision,
            "base_fields": len(snapshot["fields"]),  # applying the draft replaces them
            "source_sha256": snapshot["source_sha256"],
            "policy": snapshot["policy"],
            "roles": policy.roles,
            "signer_mode": _mode(snapshot["policy"]),
            "route": {
                "model": ROUTE.model,
                "credential": ROUTE.credential,
                "accept_fallback": True,
            },
            "profile": inference.PROFILE,
            "pages": [p.page for p in result.pages],
            "inventory": result.inventory,
            "flags": [flag.__dict__ for flag in result.flags],
            "started_at": now.isoformat(),
            "started_by": actor,
        }
        self.store.put_new(prefix + "draft.json", canonical(header))  # the draft exists from here
        return self.view(template_id, draft_id)

    def _start_slot(self, template_id, base_revision, current, draft_id, snapshot) -> dict | None:
        """Take the start slot that follows the latest draft this request saw, or return the
        draft of the request that took it first."""
        after = current["id"] if current else "none"
        now = self.clock()
        for slot in range(START_SLOTS):
            key = f"templates/{template_id}/draft-starts/r{base_revision}-{after}-{slot}.json"
            if self._write_once(key, {"draft_id": draft_id, "at": now.isoformat()}):
                return None
            held = self._read(key)
            if held is None:
                raise StorageError("a draft start slot could not be read")
            winner = held["draft_id"]
            if self._read(self._prefix(template_id, winner) + "draft.json") is not None:
                return self.view(template_id, winner)
            if now - datetime.fromisoformat(held["at"]) < START_WINDOW:
                reason = "another request is starting a draft of this revision; try again shortly"
                raise Conflict(reason, snapshot)
        raise StorageError("too many draft starts of this revision were abandoned")

    # ---- progress ----

    def _claim_age(self, key: str) -> timedelta | None:
        claim = self._read(key)
        return None if claim is None else self.clock() - datetime.fromisoformat(claim["at"])

    def view(self, template_id: str, draft_id: str) -> dict:
        header = self._header(template_id, draft_id)
        prefix = self._prefix(template_id, draft_id)
        names = set(self.store.children(prefix + "steps/"))
        stop, applied = self._read(prefix + "stop.json"), self._read(prefix + "applied.json")
        violation = self._read(prefix + "violation.json")

        def state_of(step: str) -> str:
            if f"{step}.json" in names:
                return "done"
            if f"{step}.started.json" not in names:
                return "pending"
            age = self._claim_age(f"{prefix}steps/{step}.started.json")
            return "lost" if age is not None and age > LEASE else "running"

        preflight = self._read(prefix + "steps/preflight.json")
        pages = []
        for number in header["pages"]:
            a, b = state_of(f"p{number:04d}-a"), state_of(f"p{number:04d}-b")
            if b == "done":
                state = "done"
            elif "lost" in (a, b):
                state = "lost"
            elif b == "running":
                state = "checking"
            elif a == "running":
                state = "drafting"
            elif a == "done":
                state = "drafted"  # waiting for its check
            else:
                state = "pending"
            pages.append({"page": number, "state": state})
        finished = all(page["state"] in ("done", "lost") for page in pages)
        stop_reason = (violation or stop or {}).get("reason")
        if preflight is None and state_of("preflight") == "lost":
            stop_reason = stop_reason or "the route check's outcome is unknown; start a new draft"
        if preflight is not None and preflight["status"] != "PASSED":  # even if its stop was lost
            stop_reason = stop_reason or f"preflight: {preflight['status']}"
        if applied:
            state = APPLIED
        elif stop_reason:
            state = STOPPED
        elif preflight and preflight["status"] == "PASSED" and finished:
            state = DRAFTED
        else:
            state = RUNNING
        book = StoreLedger(self.store, self.clock, draft_id)
        total, draft_usd = book.totals()
        counts = {key: sum(1 for p in pages if p["state"] == key) for key in ("done", "lost")}
        return {
            "id": draft_id,
            "template_id": template_id,
            "base_revision": header["base_revision"],
            "base_fields": header.get("base_fields", 0),
            "model": header["route"]["model"],
            "started_at": header["started_at"],
            "inventory": header["inventory"],
            "flags": header["flags"],
            "state": state,
            "stop_reason": stop_reason,
            "preflight": preflight["status"] if preflight else state_of("preflight").upper(),
            "pages": pages,
            "done": counts["done"],
            "lost": counts["lost"],
            "spend": {
                "draft_usd": round(draft_usd, 6),
                "total_usd": round(total, 6),
                "cap_usd": float(CAP_USD),
            },
            "applied": applied,
        }

    # ---- one call ----

    def step(self, template_id: str, draft_id: str, token: str | None, transport=None) -> dict:
        """Make the next model call this draft needs, if any, and return the draft's view."""
        if not token:
            raise Unavailable("no model credential reached this request")
        header = self._header(template_id, draft_id)
        current = self.view(template_id, draft_id)
        if current["state"] != RUNNING:
            return current
        prefix = self._prefix(template_id, draft_id)
        names = set(self.store.children(prefix + "steps/"))
        source = sources.source_bytes(self.store, header["source_sha256"])
        config = inference.RunConfig(ROUTE, header["roles"], header["signer_mode"], workers=1)
        runner = inference.Runner(
            source,
            None,
            config,
            token=token,
            ledger=StoreLedger(self.store, self.clock, draft_id),
            transport=self.transport if transport is None else transport,
            run_id=draft_id,
        )
        if "preflight.json" not in names:
            if "preflight.started.json" not in names and self._claim(prefix, "preflight"):
                result = runner.preflight()
                self._write_once(prefix + "steps/preflight.json", result)
                if result["status"] != "PASSED":
                    runner.stop.set(f"preflight: {result['status']}")
                self._record_stop(prefix, runner)
            return self.view(template_id, draft_id)
        for number in header["pages"]:  # a step another request claimed first is passed over
            a, b = f"p{number:04d}-a", f"p{number:04d}-b"
            ready = f"{a}.json" in names and not {f"{b}.json", f"{b}.started.json"} & names
            if ready and self._stage_b(prefix, runner, source, number):
                break
            if f"{a}.started.json" not in names and self._stage_a(prefix, runner, source, number):
                break
        self._record_stop(prefix, runner)
        return self.view(template_id, draft_id)

    def _claim(self, prefix: str, step: str) -> bool:
        return self._write_once(
            f"{prefix}steps/{step}.started.json", {"at": self.clock().isoformat()}
        )

    def _prepared(self, prefix: str, source: bytes, number: int):
        raw = self._read(f"{prefix}pages/{number:04d}.json")
        if raw is None:
            raise StorageError(f"the analysis of page {number} is missing")
        page = page_candidates(source, number)
        evidence = inference.PageEvidence(page=number)
        return evidence, inference.prepare(source, _page_record(raw), page, evidence)

    def _stage_a(self, prefix: str, runner, source: bytes, number: int) -> bool:
        """Stage A of one page, if this request claims it first. True when it did."""
        evidence, prepared = self._prepared(prefix, source, number)
        step = f"p{number:04d}-a"
        if not self._claim(prefix, step):
            return False
        self._halt_if_stopped(prefix, runner)
        output = None if prepared is None else runner.stage_a(evidence, prepared)
        self._write_once(
            f"{prefix}steps/{step}.json", _recorded(evidence.stage_a, evidence, output)
        )
        if output is None:
            reason = f"NOT_RUN: stage A has no answer ({evidence.stage_a})"
            skipped = inference.PageEvidence(page=number, stage_b=reason)
            self._finish_b(prefix, number, skipped.stage_b, skipped, None)
        elif not output.fields and not output.rejected and not prepared.candidates:
            checked = runner.stage_b(evidence, prepared, output)  # no call: nothing to check
            self._finish_b(prefix, number, evidence.stage_b, evidence, checked)
        return True

    def _stage_b(self, prefix: str, runner, source: bytes, number: int) -> bool:
        """Stage B of one page, if this request claims it first. True when it did."""
        drafted = self._read(f"{prefix}steps/p{number:04d}-a.json")
        evidence, prepared = self._prepared(prefix, source, number)
        if not self._claim(prefix, f"p{number:04d}-b"):
            return False
        self._halt_if_stopped(prefix, runner)
        output = None
        if prepared is None:
            evidence.stage_b = f"NOT_RUN: the page could not be prepared again ({evidence.stage_a})"
        elif evidence.image_sha256 != drafted["evidence"]["image_sha256"]:
            evidence.stage_b = "NOT_RUN: the page image differs from the one stage A saw"
        else:
            evidence.stage_a = drafted["status"]
            output = runner.stage_b(evidence, prepared, _output(drafted))
        self._finish_b(prefix, number, evidence.stage_b, evidence, output)
        return True

    def _finish_b(self, prefix, number, status, evidence, output) -> None:
        self._write_once(f"{prefix}steps/p{number:04d}-b.json", _recorded(status, evidence, output))

    def _halt_if_stopped(self, prefix: str, runner) -> None:
        """A step that began before another request stopped the draft sends nothing after it:
        the stop is read again once the call is claimed, just before it would be sent."""
        recorded = self._read(prefix + "violation.json") or self._read(prefix + "stop.json")
        if recorded:
            runner.stop.set(recorded["reason"])

    def _record_stop(self, prefix: str, runner) -> None:
        reason = runner.stop.reason
        if not reason:
            return
        record = {"reason": reason, "at": self.clock().isoformat()}
        if not self._write_once(prefix + "stop.json", record) and reason.startswith(
            "route violation"
        ):
            self._write_once(prefix + "violation.json", record)  # it outranks an earlier stop

    # ---- results ----

    def result(self, template_id: str, draft_id: str) -> dict:
        """The stage B fields, as the studio stores fields, and what kept others out."""
        header = self._header(template_id, draft_id)
        prefix = self._prefix(template_id, draft_id)
        fields, rejected, unfinished = [], [], []
        for number in header["pages"]:
            checked = self._read(f"{prefix}steps/p{number:04d}-b.json")
            if checked is None or not checked["status"].startswith(("RECEIVED", "NOT_NEEDED")):
                status = "not finished" if checked is None else checked["status"]
                unfinished.append({"page": number, "status": status})
                continue
            for index, raw in enumerate(checked["fields"], start=1):
                fields.append({"id": f"p{number:04d}-{index:03d}", **Field(**raw).to_checker(0)})
            rejected += [{"page": number, "reason": r["reason"]} for r in checked["rejected"]]
        stopped = self._read(prefix + "stop.json") or self._read(prefix + "violation.json")
        complete = (
            not unfinished and not rejected and not stopped and header["inventory"] == "COMPLETE"
        )
        return {
            "status": "COMPLETE" if complete else "PARTIAL",
            "fields": fields,
            "rejected": rejected,
            "unfinished": unfinished,
        }

    def apply(self, template_id: str, draft_id: str, actor: str, automatic: bool = False) -> dict:
        """Save the drafted fields as a new revision of the revision the draft started from.
        `automatic` records that drafting saved them on finishing, not a click."""
        current = self.view(template_id, draft_id)
        if current["state"] == RUNNING:
            raise Conflict("the draft is still running", current)
        outcome = self.result(template_id, draft_id)
        if not outcome["fields"]:
            raise Invalid(["the draft has no fields to apply"])
        try:  # a retried apply, even one whose record below was lost, returns the revision it made
            saved, _ = self.templates.save(
                template_id,
                current["base_revision"],
                self._header(template_id, draft_id)["policy"],  # the policy it was drafted for
                outcome["fields"],
                f"draft-{draft_id}",
            )
        except Conflict as moved:
            reason = "the template changed after this draft started; start a new draft"
            raise Conflict(reason, moved.current) from None
        record = {
            "revision": saved["revision"],
            "status": outcome["status"],
            "fields": len(outcome["fields"]),
            "rejected": len(outcome["rejected"]),
            "unfinished": [item["page"] for item in outcome["unfinished"]],
            "at": self.clock().isoformat(),
            "by": actor,
            "automatic": automatic,
        }
        self._write_once(self._prefix(template_id, draft_id) + "applied.json", record)
        return {"template": saved, "draft": self.view(template_id, draft_id), "result": outcome}

    def finish(self, template_id: str, draft_id: str, current: dict | None = None) -> dict:
        """Save a draft that has finished drafting as a revision, for whoever started it, so its
        fields reach the editor without a click; return its view. One whose fields cannot be
        saved (it found none, or the template moved on) stays drafted, and its view says why
        under `not_applied`."""
        current = current or self.view(template_id, draft_id)
        if current["state"] != DRAFTED:
            return current
        actor = self._header(template_id, draft_id)["started_by"]
        try:
            return self.apply(template_id, draft_id, actor, automatic=True)["draft"]
        except Conflict as moved:
            return {**current, "not_applied": moved.reason}
        except Invalid as refused:
            return {**current, "not_applied": str(refused)}

    def outcome(self, template_id: str, draft_id: str, revision: int) -> dict | None:
        """What a finished draft left on `revision`, for the editor: the fields it saved there,
        or, for a draft of that revision that found none to save, that it found none."""
        header = self._header(template_id, draft_id)
        applied = self._read(self._prefix(template_id, draft_id) + "applied.json")
        if applied is not None:
            if applied["revision"] != revision:
                return None
            return {
                "revision": revision,
                "status": applied["status"],
                "fields": applied["fields"],
                "rejected": applied.get("rejected", 0),  # not recorded by earlier studios
                "unfinished": applied.get("unfinished", []),
            }
        if (
            header["base_revision"] != revision
            or self.view(template_id, draft_id)["state"] != DRAFTED
        ):
            return None
        found = self.result(template_id, draft_id)
        if found["fields"]:
            return None  # fields not yet saved: the drafting page saves them
        return {
            "revision": revision,
            "status": found["status"],
            "fields": 0,
            "rejected": len(found["rejected"]),
            "unfinished": [item["page"] for item in found["unfinished"]],
        }
