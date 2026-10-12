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


PREPARED = {"docs/GUIDE.md", "docs/phase_change.json"}


def change(repo: Repo, *paths: str) -> None:
    """Commit paths; the guide and declaration keep what amend_guide_and_declare wrote."""
    for path in paths:
        if path not in PREPARED:
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
    widened = {**POLICY, "features": {"F-alpha": {"paths": ["src/"]}}}
    repo.write("docs/phase_scope.json", json.dumps(widened))
    amend_guide_and_declare(repo, "feature", ["F-alpha"])
    change(repo, "src/other.py", "docs/GUIDE.md", "docs/phase_change.json")
    repo.commit("widen", "docs/phase_scope.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 1
    assert "src/other.py is outside the declared features" in result.stdout
    assert "scope file docs/phase_scope.json changes only with kind: scope_update" in result.stdout


def test_scope_update_touching_only_scope_files_and_guide_passes(repo: Repo) -> None:
    amend_guide_and_declare(repo, "scope_update", [])
    change(repo, "docs/phase_scope.json", "docs/GUIDE.md", "docs/phase_change.json")

    result = repo.check("phase", "--base", "main")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "phase check passed: scope_update in P1" in result.stdout


def test_scope_update_cannot_bundle_operational_feature_changes(repo: Repo) -> None:
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
