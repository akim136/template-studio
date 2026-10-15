"""The app, phase policy and docs must tell the same story in every phase."""

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

import app
from tests.repo_helpers import SCRIPT, Repo, git_env

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_repo", SCRIPT)
CHECKER = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CHECKER)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_phase_contract(policy: dict, declaration: dict) -> None:
    assert policy["active_phase"] == declaration["phase"]
    assert set(declaration["features"]) <= set(policy["features"])


def current_declaration(root: Path, policy: dict) -> dict:
    """The newest change note's header, read with the checker's own parser, when the policy
    names a changes directory; otherwise the old declaration file. Notes sort by their dated
    names, so the newest is the last."""
    if "changes" not in policy:
        return json.loads((root / policy["declaration"]).read_text(encoding="utf-8"))
    notes = sorted((root / policy["changes"]).glob("*.md"))
    assert notes, f"no change note under {policy['changes']}"
    declaration, problems = CHECKER.parse_note(notes[-1].read_text(encoding="utf-8"))
    assert problems == [], (notes[-1].name, problems)
    return declaration


def test_policy_declaration_and_app_agree() -> None:
    policy = json.loads(read("docs/phase_scope.json"))
    assert_phase_contract(policy, current_declaration(ROOT, policy))
    # The phase lives only in the policy, so a scope_update can advance it without app changes.
    assert not hasattr(app, "PHASE")
    assert app.ANALYSIS_STATUS == "DRAFTING_HOSTED"


def test_contract_allows_a_scope_update_to_advance_the_phase() -> None:
    policy = {**json.loads(read("docs/phase_scope.json")), "active_phase": "P1"}
    assert_phase_contract(policy, {"phase": "P1", "kind": "scope_update", "features": []})


def test_the_newest_change_note_declares_the_phase_without_the_old_declaration(
    tmp_path: Path,
) -> None:
    policy = {
        **json.loads(read("docs/phase_scope.json")),
        "active_phase": "P5",
        "changes": "notes/",
    }
    (tmp_path / "notes").mkdir()
    for name, phase, kind, features in (
        ("2026-10-15-docs.md", "P4", "feature", "F-P4d-docs"),
        ("2026-10-16-advance.md", "P5", "scope_update", ""),
    ):
        header = f"---\nphase: {phase}\nkind: {kind}\nfeatures: {features}\n---\n\nWhy.\n"
        (tmp_path / "notes" / name).write_text(header, encoding="utf-8")

    declaration = current_declaration(tmp_path, policy)

    assert declaration == {"phase": "P5", "kind": "scope_update", "features": []}
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
