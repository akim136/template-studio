import json
from pathlib import Path

import pytest

from tests.repo_helpers import Repo

POLICY = {
    "active_phase": "P1",
    "guide": "docs/GUIDE.md",
    "declaration": "docs/phase_change.json",
    "scope_files": ["docs/phase_scope.json", "scripts/", ".github/workflows/"],
    "features": {
        "F-alpha": {"paths": ["src/alpha/", "tests/test_alpha.py"]},
        "F-beta": {"paths": ["src/beta.py"]},
    },
}


def declare(
    repo: Repo, kind: str, features: list[str], phase: str = "P1", summary: str = "base"
) -> None:
    declaration = {"phase": phase, "kind": kind, "features": features, "summary": summary}
    repo.write("docs/phase_change.json", json.dumps(declaration))


PREPARED = {"docs/GUIDE.md", "docs/phase_change.json", "docs/phase_scope.json"}


def change(repo: Repo, *paths: str) -> None:
    """Commit paths; the guide, declaration, policy and change notes keep what the test wrote."""
    for path in paths:
        if path not in PREPARED and not path.startswith("docs/changes/"):
            repo.write(path, f"changed {path}\n")
    repo.commit("change", *paths)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    repo = Repo(tmp_path / "repo")
    repo.write("docs/phase_scope.json", json.dumps(POLICY))
    repo.write("docs/GUIDE.md", "# guide\n")
    declare(repo, "scope_update", [])
    for path in ("src/alpha/a.py", "src/beta.py", "scripts/check.py"):
        repo.write(path, "base\n")
    repo.commit("base", "docs", "src", "scripts")
    repo.git("switch", "-q", "-c", "work")
    return repo


def edit_policy(repo: Repo, **changes: object) -> None:
    repo.write("docs/phase_scope.json", json.dumps({**POLICY, **changes}))


def amend_guide_and_declare(repo: Repo, kind: str, features: list[str], **kw: str) -> None:
    declare(repo, kind, features, summary="this change", **kw)
    repo.write("docs/GUIDE.md", "# guide\n\nAmended.\n")


def test_base_without_policy_requires_manual_bootstrap(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "repo")
    repo.write("README.md", "# synthetic\n")
    repo.commit("init", "README.md")
    repo.git("switch", "-q", "-c", "work")
    repo.write("docs/phase_scope.json", json.dumps(POLICY))
    repo.commit("policy", "docs/phase_scope.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 3, result.stdout + result.stderr
    assert "BOOTSTRAP_REQUIRED" in result.stdout
    assert "HEAD proposes active phase P1" in result.stdout


def test_declared_feature_within_its_paths_passes(repo: Repo) -> None:
    amend_guide_and_declare(repo, "feature", ["F-alpha"])
    change(
        repo, "src/alpha/new.py", "tests/test_alpha.py", "docs/GUIDE.md", "docs/phase_change.json"
    )

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "phase check passed: feature in P1" in result.stdout


def test_path_outside_declared_features_fails(repo: Repo) -> None:
    amend_guide_and_declare(repo, "feature", ["F-alpha"])
    change(repo, "src/beta.py", "docs/GUIDE.md", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "src/beta.py is outside the declared features" in result.stdout


def test_unknown_feature_fails(repo: Repo) -> None:
    amend_guide_and_declare(repo, "feature", ["F-gamma"])
    change(repo, "src/alpha/a.py", "docs/GUIDE.md", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "unknown feature 'F-gamma'" in result.stdout


def test_feature_change_cannot_touch_scope_files(repo: Repo) -> None:
    amend_guide_and_declare(repo, "feature", ["F-alpha"])
    change(repo, "scripts/check.py", "docs/GUIDE.md", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "scope file scripts/check.py changes only with kind: scope_update" in result.stdout


def test_feature_branch_cannot_widen_its_own_policy(repo: Repo) -> None:
    edit_policy(repo, features={"F-alpha": {"paths": ["src/"]}})
    amend_guide_and_declare(repo, "feature", ["F-alpha"])
    change(repo, "src/other.py", "docs/GUIDE.md", "docs/phase_change.json")
    repo.commit("widen", "docs/phase_scope.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "src/other.py is outside the declared features" in result.stdout
    assert "scope file docs/phase_scope.json changes only with kind: scope_update" in result.stdout


def test_scope_update_touching_only_scope_files_and_guide_passes(repo: Repo) -> None:
    edit_policy(repo, scope_files=[*POLICY["scope_files"], "AGENTS.md"])
    amend_guide_and_declare(repo, "scope_update", [])
    change(repo, "docs/phase_scope.json", "docs/GUIDE.md", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "phase check passed: scope_update to P1" in result.stdout


def test_scope_update_cannot_bundle_operational_feature_changes(repo: Repo) -> None:
    edit_policy(repo, scope_files=[*POLICY["scope_files"], "AGENTS.md"])
    amend_guide_and_declare(repo, "scope_update", [])
    change(
        repo,
        "docs/phase_scope.json",
        "docs/GUIDE.md",
        "docs/phase_change.json",
        "src/alpha/a.py",
    )

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "scope_update cannot bundle operational change src/alpha/a.py" in result.stdout
    assert "docs/phase_scope.json" not in result.stdout.split("FAILED", 1)[1]


def test_change_must_amend_guide_and_refresh_declaration(repo: Repo) -> None:
    change(repo, "src/alpha/a.py")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "the guide docs/GUIDE.md was not amended" in result.stdout
    assert "the declaration docs/phase_change.json was not refreshed" in result.stdout


def test_declared_phase_must_match_base_policy(repo: Repo) -> None:
    amend_guide_and_declare(repo, "feature", ["F-alpha"], phase="P2")
    change(repo, "src/alpha/a.py", "docs/GUIDE.md", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "declared phase 'P2' does not match active phase 'P1'" in result.stdout


@pytest.mark.parametrize(
    ("base", "policy"),
    [("no-such-ref", None), ("main", "{not json"), ("main", '{"active_phase": "P1"}')],
)
def test_unknown_base_or_malformed_policy_is_a_usage_error(
    tmp_path: Path, base: str, policy: str | None
) -> None:
    repo = Repo(tmp_path / "repo")
    repo.write("README.md", "# synthetic\n")
    paths = ["README.md"]
    if policy is not None:
        repo.write("docs/phase_scope.json", policy)
        paths.append("docs/phase_scope.json")
    repo.commit("init", *paths)

    result = repo.check("phase", "--base", base)

    assert result.returncode == 2
    assert result.stderr.startswith("check_repo: ")


def test_deleting_the_guide_fails(repo: Repo) -> None:
    declare(repo, "feature", ["F-alpha"], summary="this change")
    repo.git("rm", "-q", "docs/GUIDE.md")
    change(repo, "src/alpha/a.py", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "the guide docs/GUIDE.md is missing at HEAD" in result.stdout


def test_missing_declaration_fails(repo: Repo) -> None:
    repo.git("rm", "-q", "docs/phase_change.json")
    repo.write("docs/GUIDE.md", "# guide\n\nAmended.\n")
    change(repo, "src/alpha/a.py", "docs/GUIDE.md")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "the declaration docs/phase_change.json is missing at HEAD" in result.stdout


def test_feature_change_cannot_delete_or_rename_scope_files(repo: Repo) -> None:
    amend_guide_and_declare(repo, "feature", ["F-alpha"])
    repo.git("mv", "scripts/check.py", "src/alpha/check.py")
    change(repo, "docs/GUIDE.md", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "scope file scripts/check.py changes only with kind: scope_update" in result.stdout


def test_kind_must_be_feature_or_scope_update(repo: Repo) -> None:
    amend_guide_and_declare(repo, "bootstrap", ["F-alpha"])
    change(repo, "src/alpha/a.py", "docs/GUIDE.md", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "kind must be 'feature' or 'scope_update', not 'bootstrap'" in result.stdout


def test_scope_update_declares_no_features(repo: Repo) -> None:
    edit_policy(repo, scope_files=[*POLICY["scope_files"], "AGENTS.md"])
    amend_guide_and_declare(repo, "scope_update", ["F-alpha"])
    change(repo, "docs/phase_scope.json", "docs/GUIDE.md", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "a scope_update declares no features" in result.stdout


def test_scope_update_advances_the_phase(repo: Repo) -> None:
    edit_policy(repo, active_phase="P2")
    amend_guide_and_declare(repo, "scope_update", [], phase="P2")
    change(repo, "docs/phase_scope.json", "docs/GUIDE.md", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "phase check passed: scope_update to P2" in result.stdout

    declare(repo, "scope_update", [], phase="P1", summary="stale phase")
    repo.commit("stale", "docs/phase_change.json")
    result = repo.check("phase", "--base", "main")
    assert result.returncode == 1
    assert "declared phase 'P1' does not match active phase 'P2'" in result.stdout


@pytest.mark.parametrize(
    ("policy", "reason"),
    [
        (None, "a scope_update cannot delete docs/phase_scope.json"),
        ("{not json", "HEAD:docs/phase_scope.json is not valid JSON"),
        ('{"active_phase": "P2"}', "HEAD:docs/phase_scope.json is malformed"),
    ],
)
def test_scope_update_must_leave_a_valid_policy(
    repo: Repo, policy: str | None, reason: str
) -> None:
    amend_guide_and_declare(repo, "scope_update", [])
    if policy is None:
        repo.git("rm", "-q", "docs/phase_scope.json")
        repo.commit("change", "docs/GUIDE.md", "docs/phase_change.json")
    else:
        repo.write("docs/phase_scope.json", policy)
        repo.commit("change", "docs/phase_scope.json", "docs/GUIDE.md", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1, result.stdout + result.stderr
    assert reason in result.stdout


NOTE_POLICY = {**POLICY, "changes": "docs/changes/"}


def note(phase: str = "P1", kind: str = "feature", features: str = "F-alpha") -> str:
    return f"---\nphase: {phase}\nkind: {kind}\nfeatures: {features}\n---\n\n# A change\n\nWhy.\n"


@pytest.fixture
def notes_repo(tmp_path: Path) -> Repo:
    """A base whose policy asks for one change note per change instead of the guide edit."""
    repo = Repo(tmp_path / "repo")
    repo.write("docs/phase_scope.json", json.dumps(NOTE_POLICY))
    repo.write("docs/GUIDE.md", "# guide\n")
    declare(repo, "scope_update", [])
    repo.write("docs/changes/2026-10-01-start.md", note(kind="scope_update", features=""))
    for path in ("src/alpha/a.py", "src/beta.py", "scripts/check.py"):
        repo.write(path, "base\n")
    repo.commit("base", "docs", "src", "scripts")
    repo.git("switch", "-q", "-c", "work")
    return repo


def add_note(repo: Repo, name: str = "2026-10-15-alpha-fix.md", text: str | None = None) -> str:
    path = f"docs/changes/{name}"
    repo.write(path, note() if text is None else text)
    return path


def test_note_alone_declares_a_feature_without_guide_or_declaration(notes_repo: Repo) -> None:
    change(notes_repo, "src/alpha/a.py", add_note(notes_repo))

    result = notes_repo.check("phase", "--base", "main")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "phase check passed: feature in P1" in result.stdout


def test_note_mode_lets_a_feature_change_still_edit_the_guide(notes_repo: Repo) -> None:
    notes_repo.write("docs/GUIDE.md", "# guide\n\nCurrent state.\n")
    change(notes_repo, "src/alpha/a.py", "docs/GUIDE.md", add_note(notes_repo))

    result = notes_repo.check("phase", "--base", "main")

    assert result.returncode == 0, result.stdout + result.stderr


def test_note_mode_still_checks_paths_against_the_note(notes_repo: Repo) -> None:
    change(notes_repo, "src/beta.py", add_note(notes_repo))

    result = notes_repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "src/beta.py is outside the declared features" in result.stdout


@pytest.mark.parametrize("count", [0, 2])
def test_note_mode_needs_exactly_one_new_note(notes_repo: Repo, count: int) -> None:
    notes = [add_note(notes_repo, f"2026-10-15-change-{n}.md") for n in range(count)]
    change(notes_repo, "src/alpha/a.py", *notes)

    result = notes_repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert f"add exactly one change note under docs/changes/ (found {count})" in result.stdout


def test_note_mode_never_lets_a_change_edit_an_earlier_note(notes_repo: Repo) -> None:
    notes_repo.write(
        "docs/changes/2026-10-01-start.md", note(kind="scope_update", features="") + "More.\n"
    )
    change(notes_repo, "src/alpha/a.py", "docs/changes/2026-10-01-start.md", add_note(notes_repo))

    result = notes_repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "docs/changes/2026-10-01-start.md exists at the base and cannot change" in result.stdout


@pytest.mark.parametrize(
    ("name", "text", "finding"),
    [
        ("alpha.md", None, "must be named YYYY-MM-DD-short-name.md"),
        ("2026-10-15-Alpha_Fix.md", None, "must be named YYYY-MM-DD-short-name.md"),
        (
            "2026-10-15-alpha.md",
            "# no header\n",
            "must start with a header between two '---' lines",
        ),
        ("2026-10-15-alpha.md", "---\nphase: P1\nfeatures: F-alpha\n---\nWhy.\n", "no 'kind' line"),
        (
            "2026-10-15-alpha.md",
            "---\nphase: P1\nkind: feature\nfeatures: F-alpha\nowner: x\n---\nWhy.\n",
            "unexpected header line 'owner: x'",
        ),
        (
            "2026-10-15-alpha.md",
            "---\nphase: P1\nkind: feature\nfeatures: F-alpha\n---\n\n",
            "no text after its header",
        ),
    ],
)
def test_note_mode_rejects_a_malformed_note(
    notes_repo: Repo, name: str, text: str | None, finding: str
) -> None:
    change(notes_repo, "src/alpha/a.py", add_note(notes_repo, name, text))

    result = notes_repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert finding in result.stdout


def test_note_mode_checks_the_declared_phase(notes_repo: Repo) -> None:
    change(notes_repo, "src/alpha/a.py", add_note(notes_repo, text=note(phase="P2")))

    result = notes_repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "declared phase 'P2' does not match active phase 'P1'" in result.stdout


def test_note_mode_scope_update_touches_only_scope_files(notes_repo: Repo) -> None:
    notes_repo.write("docs/phase_scope.json", json.dumps({**NOTE_POLICY, "active_phase": "P2"}))
    scope_note = add_note(notes_repo, text=note(phase="P2", kind="scope_update", features=""))
    change(notes_repo, "docs/phase_scope.json", "docs/GUIDE.md", scope_note)
    assert notes_repo.check("phase", "--base", "main").returncode == 0

    change(notes_repo, "src/alpha/a.py")
    result = notes_repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "scope_update cannot bundle operational change src/alpha/a.py" in result.stdout


def test_a_legacy_base_keeps_the_guide_rule_while_a_scope_update_turns_on_notes(repo: Repo) -> None:
    edit_policy(repo, changes="docs/changes/")
    amend_guide_and_declare(repo, "scope_update", [])
    change(repo, "docs/phase_scope.json", "docs/GUIDE.md", "docs/phase_change.json")
    assert repo.check("phase", "--base", "main").returncode == 0
    repo.git("switch", "-q", "main")
    repo.git("merge", "-q", "--ff-only", "work")
    repo.git("switch", "-q", "-c", "next")

    change(repo, "src/alpha/a.py", add_note(repo))
    result = repo.check("phase", "--base", "main")

    assert result.returncode == 0, result.stdout + result.stderr


def test_policy_changes_entry_must_name_a_directory(notes_repo: Repo) -> None:
    notes_repo.write(
        "docs/phase_scope.json", json.dumps({**NOTE_POLICY, "changes": "docs/changes"})
    )
    change(
        notes_repo,
        "docs/phase_scope.json",
        add_note(notes_repo, text=note(kind="scope_update", features="")),
    )

    result = notes_repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "HEAD:docs/phase_scope.json is malformed" in result.stdout
