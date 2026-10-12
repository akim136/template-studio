"""Run the shared analysis service on one local source PDF.

    uv run --locked python -m eval.analyze SOURCE.pdf > /path/outside/the/repo/run.json

Stdout carries exactly one JSON document. Diagnostics, including anything a library prints,
go to stderr. The wrapper supplies bytes only; parsing stays in studio.services.analysis.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

from studio.services.analysis import AnalysisError, analyze


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.analyze", description=__doc__)
    parser.add_argument("source", type=Path, help="the source PDF")
    args = parser.parse_args(argv)

    stdout = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        try:
            record, code = analyze(args.source.read_bytes()).to_dict(), 0
        except (OSError, AnalysisError) as error:
            record, code = {"status": "FAILED", "error": str(error)}, 1
    for line in record.get("diagnostics", []):
        print(f"diagnostic: {line}", file=sys.stderr)
    json.dump(record, stdout, indent=2)
    stdout.write("\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
