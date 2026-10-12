"""The app, phase policy and docs must tell the same P0 story."""

import json
import subprocess
from pathlib import Path

import app

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_app_policy_and_declaration_agree_on_p0() -> None:
    policy = json.loads(read("docs/phase_scope.json"))
    declaration = json.loads(read(policy["declaration"]))

    assert app.PHASE == policy["active_phase"] == declaration["phase"] == "P0"
    assert app.ANALYSIS_STATUS == "NOT_IMPLEMENTED"
    assert set(declaration["features"]) <= set(policy["features"])


def test_docs_state_truthful_status() -> None:
    guide = read("docs/PROJECT_GUIDE.md")
    readme = read("README.md")

    for text in (guide, readme):
        assert "/healthz" in text
        assert "NOT_IMPLEMENTED" in text
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
