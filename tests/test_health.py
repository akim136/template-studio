import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app import app
from studio.state.api import router as state_router

ROOT = Path(__file__).resolve().parents[1]


def test_healthz_reports_truthful_status() -> None:
    with TestClient(app) as client:  # runs startup and shutdown
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "analysis": "NOT_IMPLEMENTED"}


STATE_ROUTES = {
    ("POST", "/api/sources"),
    ("POST", "/api/templates"),
    ("GET", "/api/templates/{template_id}"),
    ("PUT", "/api/templates/{template_id}"),
    ("POST", "/api/templates/{template_id}/review"),
    ("POST", "/api/templates/{template_id}/approval"),
    ("POST", "/api/templates/{template_id}/operation"),
    ("GET", "/api/templates/{template_id}/operation"),
    ("POST", "/api/templates/{template_id}/operation/status-check"),
}


def exposed_routes() -> set[tuple[str, str]]:
    exposed = set()
    for route in app.routes:
        if hasattr(route, "path"):
            exposed |= {(method, route.path) for method in route.methods}
        else:  # FastAPI 0.143 includes a router lazily, as one route with no path of its own
            assert route.original_router is state_router
            exposed |= {(m, r.path) for r in state_router.routes for m in r.methods}
    return exposed


def test_no_other_routes_are_exposed() -> None:
    assert exposed_routes() == STATE_ROUTES | {("GET", "/healthz")}
    client = TestClient(app)
    for path in ("/", "/docs", "/redoc", "/openapi.json", "/api/analyze"):
        assert client.get(path).status_code == 404, path
    assert client.post("/healthz").status_code == 405


def test_app_starts_without_credentials_or_environment() -> None:
    script = (
        "import socket\n"
        "def blocked(*a, **k):\n"
        "    raise SystemExit('outbound network attempted')\n"
        "socket.create_connection = blocked\n"
        "socket.socket.connect = blocked\n"
        "from fastapi.testclient import TestClient\n"
        "from app import app\n"
        "with TestClient(app) as c:\n"
        "    r = c.get('/healthz')\n"
        "assert r.status_code == 200 and r.json()['analysis'] == 'NOT_IMPLEMENTED'\n"
        "print('started')\n"
    )
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "started"
