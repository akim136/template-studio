"""Browser tests: a real Chromium (Playwright) against the studio served by uvicorn in local mode.

They run only with STUDIO_E2E=1 (CI's browser job sets it), because they need a browser binary
(`uv run --locked playwright install chromium`). Each test gets its own server and an empty state
directory, so tests never see each other's templates."""

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Answers the pages expect: the editor reads "no operation yet" from this 404 (static/editor.js).
EXPECTED = [("GET", re.compile(r"/api/templates/[0-9a-f]{32}/operation"), 404)]
ENABLED = os.environ.get("STUDIO_E2E") == "1"


def pytest_collection_modifyitems(config, items) -> None:
    skip = pytest.mark.skip(reason="browser tests run with STUDIO_E2E=1")
    for item in items:
        if not ENABLED and Path(item.path).is_relative_to(Path(__file__).parent):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaces the suite's guard: these tests reach their own server on loopback, and any other
    address still fails the test."""

    def loopback_only(real, at: int):
        def guarded(*args: object, **kwargs: object) -> object:
            target = args[at]
            host = target[0] if isinstance(target, tuple) else target
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise AssertionError("outbound network access attempted during a test")
            return real(*args, **kwargs)

        return guarded

    monkeypatch.setattr(socket, "create_connection", loopback_only(socket.create_connection, 0))
    monkeypatch.setattr(socket.socket, "connect", loopback_only(socket.socket.connect, 1))
    monkeypatch.setattr(socket.socket, "connect_ex", loopback_only(socket.socket.connect_ex, 1))


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    """The studio in local mode on a loopback port, with no hosted setting in its environment."""
    port = _free_port()
    env = {name: os.environ[name] for name in ("PATH", "HOME", "LANG") if name in os.environ}
    env["STUDIO_STATE_DIR"] = str(tmp_path / "state")
    command = [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1"]
    command += ["--port", str(port), "--log-level", "warning"]
    process = subprocess.Popen(command, cwd=ROOT, env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("the studio did not start") from None
                time.sleep(0.1)
        yield base
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser, server: str):
    """A fresh browser context on the server. Motion is reduced, so the create dialog skips its
    pacing delays. A console error, an uncaught exception or an unexpected error answer fails
    the test; Chrome's own console line for an error answer is left to the answer check."""
    context = browser.new_context(base_url=server, reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []

    def console(message) -> None:
        if message.type == "error" and not message.text.startswith("Failed to load resource"):
            errors.append(message.text)

    def response(answer) -> None:
        method, path = answer.request.method, urlsplit(answer.url).path
        expected = any(
            method == m and pattern.fullmatch(path) and answer.status == status
            for m, pattern, status in EXPECTED
        )
        if answer.status >= 400 and not expected:
            errors.append(f"{answer.status} {method} {path}")

    page.on("console", console)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("response", response)
    yield page
    context.close()
    assert errors == []
