"""Spike measurement, not a test: the library page from FastAPI (today) and from this Next app, side
by side on one local state directory.

Run from the repository root after `npm ci` in this directory:

    uv run --locked python spikes/next-frontend/smoke.py

It starts the studio in local mode (tests/e2e/serve.py, with its loopback-only guard), creates
three templates through the JSON API and one unreadable one, builds the Next app against it
(rewrite destinations are fixed at build time), starts `next start`, and loads the library from
each in Chromium. Next runs twice: with a per-request nonce (proxy.ts) and with today's policy
unchanged (SPIKE_CSP=classic). It prints what each page rendered, its console errors, and the
script bytes the browser ran.

It exits non-zero if the Next server answers a request naming another host, if the two pages show
different rows (all templates, a search that needs casefolding, a signature-mode filter, sorts
that are not sorts), or if the Next page fails instead of saying so when FastAPI is down.
"""

import gzip
import http.client
import json
import os
import signal
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


def start(command: list[str], servers: list, **options) -> subprocess.Popen:
    """A server in its own process group, recorded before it is waited on, so it always stops."""
    process = subprocess.Popen(command, start_new_session=True, **options)
    servers.append(process)
    return process


def stop(process: subprocess.Popen) -> None:
    """Stops the whole group: npx runs  as a child process."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    process.wait(10)


def next_server(api: str, csp: str, servers: list) -> str:
    port = free_port()
    env = {**os.environ, "STUDIO_API": api, "SPIKE_CSP": csp, "NODE_ENV": "production"}
    command = ["npx", "next", "start", "--port", str(port), "--hostname", "127.0.0.1"]
    process = start(command, servers, cwd=HERE, env=env, stdout=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    wait(f"{base}/", process)
    return base


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


def foreign(base: str) -> dict:
    """What the Next server answers a page and an API request that name another host, as a
    request from another machine would; local mode must refuse both, as FastAPI does."""
    port = int(base.rsplit(":", 1)[1])
    answers = {}
    for path in ("/", "/api/templates"):
        for header, value in (("Host", "192.0.2.10:3000"), ("Origin", "http://192.0.2.10")):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            headers = {"Host": f"127.0.0.1:{port}", header: value}
            connection.request("GET", path, headers=headers)
            answers[f"{path} {header}"] = connection.getresponse().status
            connection.close()
    return answers


def table(browser, url: str) -> list[str]:
    """The library rows a page shows, as text."""
    page = browser.new_page()
    page.goto(url, wait_until="networkidle")
    rows = [" ".join(t.split()) for t in page.locator("table.templates tbody tr").all_inner_texts()]
    page.close()
    return rows


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
    servers: list[subprocess.Popen] = []
    try:
        api_process = start(command, servers, cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
        api = f"http://127.0.0.1:{api_process.stdout.readline().strip()}"
        wait(f"{api}/healthz", api_process)
        source = post(api, "/api/sources", make_pdf(["Subscription agreement"]), "application/pdf")
        titles = [("Fund I subscription", "acct-1"), ("Fund II side letter", "")]
        titles.append(("Straße lease", ""))  # Python casefolds "ß" to "ss"
        for title, account in titles:
            details = {"title": title, "sponsor_name": "Demo", "sponsor_account_id": account}
            body = {"source_sha256": source["sha256"], "policy": POLICY, "details": details}
            post(api, "/api/templates", json.dumps(body).encode(), "application/json")
        unreadable = Path(state) / "templates" / ("f" * 32) / "head.json"
        unreadable.parent.mkdir(parents=True)
        unreadable.write_text("not json")  # listed as Unreadable, with no signature mode
        started = time.perf_counter()
        build_env = {**os.environ, "STUDIO_API": api}
        build = ["npx", "next", "build"]
        subprocess.run(build, cwd=HERE, env=build_env, check=True, capture_output=True)
        results = {"next_build_s": round(time.perf_counter() - started, 1)}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            results["fastapi"] = measure(browser, f"{api}/", "fastapi")
            results["fastapi"]["median_ms"] = timing(f"{api}/")
            bases = {}
            for csp in ("nonce", "classic"):
                base = bases[csp] = next_server(api, csp, servers)
                results[f"next_{csp}"] = measure(browser, f"{base}/", f"next_{csp}")
                results[f"next_{csp}"]["median_ms"] = timing(f"{base}/")
                results[f"next_{csp}"]["foreign_host"] = foreign(base)
            parity = {}
            queries = ["/", "/?q=STRASSE", "/?mode=lp"]
            queries += ["/?sort=__proto__", "/?sort=constructor"]  # not sorts: newest first
            for query in queries:
                ours = table(browser, f"{api}{query}")
                theirs = table(browser, f"{bases['nonce']}{query}")
                parity[query] = {"same": ours == theirs, "fastapi": len(ours), "next": len(theirs)}
            results["parity"] = parity
            stop(api_process)
            page = browser.new_page()
            answer = page.goto(f"{bases['nonce']}/")
            results["api_down"] = {
                "status": answer.status,
                "alert": page.get_by_text("Templates could not be loaded").count() == 1,
            }
            page.close()
            browser.close()
        print(json.dumps(results, indent=2))
        failures = [query for query, check in results["parity"].items() if not check["same"]]
        if failures or not results["api_down"]["alert"]:
            raise SystemExit(f"the pages differ: {failures}, API down: {results['api_down']}")
        refused = [results[f"next_{csp}"]["foreign_host"] for csp in ("nonce", "classic")]
        if any(status != 403 for answers in refused for status in answers.values()):
            raise SystemExit("the Next server answered a request naming another host")
    finally:
        for process in servers:
            stop(process)


if __name__ == "__main__":
    main()
