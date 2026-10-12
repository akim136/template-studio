from pathlib import Path

import pytest

from tests.repo_helpers import Repo

SYNTHETIC_TOKENS = "# synthetic test tokens\nreviewer.one@example.test\nProject Bluebird\nKestrel\n"


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    repo = Repo(tmp_path / "repo")
    repo.write("README.md", "# synthetic\n")
    repo.commit("init", "README.md")
    return repo


@pytest.fixture
def denylist(tmp_path: Path) -> str:
    path = tmp_path / "denylist.txt"
    path.write_text(SYNTHETIC_TOKENS, encoding="utf-8")
    return str(path)


def test_clean_repo_passes_in_both_modes(repo: Repo, denylist: str) -> None:
    for mode in ("--staged", "--all-history"):
        result = repo.check("private", mode, "--denylist", denylist)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "3 denylist token(s)" in result.stdout


def test_without_a_denylist_only_generic_rules_run(repo: Repo) -> None:
    result = repo.check("private", "--staged")
    assert result.returncode == 0
    assert "generic rules only" in result.stdout


@pytest.mark.parametrize(
    ("path", "data", "reason"),
    [
        ("docs/source.pdf", b"text", "blocked extension .pdf"),
        ("handoff/bundle.zip", b"text", "blocked extension .zip"),
        ("assets/font.woff2", b"text", "blocked extension .woff2"),
        ("notes/scan.txt", b"%PDF-1.7\n", "PDF content"),
        ("notes/bundle.bin", b"PK\x03\x04rest", "ZIP content"),
        ("private/list.csv", b"a,b\n", "private or answer-bearing directory"),
        ("evaluator/answers.json", b"{}", "private or answer-bearing directory"),
        ("baseline/dev-a/fields.json", b"{}", "private or answer-bearing directory"),
        ("cases/a/overlays/page_01.png", b"img", "private or answer-bearing directory"),
        ("cases/a/scripts/build_fields_A.py", b"pass\n", "private or answer-bearing file name"),
        ("ACCESS_ROSTER.csv", b"name\n", "private or answer-bearing file name"),
        ("access-roster.csv", b"name\n", "private or answer-bearing file name"),
        ("cases/build-fields-B.py", b"pass\n", "private or answer-bearing file name"),
        ("Template_Baseline_20260101.txt", b"x\n", "private or answer-bearing file name"),
        (".env", b"KEY=value\n", "private or answer-bearing file name"),
        ("config/.env.local", b"KEY=value\n", "private or answer-bearing file name"),
    ],
)
def test_generic_rules_block_staged_files(repo: Repo, path: str, data: bytes, reason: str) -> None:
    repo.write(path, data)
    repo.git("add", "-f", "--", path)
    result = repo.check("private", "--staged")
    assert result.returncode == 1
    assert reason in result.stdout


def test_similar_ordinary_names_pass(repo: Repo) -> None:
    paths = [
        "docs/privacy.md",
        "studio/overlay_geometry.py",
        "docs/environment.md",
        "docs/baseline_notes.md",
    ]
    for path in paths:
        repo.write(path, "Mentions %PDF- and PK markers mid-file only.\n")
    repo.git("add", "--", *paths)
    result = repo.check("private", "--staged")
    assert result.returncode == 0, result.stdout


def test_scan_from_a_subdirectory_covers_the_whole_repository(repo: Repo) -> None:
    repo.write("private/list.csv", "a,b\n")
    repo.write("docs/readme.md", "ordinary\n")
    repo.commit("add", "private/list.csv", "docs/readme.md")
    for mode in ("--staged", "--all-history"):
        result = repo.check("--repo", str(repo.root / "docs"), "private", mode)
        assert result.returncode == 1, mode
        assert "private/list.csv" in result.stdout


def test_oversized_blob_is_blocked(repo: Repo) -> None:
    repo.write("data/big.txt", b"a" * (1024 * 1024 + 1))
    repo.git("add", "--", "data/big.txt")
    result = repo.check("private", "--staged")
    assert result.returncode == 1
    assert "larger than 1048576 bytes" in result.stdout


def test_denylisted_tokens_are_found_and_redacted(repo: Repo, denylist: str) -> None:
    repo.write("docs/notes.md", "Ask REVIEWER.ONE@example.test about project\n  bluebird.\n")
    repo.git("add", "--", "docs/notes.md")
    result = repo.check("private", "--staged", "--denylist", denylist)
    assert result.returncode == 1
    assert "content matches 2 denylisted token(s)" in result.stdout
    output = (result.stdout + result.stderr).lower()
    assert "reviewer.one" not in output
    assert "bluebird" not in output


def test_denylist_matches_whole_tokens_only(repo: Repo, denylist: str) -> None:
    repo.write("docs/birds.md", "Kestrelton and kestrels are ordinary words.\n")
    repo.git("add", "--", "docs/birds.md")
    assert repo.check("private", "--staged", "--denylist", denylist).returncode == 0

    repo.write("docs/birds.md", "A KESTREL appears.\n")
    repo.git("add", "--", "docs/birds.md")
    assert repo.check("private", "--staged", "--denylist", denylist).returncode == 1


def test_denylist_treats_underscores_and_punctuation_as_boundaries(
    repo: Repo, tmp_path: Path
) -> None:
    tokens = tmp_path / "tokens.txt"
    tokens.write_text("Kestrel\n@corp.example\n", encoding="utf-8")
    for text in ("KESTREL_FUND = 1\n", "build_fields_kestrel\n", "mail jo@corp.example\n"):
        repo.write("docs/notes.md", text)
        repo.git("add", "--", "docs/notes.md")
        result = repo.check("private", "--staged", "--denylist", str(tokens))
        assert result.returncode == 1, text


def test_denylisted_path_is_redacted(repo: Repo, denylist: str) -> None:
    repo.write("docs/kestrel-notes.md", "ordinary\n")
    repo.git("add", "--", "docs/kestrel-notes.md")
    result = repo.check("private", "--staged", "--denylist", denylist)
    assert result.returncode == 1
    assert "path matches a denylisted token" in result.stdout
    assert "[REDACTED]" in result.stdout
    assert "kestrel" not in result.stdout.lower()


def test_history_scan_finds_a_file_removed_later(repo: Repo) -> None:
    repo.write("private/roster.csv", "name\n")
    repo.commit("add", "private/roster.csv")
    repo.git("rm", "-q", "--", "private/roster.csv")
    repo.commit("remove")
    assert repo.check("private", "--staged").returncode == 0
    result = repo.check("private", "--all-history")
    assert result.returncode == 1
    assert "private/roster.csv" in result.stdout


def test_history_scan_checks_commit_messages(repo: Repo, denylist: str) -> None:
    repo.commit("notes about Project Bluebird")
    result = repo.check("private", "--all-history", "--denylist", denylist)
    assert result.returncode == 1
    assert "metadata or message matches 1 token(s)" in result.stdout
    assert "bluebird" not in result.stdout.lower()


def test_bad_denylists_are_usage_errors(repo: Repo, tmp_path: Path) -> None:
    short = tmp_path / "short.txt"
    short.write_text("abc\n", encoding="utf-8")
    result = repo.check("private", "--staged", "--denylist", str(short))
    assert result.returncode == 2
    assert "at least 4 characters" in result.stderr

    inside = repo.root / "denylist.txt"
    inside.write_text("Kestrel\n", encoding="utf-8")
    result = repo.check("private", "--staged", "--denylist", str(inside))
    assert result.returncode == 2
    assert "outside the repository" in result.stderr

    comments = tmp_path / "comments.txt"
    comments.write_text("# no tokens yet\n\n", encoding="utf-8")
    result = repo.check("private", "--staged", "--denylist", str(comments))
    assert result.returncode == 2
    assert "no tokens" in result.stderr

    result = repo.check("private", "--staged", "--denylist", "")
    assert result.returncode == 2
