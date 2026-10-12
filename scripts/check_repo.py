#!/usr/bin/env python3
"""Focused repository checks for Homebase Template Studio. Standard library only.

  private  Block private inputs and answer-bearing artifacts from the index or history.

Exit codes: 0 pass, 1 findings, 2 usage or git error.

These checks back up explicit staging and human review. They do not isolate a
model from private files. Without the out-of-repo denylist they check only the
generic rules below, which is all hosted CI can do.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

MAX_BLOB_BYTES = 1024 * 1024
MIN_TOKEN_LENGTH = 4
BLOCKED_EXTENSIONS = {
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".7z",
    ".rar",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".pem",
    ".key",
    ".p12",
}
PRIVATE_DIRS = {"private", "evaluator", "overlays", "_incoming"}
PRIVATE_NAME = re.compile(r"^(access_roster|template_baseline_|build_fields_)|^\.env($|\.)", re.I)


class CheckError(Exception):
    """A usage or git failure (exit 2)."""


def git(repo: str, *args: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", repo, *args], input=data, capture_output=True, check=False
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise CheckError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def tree_records(output: bytes) -> list[tuple[str, str]]:
    """Parse `ls-files -s -z` or `ls-tree -r -z` output into (path, blob sha)."""
    entries = []
    for record in output.split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        fields = meta.split()
        if fields[0] == b"160000":  # submodule commit, not a blob
            continue
        sha = fields[2] if len(fields) == 3 and fields[1] == b"blob" else fields[1]
        entries.append((path.decode(), sha.decode()))
    return entries


def staged_entries(repo: str) -> list[tuple[str, str]]:
    return tree_records(git(repo, "ls-files", "-s", "-z"))


def history_entries(repo: str) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    for commit in git(repo, "rev-list", "--all").split():
        seen.update(tree_records(git(repo, "ls-tree", "-r", "-z", commit.decode())))
    return sorted(seen)


def blob_sizes(repo: str, shas: set[str]) -> dict[str, int]:
    if not shas:
        return {}
    request = "".join(f"{sha}\n" for sha in sorted(shas)).encode()
    out = git(repo, "cat-file", "--batch-check=%(objectname) %(objectsize)", data=request)
    return {name.decode(): int(size) for name, size in (line.split() for line in out.splitlines())}


def read_blobs(repo: str, shas: set[str]) -> dict[str, bytes]:
    ordered = sorted(shas)
    if not ordered:
        return {}
    out = git(repo, "cat-file", "--batch", data="".join(f"{s}\n" for s in ordered).encode())
    blobs, pos = {}, 0
    for _ in ordered:
        header_end = out.index(b"\n", pos)
        name, _kind, size = out[pos:header_end].split()
        start = header_end + 1
        blobs[name.decode()] = out[start : start + int(size)]
        pos = start + int(size) + 1
    return blobs


def load_denylist(path: str, repo: str) -> list[re.Pattern[bytes]]:
    file = Path(path).resolve()
    root = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if file.is_relative_to(root):
        raise CheckError("the denylist must live outside the repository")
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise CheckError(f"cannot read the denylist: {error.strerror}") from None
    patterns = []
    for number, line in enumerate(lines, 1):
        token = line.strip()
        if not token or token.startswith("#"):
            continue
        if len(token) < MIN_TOKEN_LENGTH:
            raise CheckError(
                f"denylist line {number}: tokens need at least {MIN_TOKEN_LENGTH} characters"
            )
        body = rb"\s+".join(re.escape(part.encode()) for part in token.split())
        patterns.append(re.compile(rb"(?<!\w)" + body + rb"(?!\w)", re.IGNORECASE))
    return patterns


def redact(text: str, deny: list[re.Pattern[bytes]]) -> str:
    data = text.encode()
    for pattern in deny:
        data = pattern.sub(b"[REDACTED]", data)
    return data.decode(errors="replace")


def path_reasons(path: str) -> list[str]:
    parts = PurePosixPath(path)
    reasons = []
    if parts.suffix.lower() in BLOCKED_EXTENSIONS:
        reasons.append(f"blocked extension {parts.suffix.lower()}")
    if PRIVATE_DIRS & {part.lower() for part in parts.parts[:-1]}:
        reasons.append("private or answer-bearing directory")
    if PRIVATE_NAME.search(parts.name):
        reasons.append("private or answer-bearing file name")
    return reasons


def content_reasons(data: bytes) -> list[str]:
    reasons = []
    if data.startswith(b"%PDF-"):
        reasons.append("PDF content")
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        reasons.append("ZIP content")
    return reasons


def run_private(args: argparse.Namespace) -> int:
    repo = args.repo
    deny = load_denylist(args.denylist, repo) if args.denylist else []
    entries = history_entries(repo) if args.all_history else staged_entries(repo)
    sizes = blob_sizes(repo, {sha for _, sha in entries})
    blobs = read_blobs(repo, {sha for sha, size in sizes.items() if size <= MAX_BLOB_BYTES})

    findings = []
    for path, sha in entries:
        reasons = path_reasons(path)
        if sizes[sha] > MAX_BLOB_BYTES:
            reasons.append(f"larger than {MAX_BLOB_BYTES} bytes")
        else:
            reasons += content_reasons(blobs[sha])
            hits = sum(1 for pattern in deny if pattern.search(blobs[sha]))
            if hits:
                reasons.append(f"content matches {hits} denylisted token(s)")
        if any(pattern.search(path.encode()) for pattern in deny):
            reasons.append("path matches a denylisted token")
        if reasons:
            findings.append(f"{redact(path, deny)} [{sha[:12]}]: {'; '.join(reasons)}")

    if args.all_history and deny:
        log = git(repo, "log", "--all", "--format=%H%n%an %ae%n%cn %ce%n%B%x00")
        for record in log.split(b"\0"):
            record = record.strip()
            hits = sum(1 for pattern in deny if pattern.search(record))
            if hits:
                commit = record.split(b"\n", 1)[0].decode()[:12]
                findings.append(f"commit {commit}: metadata or message matches {hits} token(s)")

    mode = "all history" if args.all_history else "index"
    scope = f"{len(deny)} denylist token(s)" if deny else "generic rules only, no denylist"
    if findings:
        print(f"PRIVATE CHECK FAILED ({mode}, {scope}):")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print(f"private check passed: {len(entries)} entries ({mode}, {scope})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".", help="repository path (default: .)")
    commands = parser.add_subparsers(dest="command", required=True)

    private = commands.add_parser("private", help="block private and answer-bearing files")
    mode = private.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="scan every index entry")
    mode.add_argument(
        "--all-history",
        action="store_true",
        help="scan every blob in every commit reachable from any ref, plus commit metadata",
    )
    private.add_argument(
        "--denylist", help="out-of-repo file of exact confidential tokens, one per line"
    )

    args = parser.parse_args(argv)
    try:
        return run_private(args)
    except CheckError as error:
        print(f"check_repo: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
