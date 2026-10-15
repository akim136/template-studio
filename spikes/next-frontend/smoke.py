"""Spike measurement, not a test: the library page from FastAPI (today) and from this Next app, side
by side on one local state directory.

Run from the repository root after `npm ci` in this directory:

    uv run --locked python spikes/next-frontend/smoke.py

It starts the studio in local mode (tests/e2e/serve.py, with its loopback-only guard), creates two
templates through the JSON API, builds the Next app against it (rewrite destinations are fixed
at build time), starts `next start`, and loads the library from each in
Chromium. Next runs twice: with a per-request nonce (proxy.ts) and with today's policy unchanged
(SPIKE_CSP=classic). It prints what each page rendered, its console errors, and the script bytes
the browser ran.
"""

import gzip
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from tests.analysis.pdf_factory import make_pdf  # noqa: E402
from tests.state.helpers import POLICY  # noqa: E402


def wait(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{url} exited")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"{url} did not start")


def post(base: str, path: str, body: bytes, kind: str) -> dict:
    request = urllib.request.Request(f"{base}{path}", body, {"content-type": kind}, method="POST")
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def next_server(api: str, csp: str) -> tuple[subprocess.Popen, str]:
    port = free_port()
    env = {**os.environ, "STUDIO_API": api, "SPIKE_CSP": csp, "NODE_ENV": "production"}
    command = ["npx", "next", "start", "--port", str(port), "--hostname", "127.0.0.1"]
    process = subprocess.Popen(command, cwd=HERE, env=env, stdout=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    wait(f"{base}/", process)
    return process, base


def measure(browser, url: str, name: str) -> dict:
    page = browser.new_page()
    errors, scripts = [], {}
    page.on("console", lambda m: m.type == "error" and errors.append(m.text[:120]))
    page.on("pageerror", lambda e: errors.append(str(e)[:120]))

    def keep(response) -> None:
        if response.request.resource_type == "script":
            scripts[response.url] = response.body()

    page.on("response", keep)
    page.goto(url, wait_until="networkidle")
    inline = page.evaluate(
        "[...document.scripts].filter(s => !s.src).map(s => s.textContent).join('')"
    )
    rows = page.locator("table.templates tbody tr").count()
    if os.environ.get("SPIKE_SHOTS"):  # a directory for screenshots of each page
        page.screenshot(path=Path(os.environ["SPIKE_SHOTS"]) / f"{name}.png", full_page=True)
    page.select_option("select[name=mode]", "lp")  # submits only if the page's script runs
    try:
        page.wait_for_url("**mode=lp**", timeout=3000)
        interactive = True
    except PlaywrightTimeout:
        interactive = False
    page.close()
    external = b"".join(scripts.values())
    return {
        "rows": rows,
        "errors": errors,
        "script_files": len(scripts),
        "script_bytes": len(external),
        "script_gzip": len(gzip.compress(external)),
        "inline_script_bytes": len(inline.encode()),
        "filter_submits_on_change": interactive,
    }


def timing(url: str, n: int = 30) -> float:
    took = []
    for _ in range(n):
        start = time.perf_counter()
        with urllib.request.urlopen(url) as response:
            response.read()
        took.append(time.perf_counter() - start)
    return round(sorted(took)[n // 2] * 1000, 1)


def main() -> None:
    state = tempfile.mkdtemp(prefix="studio-next-spike-")
    env = {name: os.environ[name] for name in ("PATH", "HOME", "LANG") if name in os.environ}
    env["STUDIO_STATE_DIR"] = state
    command = [sys.executable, "-m", "tests.e2e.serve"]
    api_process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
    servers = [api_process]
    try:
        api = f"http://127.0.0.1:{api_process.stdout.readline().strip()}"
        wait(f"{api}/healthz", api_process)
        source = post(api, "/api/sources", make_pdf(["Subscription agreement"]), "application/pdf")
        for title, account in (("Fund I subscription", "acct-1"), ("Fund II side letter", "")):
            details = {"title": title, "sponsor_name": "Demo", "sponsor_account_id": account}
            body = {"source_sha256": source["sha256"], "policy": POLICY, "details": details}
            post(api, "/api/templates", json.dumps(body).encode(), "application/json")
        started = time.perf_counter()
        build_env = {**os.environ, "STUDIO_API": api}
        build = ["npx", "next", "build"]
        subprocess.run(build, cwd=HERE, env=build_env, check=True, capture_output=True)
        results = {"next_build_s": round(time.perf_counter() - started, 1)}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            results["fastapi"] = measure(browser, f"{api}/", "fastapi")
            results["fastapi"]["median_ms"] = timing(f"{api}/")
            for csp in ("nonce", "classic"):
                process, base = next_server(api, csp)
                servers.append(process)
                results[f"next_{csp}"] = measure(browser, f"{base}/", f"next_{csp}")
                results[f"next_{csp}"]["median_ms"] = timing(f"{base}/")
            browser.close()
        print(json.dumps(results, indent=2))
    finally:
        for process in servers:
            process.terminate()
            process.wait(10)


if __name__ == "__main__":
    main()
