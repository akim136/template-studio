"""Draft and self-check fields on one local source PDF through the authorized model route.

    uv run --locked python -m eval.infer SOURCE.pdf --model openai/gpt-4.1 --credential byok \\
        --roles Client,CounterSigner --signer-mode SIGNER_MODE --run-cap 2.00 \\
        --ledger PRIVATE/spend-ledger.jsonl --env-file PRIVATE/.env.local --out PRIVATE/run.json

Every route choice is explicit; there are no defaults for model, credential or caps. The full
record (it contains source text and model answers) goes to --out, outside the repository, and is
made read-only. Stdout carries one JSON summary with counts and spend only.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

from eval.analyze import read_source, stdout_to_stderr
from studio.services import gateway, inference
from studio.services.analysis import AnalysisError, analyze
from studio.services.spend import SpendLedger

ROOT = Path(__file__).resolve().parents[1]


def _outside_repo(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise SystemExit(f"{path} is inside the repository; private records stay outside it")
    return resolved


def _pages(text: str | None) -> list[int] | None:
    if not text:
        return None
    pages = set()
    for part in text.split(","):
        first, _, last = part.partition("-")
        pages.update(range(int(first), int(last or first) + 1))
    return sorted(pages)


def _summary(record: dict) -> dict:
    evidence = record["evidence"]
    return {
        "status": record["status"],
        "stop_reason": record["stop_reason"],
        "profile": record["profile"],
        "route": record["route"],
        "observed_routes": record["observed_routes"],
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
    parser.add_argument("--run-cap", required=True, type=float, help="USD for this run")
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, help="holds VERCEL_OIDC_TOKEN when not in env")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--pages", help="e.g. 1-3,7; default every page")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    out, ledger_path = _outside_repo(args.out), _outside_repo(args.ledger)
    if out.exists():
        raise SystemExit(f"{out} exists; run records are never overwritten")
    route = gateway.Route(args.model, args.credential)
    config = inference.RunConfig(
        route=route,
        roles=[role.strip() for role in args.roles.split(",")],
        signer_mode=args.signer_mode,
        workers=args.workers,
        pages=_pages(args.pages),
    )
    token = gateway.read_token(args.env_file)

    stdout = sys.stdout
    with stdout_to_stderr():
        try:
            source = read_source(args.source)
            analysis = analyze(source)
        except (AnalysisError, OSError) as error:
            print(f"analysis failed: {error}", file=sys.stderr)
            return 1
        sha = hashlib.sha256(source).hexdigest()
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{stamp}-{sha[:8]}-{route.model.split('/')[-1]}"
        ledger = SpendLedger(ledger_path, run_id, args.run_cap)
        record = inference.run(source, analysis, config, token=token, ledger=ledger, run_id=run_id)
        record["run_id"] = run_id
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.chmod(out, 0o444)
    json.dump({"run_id": run_id, **_summary(record)}, stdout, indent=2)
    stdout.write("\n")
    return 0 if record["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
