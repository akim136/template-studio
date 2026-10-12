"""Homebase Template Studio entrypoint.

The liveness route reports what is actually implemented, so no reviewer or client infers an
analysis capability that is absent. The active phase lives in docs/phase_scope.json, not here.

The state routes under /api answer 503 unless saved state is configured: a local directory outside
the repository (STUDIO_STATE_DIR), or, on Vercel, R2 with Sign in with Vercel. /healthz reports
which, and names any hosted setting that is missing or malformed (see studio/state/api.py).

The pages (library, setup, field drafting, a template's record) are rendered from templates/ with
the scripts and styles in static/; they read and show only saved state (see studio/ui/pages.py).
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from studio.state.api import StateGate, auth_router, configure, report
from studio.state.api import router as state_router
from studio.ui.pages import ROOT, pages

ANALYSIS_STATUS = "NOT_IMPLEMENTED"

app = FastAPI(
    title="Homebase Template Studio",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.configuration = configure(os.environ, Path(__file__).resolve().parent)
report(app.state.configuration)
app.state.services = app.state.configuration.services
app.include_router(state_router)
app.include_router(auth_router)
app.include_router(pages)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.add_middleware(StateGate)


@app.get("/healthz")
def healthz() -> dict:
    configuration = app.state.configuration
    health = {"status": "ok", "analysis": ANALYSIS_STATUS, "state": configuration.state}
    if configuration.problems:
        health["problems"] = list(configuration.problems)
    return health
