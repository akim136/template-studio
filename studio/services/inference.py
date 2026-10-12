"""Field drafting (stage A) and self-check (stage B) over an analyzed source.

Profile PROVISIONAL_DEVELOPMENT: the prompts, line-field heights and required-field policy are
development choices, not an agreed case policy.

What the model receives, per page and nothing else: the instructions and business rules in this
module, the page image rendered from the source, the page's text layer, and the page's
candidates. Stage B also receives its own stage A fields and the same image with those fields
drawn on it. No file other than the source is read to build a request, and no tools are offered.

Before any page is sent, one preflight call with no page content proves the route. The gateway
cannot be told to refuse a fallback from Alex's key to Vercel's credentials, so the route is
also checked on every answer, and a violation stops the run; calls already in flight when it
happens (at most `workers`) have been sent. A rotated page is not sent: its fields need an
audited normalizer first.

Stage A and stage B are separate, immutable records. A page whose stage B failed has no final
fields; stage A is never substituted for it. Rendering a page is not inspection: a page counts as
inspected only when the model's answer for it was received and parsed. A run is COMPLETE only
when every page was inspected, no final field was rejected and nothing stopped the run.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from studio.services import gateway
from studio.services.analysis import AnalysisResult
from studio.services.candidates import PageCandidates, extract_candidates
from studio.services.fields import (
    MERGE_NAMES,
    Field,
    envelope,
    field_dict,
    resolve,
    stage_schema,
)
from studio.services.overlay import draw_boxes
from studio.services.render import encode_png, render_rgb
from studio.services.spend import BudgetExceeded, SpendLedger

PROFILE = "PROVISIONAL_DEVELOPMENT"
MODEL_IMAGE_SHORT_SIDE = 768  # pixels: the size a high-detail page image is viewed at
MAX_TEXT_CHARS = 8000  # page text beyond this is cut, and the cut is recorded
MAX_OUTPUT_TOKENS = 6000
MAX_WORKERS = 4  # concurrent page calls: also the most pages a mid-run fallback can carry
PREFLIGHT_TEXT = "Route check. Return an empty field list and empty notes."
PREFLIGHT_OUTPUT_TOKENS = 1000

RULES = """Business rules (provisional development policy):
- Roles: {roles}. "sender" is the party that prepares the document before it is sent.
- A merge field (text-merge, checkbox-merge) is filled by the sender from data and must use one
  of these merge names: {merges}. Every other field is completed by one of the roles.
- Mark a field required only when the document shows it must be completed; otherwise false."""

STAGE_A = """You place e-signature fields on one page of a PDF form.
Find every place where a role must sign, initial, date, write or tick, and every value the sender
fills from data. Return one entry per field.
- type: signature, initials, date_signed, text, checkbox, or text-merge / checkbox-merge.
- role: who completes it.
- candidate_id: when the field sits on a listed candidate (an underscore run, a drawn line or a
  checkbox shape), give its id; its exact position is used. Otherwise null.
- box: the field's area in pixels of the attached image (left, top, width, height). Always give
  it, even with a candidate.
- evidence: the printed label or instruction that shows this is a field.
Candidates are positions, not answers: skip candidates that are not fields, and add fields that
have no candidate. If the page has no fields, return an empty list.

{rules}"""

STAGE_B = """You are checking a first draft of e-signature fields on one page of a PDF form.
The image shows the page with each draft field outlined and numbered (red signature, orange
initials, purple date, blue text, teal text-merge, green checkbox). The draft is listed below by
the same numbers. Return the complete corrected list of fields for the page: keep correct fields,
fix wrong ones, remove entries that are not fields, and add any field the draft missed. Describe
each change in `changes`. Use the same field format and rules as the draft.

{rules}"""


@dataclass(frozen=True)
class RunConfig:
    route: gateway.Route
    roles: list[str]
    signer_mode: str
    workers: int = 4
    pages: list[int] | None = None  # None: every page

    def rules(self) -> str:
        roles = ", ".join(f"{role} (index {index})" for index, role in enumerate(self.roles))
        return RULES.format(roles=roles, merges=", ".join(MERGE_NAMES))


@dataclass
class PageEvidence:
    page: int
    text_chars: int = 0
    text_sent_chars: int = 0
    candidates: int = 0
    candidates_note: str = ""
    image_sha256: str | None = None  # of the PNG submitted in stage A
    image_size_px: list[int] | None = None
    scale: float | None = None  # image pixels per source point
    geometry_note: str = ""  # set when the visible area differs from the media box
    stage_a: str = "NOT_RUN"
    stage_b: str = "NOT_RUN"
    inspected: bool = False  # the model's answer for this page was received and parsed
    final_rejected: int = 0  # final (stage B) fields rejected as invalid
    calls: list[dict] = field(default_factory=list)


@dataclass
class StageOutput:
    fields: list[Field]
    rejected: list[dict]
    notes: str
    changes: list[str] | None
    raw: str


class _Stop:
    """Why the run stopped. The first reason wins, except that a route violation outranks any
    other: it is the one a reader must not miss."""

    def __init__(self):
        self.event, self.reason, self.lock = threading.Event(), "", threading.Lock()

    def set(self, reason: str):
        with self.lock:
            violation = "route violation"
            if not self.reason or (
                reason.startswith(violation) and not self.reason.startswith(violation)
            ):
                self.reason = reason
            self.event.set()


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _candidate_lines(page: PageCandidates, scale: float) -> str:
    if not page.candidates:
        return "Candidates: none."
    lines = ["Candidates (pixels in the attached image):"]
    for c in page.candidates:
        box = (round(c.x * scale), round(c.y * scale), round(c.width * scale))
        lines.append(
            f"{c.id} {c.kind} left={box[0]} top={box[1]} width={box[2]} "
            f"height={max(round(c.height * scale), 1)} | left: {c.left_text!r} | "
            f"right: {c.right_text!r} | below: {c.below_text!r}"
        )
    return "\n".join(lines)


def _draft_lines(fields: list[Field], scale: float) -> str:
    if not fields:
        return "Draft fields: none."
    lines = ["Draft fields (pixels in the attached image):"]
    for number, f in enumerate(fields, start=1):
        lines.append(
            f"{number}. type={f.type} signer={f.signer} required={str(f.required).lower()} "
            f"merge_name={f.name} candidate_id={f.candidate_id} "
            f"box=({round(f.x * scale)}, {round(f.y * scale)}, {round(f.width * scale)}, "
            f"{round(f.height * scale)}) evidence={f.evidence!r}"
        )
    return "\n".join(lines)


class Runner:
    def __init__(self, source, analysis, config, *, token, ledger, transport, run_id):
        self.source, self.analysis, self.config = source, analysis, config
        self.token, self.ledger, self.transport, self.run_id = token, ledger, transport, run_id
        self.stop = _Stop()
        self.observed: set[tuple[str, str, str]] = set()
        self.lock = threading.Lock()

    def _guard(self, call):
        """(status, result) of one model call. A failure is recorded; one that concerns the route
        or the budget, or would repeat on every call, also stops the run."""
        if self.stop.event.is_set():
            return f"NOT_RUN: {self.stop.reason}", None
        try:
            return "RECEIVED", call()
        except gateway.RouteViolation as error:
            self.stop.set(f"route violation: {error}")
            return f"ROUTE_VIOLATION: {error}", None
        except BudgetExceeded as error:
            self.stop.set(f"budget: {error}")
            return f"BUDGET_STOPPED: {error}", None
        except gateway.GatewayError as error:
            if error.fatal:
                self.stop.set(f"gateway: {error}")
            return f"FAILED: {error}", None
        except Exception as error:  # unexpected: stop rather than repeat it on every page
            self.stop.set(f"error: {type(error).__name__}")
            return f"FAILED: {type(error).__name__}: {error}", None

    def _observe(self, result):
        with self.lock:
            self.observed.add(
                (result.observed_model, result.observed_provider, result.observed_credential)
            )

    def preflight(self) -> dict:
        """One call with no page content, so a wrong route is found before any page is sent."""
        route = self.config.route

        def call():
            return gateway.call(
                route,
                [{"role": "user", "content": PREFLIGHT_TEXT}],
                stage_schema(self.config.roles, False),
                token=self.token,
                ledger=self.ledger,
                label=f"{self.run_id} preflight",
                worst_case=gateway.worst_case_usd(
                    route, len(PREFLIGHT_TEXT), 0, PREFLIGHT_OUTPUT_TOKENS
                ),
                max_output_tokens=PREFLIGHT_OUTPUT_TOKENS,
                transport=self.transport,
            )

        status, result = self._guard(call)
        if result is None:
            return {"status": status}
        self._observe(result)
        return {
            "status": "PASSED",
            "generation_id": result.generation_id,
            "failed_attempts": result.failed_attempts,
        }

    def _call(self, evidence, stage, system, user_text, png, self_check):
        roles = self.config.roles
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": _data_url(png), "detail": "high"}},
                ],
            },
        ]
        worst = gateway.worst_case_usd(
            self.config.route, len(system) + len(user_text), 1, MAX_OUTPUT_TOKENS
        )
        result = gateway.call(
            self.config.route,
            messages,
            stage_schema(roles, self_check),
            token=self.token,
            ledger=self.ledger,
            label=f"{self.run_id} page {evidence.page} stage {stage}",
            worst_case=worst,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            transport=self.transport,
        )
        self._observe(result)
        evidence.calls.append(
            {
                "stage": stage,
                "generation_id": result.generation_id,
                "observed_model": result.observed_model,
                "observed_credential": result.observed_credential,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "market_cost_usd": str(result.market_cost_usd),
                "failed_attempts": result.failed_attempts,
                "seconds": result.seconds,
            }
        )
        return result.content

    def _stage(self, evidence, stage, system, text, png, candidates, scale, size, self_check):
        """Run one stage on one page; returns the output or None, recording why in evidence."""
        status, content = self._guard(
            lambda: self._call(evidence, stage, system, text, png, self_check)
        )
        if content is None:
            return status, None
        try:
            answer = json.loads(content)
            fields, rejected = resolve(
                evidence.page, answer["fields"], self.config.roles, candidates, scale, size
            )
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            return f"FAILED: unusable answer: {type(error).__name__}: {error}", None
        output = StageOutput(
            fields=fields,
            rejected=[{"raw": r.raw, "reason": r.reason} for r in rejected],
            notes=answer.get("notes", ""),
            changes=answer.get("changes") if self_check else None,
            raw=content,
        )
        return "RECEIVED", output

    def page(self, number: int, page_candidates: PageCandidates):
        """One page, start to finish. An unexpected failure is recorded on the page and stops
        the run, so the run still ends with a record; a received stage A is kept."""
        evidence, done = PageEvidence(page=number), {}
        try:
            return self._page(evidence, done, number, page_candidates)
        except Exception as error:
            self.stop.set(f"error: {type(error).__name__}")
            failed = f"FAILED: {type(error).__name__}: {error}"
            if "a" in done:
                evidence.stage_b = failed
            else:
                evidence.stage_a = failed
            evidence.inspected = False
            return evidence, done.get("a"), None

    def _page(self, evidence: PageEvidence, done: dict, number: int, page_candidates):
        record = self.analysis.pages[number - 1]
        evidence.text_chars = len(record.text)
        evidence.candidates = len(page_candidates.candidates)
        notes = [page_candidates.reason] if page_candidates.reason else []
        if page_candidates.truncated_from:
            notes.insert(0, f"cut from {page_candidates.truncated_from}")
        evidence.candidates_note = "; ".join(notes)
        if self.stop.event.is_set():
            evidence.stage_a = f"NOT_RUN: {self.stop.reason}"
            return evidence, None, None
        if page_candidates.rotation or record.rotation:
            evidence.stage_a = "NOT_RUN: rotated page; its fields need an audited normalizer"
            return evidence, None, None
        if record.image is None:
            evidence.stage_a = f"NOT_RUN: no page image ({record.image_reason})"
            return evidence, None, None
        # The visible area as PDFium renders it: the image, the candidates and the fields all
        # use this frame, measured from its top-left corner.
        size = (page_candidates.width_pt, page_candidates.height_pt)
        if min(size) <= 0:
            evidence.stage_a = f"NOT_RUN: the page was not measured ({page_candidates.reason})"
            return evidence, None, None
        media = (record.width_pt, record.height_pt)
        if None not in media and max(abs(a - b) for a, b in zip(size, media, strict=True)) > 0.5:
            evidence.geometry_note = (
                f"the visible area is {size[0]:g} x {size[1]:g} pt and the media box "
                f"{media[0]:g} x {media[1]:g} pt; coordinates are from the visible area"
            )
        scale = MODEL_IMAGE_SHORT_SIDE / min(size)
        try:
            image = render_rgb(self.source, number, scale)
        except Exception as error:
            evidence.stage_a = f"NOT_RUN: render failed: {type(error).__name__}: {error}"
            return evidence, None, None
        png = encode_png(image)
        evidence.image_sha256 = hashlib.sha256(png).hexdigest()
        evidence.image_size_px, evidence.scale = [image.width, image.height], round(scale, 4)

        text = record.text[:MAX_TEXT_CHARS]
        evidence.text_sent_chars = len(text)
        cut = f" (cut from {len(record.text)} characters)" if len(text) < len(record.text) else ""
        candidates = page_candidates.candidates
        context = (
            f"Page {number}. The image is {image.width} x {image.height} pixels.\n"
            f"{_candidate_lines(page_candidates, scale)}\n\nPage text{cut}:\n{text}"
        )
        rules = self.config.rules()
        evidence.stage_a, stage_a = self._stage(
            evidence, "A", STAGE_A.format(rules=rules), context, png, candidates, scale, size, False
        )
        if stage_a is None:
            return evidence, None, None
        done["a"] = stage_a
        if not stage_a.fields and not stage_a.rejected and not candidates:
            evidence.stage_b = "NOT_NEEDED: no draft fields, no rejected fields, no candidates"
            evidence.inspected = True
            return evidence, stage_a, StageOutput([], [], "", [], "")
        boxes = [
            (n, f.type, f.x * scale, f.y * scale, f.width * scale, f.height * scale)
            for n, f in enumerate(stage_a.fields, start=1)
        ]
        overlay_png = encode_png(draw_boxes(image, boxes))
        check_context = context + "\n\n" + _draft_lines(stage_a.fields, scale)
        evidence.stage_b, stage_b = self._stage(
            evidence,
            "B",
            STAGE_B.format(rules=rules),
            check_context,
            overlay_png,
            candidates,
            scale,
            size,
            True,
        )
        evidence.inspected = stage_b is not None
        evidence.final_rejected = len(stage_b.rejected) if stage_b is not None else 0
        return evidence, stage_a, stage_b


def _status(stop: _Stop, evidence: list[PageEvidence], analysis: AnalysisResult) -> str:
    if stop.reason.startswith("route violation"):
        return "ROUTE_VIOLATION"
    if stop.reason.startswith("budget"):
        return "BUDGET_STOPPED"
    done = all(e.inspected and not e.final_rejected for e in evidence)
    every_page = len(evidence) == analysis.page_count
    complete = done and every_page and analysis.inventory == "COMPLETE" and not stop.reason
    return "COMPLETE" if complete else "PARTIAL"


def run(
    source: bytes,
    analysis: AnalysisResult,
    config: RunConfig,
    *,
    token: str,
    ledger: SpendLedger,
    run_id: str,
    transport: gateway.Transport | None = None,  # None: the real gateway
) -> dict:
    if hashlib.sha256(source).hexdigest() != analysis.source_sha256:
        raise ValueError("the analysis is of a different source")
    if not 1 <= config.workers <= MAX_WORKERS:
        raise ValueError(f"workers must be between 1 and {MAX_WORKERS}")
    count = len(analysis.pages)
    numbers = list(range(1, count + 1)) if config.pages is None else list(config.pages)
    if (
        not numbers
        or len(set(numbers)) != len(numbers)
        or not all(1 <= n <= count for n in numbers)
    ):
        raise ValueError(f"pages must be unique and within 1..{count}, got {numbers}")
    candidates = {page.page: page for page in extract_candidates(source, numbers)}
    runner = Runner(
        source, analysis, config, token=token, ledger=ledger, transport=transport, run_id=run_id
    )
    preflight = runner.preflight()
    with ThreadPoolExecutor(max_workers=config.workers) as pool:
        futures = [
            pool.submit(runner.page, n, candidates.get(n, PageCandidates(n, [], reason="none")))
            for n in numbers
        ]
        results = [future.result() for future in futures]

    evidence = [r[0] for r in results]
    final_pages = [e.page for e, _, b in results if b is not None]
    a_fields = [f for _, a, _ in results if a is not None for f in a.fields]
    b_fields = [f for _, _, b in results if b is not None for f in b.fields]
    measured = {n: (c.width_pt, c.height_pt) for n, c in candidates.items() if c.width_pt}
    pages_meta = [
        {
            "document_index": 0,
            "page": p.page,
            "width": measured.get(p.page, (p.width_pt, p.height_pt))[0],
            "height": measured.get(p.page, (p.width_pt, p.height_pt))[1],
            "rotation": p.rotation,
        }
        for p in analysis.pages
    ]
    rules = config.rules()
    rules_sha = hashlib.sha256(rules.encode()).hexdigest()
    common = {
        "source_sha256": analysis.source_sha256,
        "pages": pages_meta,
        "signer_mode": config.signer_mode,
        "business_rules_sha256": rules_sha,
    }
    all_total, run_total = ledger.totals()
    return {
        "profile": PROFILE,
        "status": _status(runner.stop, evidence, analysis),
        "stop_reason": runner.stop.reason,
        "route": {"model": config.route.model, "credential": config.route.credential},
        "observed_routes": sorted(list(route) for route in runner.observed),
        "preflight": preflight,
        "source_sha256": analysis.source_sha256,
        "analysis_inventory": analysis.inventory,
        "pages_requested": numbers,
        "pages_with_final_fields": final_pages,
        "business_rules": rules,
        "business_rules_sha256": rules_sha,
        "evidence": [e.__dict__ for e in evidence],
        "stage_a": {
            "envelope": envelope(**common, fields=a_fields, output_stage="A"),
            "fields": [field_dict(f) for f in a_fields],
            "rejected": [r for _, a, _ in results if a is not None for r in a.rejected],
            "notes": {e.page: a.notes for e, a, _ in results if a is not None},
            "raw": {e.page: a.raw for e, a, _ in results if a is not None},
        },
        "stage_b": {
            "envelope": envelope(**common, fields=b_fields, output_stage="B"),
            "fields": [field_dict(f) for f in b_fields],
            "rejected": [r for _, _, b in results if b is not None for r in b.rejected],
            "changes": {e.page: b.changes for e, _, b in results if b is not None},
            "notes": {e.page: b.notes for e, _, b in results if b is not None},
            "raw": {e.page: b.raw for e, _, b in results if b is not None},
        },
        "spend": {"run_usd": round(run_total, 6), "all_runs_usd": round(all_total, 6)},
    }
