"""Draft and self-check fields on one local source PDF through the authorized model route.

    uv run --locked python -m eval.infer SOURCE.pdf --model openai/gpt-4.1 --credential byok \\
        --roles Client,CounterSigner --signer-mode SIGNER_MODE --run-cap 2.00 \\
        --ledger PRIVATE/spend-ledger.jsonl --env-file PRIVATE/.env.local --out PRIVATE/run.json

Every route choice is explicit; there are no defaults for model, credential or caps. A byok run
stops at the first call that falls back to Vercel's credentials unless --accept-fallback is given;
with it, the run continues and the record counts every call that fell back. The full
record (it contains source text and model answers) goes to --out, outside the repository; it is
created owner-only and then made read-only (0400). Stdout carries one JSON summary with counts and
spend only.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from eval.analyze import read_source, stdout_to_stderr
from studio.services import gateway, inference
from studio.services.analysis import AnalysisError, analyze
from studio.services.spend import SpendLedger

ROOT = Path(__file__).resolve().parents[1]
PAGE_LIMIT = 10_000  # a page number past this is a typo; it is refused before expanding


def _outside_repo(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise SystemExit(f"{path} is inside the repository; private records stay outside it")
    return resolved


def _pages(text: str) -> list[int]:
    pages = set()
    for part in text.split(","):
        first, _, last = part.partition("-")
        first, last = int(first), int(last or first)
        if first < 1 or last < first or last > PAGE_LIMIT:
            raise ValueError(f"{part} is not a page range")
        pages.update(range(first, last + 1))
    return sorted(pages)


def _usd(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{text} is not an amount") from None
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{text} is not a positive amount")
    return value


def _summary(record: dict) -> dict:
    evidence = record["evidence"]
    return {
        "status": record["status"],
        "stop_reason": record["stop_reason"],
        "profile": record["profile"],
        "route": record["route"],
        "observed_routes": record["observed_routes"],
        "fallback_calls": record["fallback_calls"],
        "preflight": record["preflight"]["status"],
        "pages_requested": len(record["pages_requested"]),
        "pages_inspected": sum(1 for e in evidence if e["inspected"]),
        "stage_a_fields": len(record["stage_a"]["fields"]),
        "stage_a_rejected": len(record["stage_a"]["rejected"]),
        "stage_b_fields": len(record["stage_b"]["fields"]),
        "stage_b_rejected": len(record["stage_b"]["rejected"]),
        "calls": sum(len(e["calls"]) for e in evidence),
        "spend": record["spend"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.infer", description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--credential", required=True, choices=["byok", "system"])
    parser.add_argument("--roles", required=True, help="comma-separated, in signer-index order")
    parser.add_argument("--signer-mode", required=True)
    parser.add_argument("--run-cap", required=True, type=_usd, help="USD for this run")
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, help="holds VERCEL_OIDC_TOKEN when not in env")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--pages", type=_pages, help="e.g. 1-3,7; default every page")
    parser.add_argument("--workers", type=int, default=inference.MAX_WORKERS)
    parser.add_argument(
        "--accept-fallback",
        action="store_true",
        help="byok only: continue when a call falls back to Vercel's credentials, and record it",
    )
    args = parser.parse_args(argv)

    out, ledger_path = _outside_repo(args.out), _outside_repo(args.ledger)
    if out == ledger_path:
        raise SystemExit("the run record and the ledger must be different files")
    try:
        route = gateway.Route(args.model, args.credential, args.accept_fallback)
    except ValueError as error:
        raise SystemExit(f"not run: {error}") from None
    config = inference.RunConfig(
        route=route,
        roles=[role.strip() for role in args.roles.split(",")],
        signer_mode=args.signer_mode,
        workers=args.workers,
        pages=args.pages,
    )
    token = gateway.read_token(args.env_file)
    # The record is created before any call, so a bad --out never surfaces after spend.
    try:
        descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(f"{out} exists; run records are never overwritten") from None
    except OSError as error:
        raise SystemExit(f"cannot create {out}: {error.strerror}") from None

    stdout = sys.stdout
    record: dict = {"status": "NOT_RUN", "reason": "the run ended before a record was made"}
    try:
        with stdout_to_stderr():
            try:
                source = read_source(args.source)
                analysis = analyze(source)
            except (AnalysisError, OSError) as error:
                print(f"analysis failed: {error}", file=sys.stderr)
                record = {"status": "NOT_RUN", "reason": f"analysis failed: {error}"}
                return 1
            sha = hashlib.sha256(source).hexdigest()
            stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
            run_id = f"{stamp}-{sha[:8]}-{route.model.split('/')[-1]}"
            ledger = SpendLedger(ledger_path, run_id, args.run_cap)
            try:
                record = inference.run(
                    source, analysis, config, token=token, ledger=ledger, run_id=run_id
                )
            except ValueError as error:  # refused before any call: bad pages or workers
                print(f"not run: {error}", file=sys.stderr)
                record = {"status": "NOT_RUN", "reason": str(error), "run_id": run_id}
                return 2
            record["run_id"] = run_id
    finally:
        with open(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
        os.chmod(out, 0o400)
    json.dump({"run_id": run_id, **_summary(record)}, stdout, indent=2)
    stdout.write("\n")
    return 0 if record["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
