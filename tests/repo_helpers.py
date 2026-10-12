"""Throwaway Git repositories for checker tests. All values are synthetic."""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_repo.py"


def git_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "author@example.test",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "author@example.test",
    }


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            env=git_env(),
            check=True,
        )
        return result.stdout

    def write(self, path: str, data: str | bytes) -> None:
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            file.write_bytes(data)
        else:
            file.write_text(data, encoding="utf-8")

    def commit(self, message: str, *paths: str) -> None:
        if paths:
            self.git("add", "-f", "--", *paths)
        self.git("commit", "-q", "--no-verify", "--allow-empty", "-m", message)

    def check(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.root), *args],
            capture_output=True,
            text=True,
            env=git_env(),
            check=False,
        )
