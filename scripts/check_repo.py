#!/usr/bin/env python3
"""Focused repository checks for Homebase Template Studio. Standard library only.

  private  Block private inputs and answer-bearing artifacts from the index or history.
  phase    Check this change against the phase policy read from the base revision. When
           the base policy names a `changes` directory, the change declares itself in the
           one note it adds there instead of amending the guide and the declaration file.

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
PRIVATE_DIRS = {"private", "evaluator", "overlays", "baseline", "_incoming"}
PRIVATE_NAME = re.compile(
    r"^(access[_ -]?roster|template[_ -]?baseline|build[_ -]?fields)|^\.env($|\.)", re.I
)
# A PDF header may follow up to 1 KiB of leading bytes; a ZIP is found by its end record.
PDF_HEADER = re.compile(rb"(?:^|[\r\n\0])%PDF-\d")
ZIP_END = b"PK\x05\x06"
ZIP_TAIL = 22 + 65535
POLICY_PATH = "docs/phase_scope.json"
NOTE_NAME = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
NOTE_KEYS = ("phase", "kind", "features")
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
    if not path:
        raise CheckError("--denylist needs a file path")
    file = Path(path).resolve()
    if file.is_relative_to(Path(repo).resolve()):
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
        # Letters and digits continue a token; "_", "@", "." and the like end it.
        left = rb"(?<![A-Za-z0-9])" if token[0].isalnum() else b""
        right = rb"(?![A-Za-z0-9])" if token[-1].isalnum() else b""
        patterns.append(re.compile(left + body + right, re.IGNORECASE))
    if not patterns:
        raise CheckError("the denylist has no tokens")
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
    if PDF_HEADER.search(data[: 1024 + 8]):
        reasons.append("PDF content")
    if data.startswith(b"PK\x03\x04") or ZIP_END in data[-ZIP_TAIL:]:
        reasons.append("ZIP content")
    return reasons


def run_private(args: argparse.Namespace) -> int:
    repo = args.repo
    deny = load_denylist(args.denylist, repo) if args.denylist is not None else []
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


def exists(repo: str, ref: str, path: str) -> bool:
    check = subprocess.run(
        ["git", "-C", repo, "cat-file", "-e", f"{ref}:{path}"], capture_output=True
    )
    return check.returncode == 0


def read_json(repo: str, ref: str, path: str) -> dict | None:
    """Return the JSON object at ref:path, or None when the file is absent there."""
    spec = f"{ref}:{path}"
    if not exists(repo, ref, path):
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
        and (
            "changes" not in policy
            or (isinstance(policy["changes"], str) and policy["changes"].endswith("/"))
        )
    ):
        raise CheckError(f"{ref}:{POLICY_PATH} is malformed")
    return policy


def covered(path: str, patterns: list[str]) -> bool:
    """Exact file match, or prefix match for entries ending in '/'."""
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in patterns)


def parse_note(text: str) -> tuple[dict, list[str]]:
    """A change note's declaration: a header of phase, kind and features lines between two
    '---' lines (features comma-separated, empty for a scope_update), then the note's text."""
    lines = [line.strip() for line in text.split("\n")]
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return {}, ["the note must start with a header between two '---' lines"]
    end = lines.index("---", 1)
    declaration: dict = {}
    problems = []
    for line in lines[1:end]:
        key, colon, value = (part.strip() for part in line.partition(":"))
        if not colon or key not in NOTE_KEYS or key in declaration:
            problems.append(f"unexpected header line {line[:60]!r}")
        elif key == "features":
            declaration[key] = [item.strip() for item in value.split(",") if item.strip()]
        else:
            declaration[key] = value
    problems += [f"the header has no {key!r} line" for key in NOTE_KEYS if key not in declaration]
    if not "".join(lines[end + 1 :]):
        problems.append("the note has no text after its header")
    return declaration, problems


def note_declaration(repo: str, base: str, notes: str, findings: list[str]) -> tuple[dict, str]:
    """The declaration in the one note this change adds under `notes`, and that note's path.
    Notes already at the base are the record of earlier changes and never change."""
    fields = git(
        repo, "diff", "--name-status", "--no-relative", "--no-renames", "-z", f"{base}...HEAD"
    )
    fields = [f for f in fields.decode().split("\0") if f]
    added = []
    for status, path in zip(fields[0::2], fields[1::2], strict=True):
        if path.startswith(notes):
            if status == "A":
                added.append(path)
            else:
                findings.append(f"the change note {path} exists at the base and cannot change")
    if len(added) != 1:
        findings.append(f"add exactly one change note under {notes} (found {len(added)})")
        return {}, ""
    path = added[0]
    if not NOTE_NAME.fullmatch(path[len(notes) :]):
        findings.append(f"the change note {path} must be named YYYY-MM-DD-short-name.md")
    declaration, problems = parse_note(git(repo, "show", f"HEAD:{path}").decode(errors="replace"))
    findings += [f"the change note {path}: {problem}" for problem in problems]
    return declaration, path


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
    diff = git(repo, "diff", "--name-only", "--no-relative", "--no-renames", "-z", f"{base}...HEAD")
    changed = [p for p in diff.decode().split("\0") if p]
    findings: list[str] = []
    if not exists(repo, "HEAD", guide):
        findings.append(f"the guide {guide} is missing at HEAD")
    if "changes" in policy:
        # The note replaces the guide edit and the declaration file; the guide may still change.
        declaration, note = note_declaration(repo, base, policy["changes"], findings)
        operational = [p for p in changed if p not in (guide, note)]
    else:
        if guide not in changed and exists(repo, "HEAD", guide):
            findings.append(f"the guide {guide} was not amended")
        declaration = read_json(repo, "HEAD", decl_path)
        if declaration is None:
            findings.append(f"the declaration {decl_path} is missing at HEAD")
            declaration = {}
        elif decl_path not in changed:
            findings.append(f"the declaration {decl_path} was not refreshed")
        operational = [p for p in changed if p not in (guide, decl_path)]
    kind, features = declaration.get("kind"), declaration.get("features", [])
    if not is_str_list(features):
        findings.append("features must be a list of feature IDs")
        features = []

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
        # The proposed policy governs the next change, so it must load; it may advance the phase.
        try:
            proposed = load_policy(repo, "HEAD")
        except CheckError as error:
            findings.append(str(error))
        else:
            if proposed is None:
                findings.append(f"a scope_update cannot delete {POLICY_PATH}")
            else:
                active = proposed["active_phase"]
    else:
        findings.append(f"kind must be 'feature' or 'scope_update', not {kind!r}")
    if declaration.get("phase") != active:
        findings.append(
            f"declared phase {declaration.get('phase')!r} does not match active phase {active!r}"
        )

    where = f"base {base} {base_sha[:12]}"
    if findings:
        print(f"PHASE CHECK FAILED ({where}):")
        for finding in findings:
            print(f"  {finding}")
        return 1
    verb = "to" if kind == "scope_update" else "in"
    print(f"phase check passed: {kind} {verb} {active} ({where}, {len(changed)} changed path(s))")
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
        # Subdirectory paths would narrow ls-files, ls-tree and diff to that subtree.
        args.repo = git(args.repo, "rev-parse", "--show-toplevel").decode().strip()
        return run_private(args) if args.command == "private" else run_phase(args)
    except CheckError as error:
        print(f"check_repo: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
