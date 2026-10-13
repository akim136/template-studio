"""The app, phase policy and docs must tell the same story in every phase."""

import json
import re
import subprocess
from pathlib import Path

import pytest

import app
from tests.repo_helpers import SCRIPT, Repo, git_env

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_phase_contract(policy: dict, declaration: dict) -> None:
    assert policy["active_phase"] == declaration["phase"]
    assert set(declaration["features"]) <= set(policy["features"])


def test_policy_declaration_and_app_agree() -> None:
    policy = json.loads(read("docs/phase_scope.json"))
    assert_phase_contract(policy, json.loads(read(policy["declaration"])))
    # The phase lives only in the policy, so a scope_update can advance it without app changes.
    assert not hasattr(app, "PHASE")
    assert app.ANALYSIS_STATUS == "DRAFTING_HOSTED"


def test_contract_allows_a_scope_update_to_advance_the_phase() -> None:
    policy = {**json.loads(read("docs/phase_scope.json")), "active_phase": "P1"}
    assert_phase_contract(policy, {"phase": "P1", "kind": "scope_update", "features": []})


def test_docs_state_truthful_status() -> None:
    guide = read("docs/PROJECT_GUIDE.md")
    readme = read("README.md")

    for text in (guide, readme):
        assert "/healthz" in text
        assert "DRAFTING_HOSTED" in text
    assert "NOT_APPLIED" in guide
    assert "BOOTSTRAP_REQUIRED" in guide
    assert "generic rules" in guide and "generic rules" in readme


def test_policy_covers_every_tracked_file() -> None:
    policy = json.loads(read("docs/phase_scope.json"))
    allowed = [policy["guide"], policy["declaration"], *policy["scope_files"]]
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
