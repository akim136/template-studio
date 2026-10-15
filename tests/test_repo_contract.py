"""The app, phase policy and docs must tell the same story in every phase."""

import json
import re
import subprocess
from pathlib import Path

import pytest

import app
from scripts.check_repo import parse_note
from tests.repo_helpers import SCRIPT, Repo, git_env

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_phase_contract(policy: dict, declaration: dict) -> None:
    assert policy["active_phase"] == declaration["phase"]
    assert set(declaration["features"]) <= set(policy["features"])


def current_declaration(root: Path, policy: dict) -> dict:
    """The declaration of the change note merged last, read with the checker's own parser, when
    the policy names a changes directory; otherwise the old declaration file. "Last" follows the
    first-parent history (merge order), not the notes' names. A merge can bring several notes at
    once, such as a stack, and phases only advance, so the highest phase among them is current."""
    if "changes" not in policy:
        return json.loads((root / policy["declaration"]).read_text(encoding="utf-8"))
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--first-parent", "-m", "--diff-filter=A"]
        + ["--name-only", "--format=%x00", "--", policy["changes"]],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    added = next((c.split() for c in log.split("\0") if c.strip()), [])
    assert added, f"no change note under {policy['changes']}"
    declarations = []
    for path in added:
        declaration, problems = parse_note((root / path).read_text(encoding="utf-8"))
        assert problems == [], (path, problems)
        declarations.append(declaration)
    return max(declarations, key=lambda d: int(d["phase"].removeprefix("P")))


def test_policy_declaration_and_app_agree() -> None:
    policy = json.loads(read("docs/phase_scope.json"))
    assert_phase_contract(policy, current_declaration(ROOT, policy))
    # The phase lives only in the policy, so a scope_update can advance it without app changes.
    assert not hasattr(app, "PHASE")
    assert app.ANALYSIS_STATUS == "DRAFTING_HOSTED"


def test_contract_allows_a_scope_update_to_advance_the_phase() -> None:
    policy = {**json.loads(read("docs/phase_scope.json")), "active_phase": "P1"}
    assert_phase_contract(policy, {"phase": "P1", "kind": "scope_update", "features": []})


def write_note(repo: Repo, name: str, phase: str, kind: str, features: str) -> str:
    path = f"notes/{name}"
    repo.write(path, f"---\nphase: {phase}\nkind: {kind}\nfeatures: {features}\n---\n\nWhy.\n")
    return path


@pytest.fixture
def advanced(tmp_path: Path) -> tuple[Repo, dict]:
    """A policy advanced to P5, with notes but no old declaration file."""
    repo = Repo(tmp_path / "repo")
    policy = {
        **json.loads(read("docs/phase_scope.json")),
        "active_phase": "P5",
        "changes": "notes/",
    }
    repo.commit("docs", write_note(repo, "2026-10-15-docs.md", "P4", "feature", "F-P4d-docs"))
    return repo, policy


def test_the_note_merged_last_declares_the_phase_whatever_its_name(advanced) -> None:
    repo, policy = advanced
    repo.commit("fix", write_note(repo, "2026-10-16-zeta.md", "P4", "feature", "F-P4d-docs"))
    repo.commit("advance", write_note(repo, "2026-10-16-advance.md", "P5", "scope_update", ""))

    declaration = current_declaration(repo.root, policy)

    assert declaration == {"phase": "P5", "kind": "scope_update", "features": []}
    assert_phase_contract(policy, declaration)


def test_a_merge_that_brings_several_notes_declares_the_latest_phase(advanced) -> None:
    repo, policy = advanced
    repo.git("switch", "-q", "-c", "stack")
    repo.commit("fix", write_note(repo, "2026-10-16-a-fix.md", "P4", "feature", "F-P4d-docs"))
    repo.commit("advance", write_note(repo, "2026-10-16-advance.md", "P5", "scope_update", ""))
    repo.git("switch", "-q", "main")
    repo.commit("other", write_note(repo, "2026-10-16-zz-other.md", "P4", "feature", "F-P4d-docs"))
    repo.git("merge", "-q", "--no-ff", "-m", "merge the stack", "stack")

    declaration = current_declaration(repo.root, policy)

    assert declaration["phase"] == "P5"
    assert_phase_contract(policy, declaration)


def test_docs_state_truthful_status() -> None:
    guide = read("docs/PROJECT_GUIDE.md")
    readme = read("README.md")

    for text in (guide, readme):
        assert "/healthz" in text
        assert "DRAFTING_HOSTED" in text
        # the old label may be named as history, but never as what /healthz reports now
        for sentence in re.split(r"(?<=[.;])\s", text):
            assert not ("NOT_IMPLEMENTED" in sentence and "still reports" in sentence), sentence
    assert "NOT_APPLIED" in guide
    assert "BOOTSTRAP_REQUIRED" in guide
    assert "generic rules" in guide and "generic rules" in readme


def test_policy_covers_every_tracked_file() -> None:
    policy = json.loads(read("docs/phase_scope.json"))
    allowed = [policy["guide"], policy["declaration"], *policy["scope_files"]]
    if "changes" in policy:
        allowed.append(policy["changes"])
    for feature in policy["features"].values():
        allowed += feature["paths"]
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode()

    uncovered = [
        path
        for path in tracked.split("\0")
        if path and not any(path == p or (p.endswith("/") and path.startswith(p)) for p in allowed)
    ]
    assert uncovered == []


def test_ci_is_one_read_only_workflow() -> None:
    workflows = sorted((ROOT / ".github" / "workflows").iterdir())
    assert [w.name for w in workflows] == ["ci.yml"]
    text = workflows[0].read_text(encoding="utf-8")

    assert "\npermissions:\n  contents: read\n" in text
    assert "write" not in text
    assert "pull_request_target" not in text
    assert "secrets." not in text
    assert "persist-credentials: false" in text
    assert "--denylist" not in text
    uses = re.findall(r"uses:\s*(\S+)", text)
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses)


def merge_gate_snippet() -> str:
    guide = read("docs/PROJECT_GUIDE.md")
    match = re.search(r"\*\*Merge gate\.\*\*.*?```sh\n(.*?)```", guide, re.S)
    assert match, "the guide must keep a runnable merge-gate snippet"
    return match.group(1)


@pytest.mark.parametrize(
    ("base_checker", "expected"),
    [(None, "fails"), ("", "fails"), ("real", "exit 3")],
)
def test_merge_gate_snippet_runs_the_base_checker_and_fails_closed(
    tmp_path: Path, base_checker: str | None, expected: str
) -> None:
    repo = Repo(tmp_path / "repo")
    repo.write("README.md", "# synthetic\n")
    if base_checker is not None:
        repo.write("scripts/check_repo.py", SCRIPT.read_text() if base_checker else "")
    repo.commit("base", "README.md", *(["scripts"] if base_checker is not None else []))
    repo.git("switch", "-q", "-c", "work")

    result = subprocess.run(
        ["bash", "-c", merge_gate_snippet()],
        cwd=repo.root,
        env=git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    last = result.stdout.strip().splitlines()[-1]

    if expected == "fails":
        assert last.startswith("exit ") and last != "exit 0", result.stdout + result.stderr
    else:
        assert last == expected, result.stdout + result.stderr
        assert "BOOTSTRAP_REQUIRED" in result.stdout
