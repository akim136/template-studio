"""Browser tests: a real Chromium (Playwright) against the studio served by uvicorn in local mode.

They run only with STUDIO_E2E=1 (CI's browser job sets it), because they need a browser binary
(`uv run --locked playwright install chromium`). Each test gets its own server and an empty state
directory, so tests never see each other's templates."""

import os
import re
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.e2e.guard import install

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
    install(monkeypatch.setattr)


def studio_env(tmp_path: Path) -> dict[str, str]:
    """An environment for a studio process: local mode on an empty state directory, with no hosted
    setting and none of the developer's own state."""
    env = {name: os.environ[name] for name in ("PATH", "HOME", "LANG") if name in os.environ}
    env["STUDIO_STATE_DIR"] = str(tmp_path / "state")
    return env


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    """The studio in local mode on a loopback port, with no hosted setting in its environment and
    the loopback-only guard installed in its process. The server picks and holds its own port."""
    env = studio_env(tmp_path)
    command = [sys.executable, "-m", "tests.e2e.serve"]
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
    try:
        port = process.stdout.readline().strip()
        if not port.isdigit():
            raise RuntimeError("the studio did not report its port")
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20
        while True:
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("the studio did not start")
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        yield base
    finally:
        process.terminate()
        process.wait(timeout=10)
        process.stdout.close()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def errors() -> list[str]:
    """What went wrong in the page; the page fixture fails the test unless it ends empty."""
    return []


@pytest.fixture
def page(browser, server: str, errors: list[str]):
    """A fresh browser context on the server. Motion is reduced, so the create dialog skips its
    pacing delays. Requests to any other origin are refused. A console error, an uncaught
    exception, an unexpected error answer or a failed request fails the test; Chrome's own
    console line for a failed load is left to the answer and request checks. A request cancelled
    by navigation (net::ERR_ABORTED) is not a failure."""
    context = browser.new_context(base_url=server, reduced_motion="reduce")
    origin = urlsplit(server)[:2]

    def only_the_studio(route) -> None:
        if urlsplit(route.request.url)[:2] == origin:
            route.continue_()
        else:
            route.abort("blockedbyclient")

    context.route("**/*", only_the_studio)
    page = context.new_page()

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

    def failed(request) -> None:
        if request.failure != "net::ERR_ABORTED":
            errors.append(f"{request.failure} {request.method} {request.url}")

    page.on("console", console)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("response", response)
    page.on("requestfailed", failed)
    yield page
    context.close()
    assert errors == []
