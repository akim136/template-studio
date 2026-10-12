import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app import app

ROOT = Path(__file__).resolve().parents[1]


def test_healthz_reports_truthful_p0_status() -> None:
    with TestClient(app) as client:  # runs startup and shutdown
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "phase": "P0", "analysis": "NOT_IMPLEMENTED"}


def test_no_other_routes_are_exposed() -> None:
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
