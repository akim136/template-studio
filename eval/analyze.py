"""Run the shared analysis service on one local source PDF.

    uv run --locked python -m eval.analyze SOURCE.pdf > /path/outside/the/repo/run.json

Stdout carries exactly one JSON document. Diagnostics, including anything a library prints,
go to stderr: while analysis runs, both Python's stdout and file descriptor 1 point at stderr,
because PDFium is native code and would bypass a Python-level redirect. The wrapper supplies
bytes only; parsing stays in studio.services.analysis.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

from studio.services.analysis import (
    MAX_SOURCE_BYTES,
    AnalysisError,
    analyze,
    oversize_message,
)

CHUNK_BYTES = 1024 * 1024


@contextlib.contextmanager
def stdout_to_stderr():
    sys.stdout.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        with contextlib.suppress(Exception):  # flush C stdio buffers before fd 1 is restored
            ctypes.CDLL(None).fflush(None)
        sys.stderr.flush()
        os.dup2(saved, 1)
        os.close(saved)


def read_source(path: Path) -> bytes:
    """The source's bytes. Only a regular file is opened, and an oversize file is hashed in
    chunks for its failure record but never held in memory whole."""
    if not stat.S_ISREG(path.stat().st_mode):  # a FIFO or device could block or never end
        raise OSError(f"not a regular file: {path}")
    with path.open("rb") as handle:
        data = handle.read(MAX_SOURCE_BYTES + 1)
        if len(data) <= MAX_SOURCE_BYTES:
            return data
        digest, size = hashlib.sha256(data), len(data)
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    error = AnalysisError(oversize_message(size), b"", [])
    error.source_sha256, error.source_bytes = digest.hexdigest(), size
    raise error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.analyze", description=__doc__)
    parser.add_argument("source", type=Path, help="the source PDF")
    args = parser.parse_args(argv)

    stdout = sys.stdout
    with stdout_to_stderr():
        try:
            record, code = analyze(read_source(args.source)).to_dict(), 0
        except AnalysisError as error:
            record, code = error.to_dict(), 1
        except OSError as error:
            record, code = {"status": "FAILED", "error": str(error)}, 1
    for line in record.get("diagnostics", []):
        print(f"diagnostic: {line}", file=sys.stderr)
    json.dump(record, stdout, indent=2)
    stdout.write("\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
