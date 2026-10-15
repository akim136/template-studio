"""The browser tests' own guards: only the studio is reachable, and a failed request fails the
test."""

import socket
import subprocess
import sys
import time

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from tests.e2e.conftest import ROOT, read_port, stop, studio_env
from tests.e2e.test_journey import create_template, follow

# Runs the server module's entry point with the server replaced by an outbound connection attempt.
PROBE = """
import runpy, socket, types
import uvicorn
reach = lambda sockets: socket.create_connection(("192.0.2.1", 9), timeout=1)
uvicorn.Server = lambda config: types.SimpleNamespace(run=reach)
runpy.run_module("tests.e2e.serve", run_name="__main__")
"""


def test_the_browser_reaches_only_the_studio(page: Page, errors: list[str]) -> None:
    # The pages' content security policy already stops fetches elsewhere; a navigation (such as
    # the link to the signing provider) is not covered by it.
    page.goto("/")
    with pytest.raises(PlaywrightError, match="ERR_BLOCKED_BY_CLIENT"):
        page.goto("https://example.invalid/")
    assert errors == ["net::ERR_BLOCKED_BY_CLIENT GET https://example.invalid/"]
    errors.clear()


def test_a_page_image_that_fails_to_load_is_reported(page: Page, errors: list[str]) -> None:
    create_template(page, pages=1)
    page.route("**/pages/*.png", lambda route: route.abort("connectionreset"))
    follow(page, "Edit fields", "**/edit")
    expect(page.locator("[data-sheet-note]")).to_contain_text("could not be shown")
    assert [error.split(" ")[0] for error in errors] == ["net::ERR_CONNECTION_RESET"]
    errors.clear()


def test_the_server_process_refuses_outbound_connections(tmp_path, monkeypatch) -> None:
    inherited = tmp_path / "inherited"
    monkeypatch.setenv("STUDIO_STATE_DIR", str(inherited))
    command = [sys.executable, "-c", PROBE]
    result = subprocess.run(
        command, cwd=ROOT, env=studio_env(tmp_path), capture_output=True, text=True, timeout=60
    )
    assert result.returncode != 0
    assert "outbound network access attempted" in result.stderr
    assert not inherited.exists()  # the developer's own state is never opened


def test_the_server_holds_the_port_it_reports(tmp_path, monkeypatch) -> None:
    inherited = tmp_path / "inherited"
    monkeypatch.setenv("STUDIO_STATE_DIR", str(inherited))
    command = [sys.executable, "-m", "tests.e2e.serve"]
    process = subprocess.Popen(
        command, cwd=ROOT, env=studio_env(tmp_path), stdout=subprocess.PIPE, text=True
    )
    try:
        port = int(read_port(process, time.monotonic() + 20))
        with socket.socket() as other, pytest.raises(OSError):
            other.bind(("127.0.0.1", port))
        assert not inherited.exists()
    finally:
        stop(process)


def test_a_studio_that_never_reports_its_port_fails_in_time() -> None:
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    silent = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="did not report its port"):
            read_port(silent, started + 0.5)
        assert time.monotonic() - started < 5
    finally:
        stop(silent)
