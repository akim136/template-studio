#!/usr/bin/env python3
"""Focused repository checks for Homebase Template Studio. Standard library only.

  private  Block private inputs and answer-bearing artifacts from the index or history.
  phase    Check this change against the phase policy read from the base revision.

Exit codes: 0 pass, 1 findings, 2 usage or git error, 3 bootstrap required (the
base has no phase policy, so Alex's manual review is the gate).

These checks back up explicit staging and human review. They do not isolate a
model from private files. Without the out-of-repo denylist they check only the
generic rules below, which is all hosted CI can do. The phase check reads policy
from the base so a branch cannot widen its own scope, but a branch can still edit
this script; the merge gate therefore runs the base revision's copy.
"""

from __future__ import annotations

import argparse
import json
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
POLICY_PATH = "docs/phase_scope.json"
BOOTSTRAP_REQUIRED = 3


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


def read_json(repo: str, ref: str, path: str) -> dict | None:
    """Return the JSON object at ref:path, or None when the file is absent there."""
    spec = f"{ref}:{path}"
    absent = subprocess.run(["git", "-C", repo, "cat-file", "-e", spec], capture_output=True)
    if absent.returncode != 0:
        return None
    try:
        value = json.loads(git(repo, "show", spec))
    except json.JSONDecodeError as error:
        raise CheckError(f"{spec} is not valid JSON: {error.msg}") from None
    if not isinstance(value, dict):
        raise CheckError(f"{spec} must be a JSON object")
    return value


def is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def load_policy(repo: str, ref: str) -> dict | None:
    policy = read_json(repo, ref, POLICY_PATH)
    if policy is None:
        return None
    features = policy.get("features")
    if not (
        all(isinstance(policy.get(key), str) for key in ("active_phase", "guide", "declaration"))
        and is_str_list(policy.get("scope_files"))
        and isinstance(features, dict)
        and features
        and all(isinstance(f, dict) and is_str_list(f.get("paths")) for f in features.values())
    ):
        raise CheckError(f"{ref}:{POLICY_PATH} is malformed")
    return policy


def covered(path: str, patterns: list[str]) -> bool:
    """Exact file match, or prefix match for entries ending in '/'."""
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in patterns)


def run_phase(args: argparse.Namespace) -> int:
    repo, base = args.repo, args.base
    try:
        base_sha = git(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").decode()
    except CheckError:
        raise CheckError(f"unknown base revision {base!r}") from None
    policy = load_policy(repo, base)
    if policy is None:
        proposed = load_policy(repo, "HEAD")
        head = (
            f"HEAD proposes active phase {proposed['active_phase']}"
            if proposed
            else ("HEAD has no policy either")
        )
        print(f"BOOTSTRAP_REQUIRED: {base} has no {POLICY_PATH}; {head}.")
        print("No trusted base policy exists. Alex's manual review is the gate.")
        return BOOTSTRAP_REQUIRED

    active, guide, decl_path = policy["active_phase"], policy["guide"], policy["declaration"]
    changed = [
        p
        for p in git(repo, "diff", "--name-only", "--no-renames", "-z", f"{base}...HEAD")
        .decode()
        .split("\0")
        if p
    ]
    findings = []
    if guide not in changed:
        findings.append(f"the guide {guide} was not amended")
    if decl_path not in changed:
        findings.append(f"the declaration {decl_path} was not refreshed")
    declaration = read_json(repo, "HEAD", decl_path) or {}
    kind, features = declaration.get("kind"), declaration.get("features", [])
    if declaration.get("phase") != active:
        findings.append(
            f"declared phase {declaration.get('phase')!r} does not match active phase {active!r}"
        )
    if not is_str_list(features):
        findings.append("features must be a list of feature IDs")
        features = []

    operational = [p for p in changed if p not in (guide, decl_path)]
    scope = policy["scope_files"]
    if kind == "feature":
        if not features:
            findings.append("a feature change must declare at least one feature")
        allowed = []
        for feature in features:
            if feature not in policy["features"]:
                findings.append(f"unknown feature {feature!r}")
            else:
                allowed += policy["features"][feature]["paths"]
        for path in operational:
            if covered(path, scope):
                findings.append(f"scope file {path} changes only with kind: scope_update")
            elif not covered(path, allowed):
                findings.append(f"{path} is outside the declared features")
    elif kind == "scope_update":
        if features:
            findings.append("a scope_update declares no features")
        for path in operational:
            if not covered(path, scope):
                findings.append(f"scope_update cannot bundle operational change {path}")
    else:
        findings.append(f"kind must be 'feature' or 'scope_update', not {kind!r}")

    where = f"base {base} {base_sha[:12]}"
    if findings:
        print(f"PHASE CHECK FAILED ({where}):")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print(f"phase check passed: {kind} in {active} ({where}, {len(changed)} changed path(s))")
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

    phase = commands.add_parser("phase", help="check this change against the base phase policy")
    phase.add_argument("--base", required=True, help="trusted base revision, e.g. main")

    args = parser.parse_args(argv)
    try:
        return run_private(args) if args.command == "private" else run_phase(args)
    except CheckError as error:
        print(f"check_repo: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
