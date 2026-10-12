"""Homebase Template Studio entrypoint.

The liveness route reports what is actually implemented, so no reviewer or client infers an
analysis capability that is absent. The active phase lives in docs/phase_scope.json, not here.

The state routes under /api answer 503 unless STUDIO_STATE_DIR names a local directory outside
the repository. Nothing is configured on a deployment (see studio/state/api.py).
"""

import os
from pathlib import Path

from fastapi import FastAPI

from studio.state.api import router as state_router
from studio.state.api import services_from_environment

ANALYSIS_STATUS = "NOT_IMPLEMENTED"

app = FastAPI(
    title="Homebase Template Studio",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.services = services_from_environment(os.environ, Path(__file__).resolve().parent)
app.include_router(state_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "analysis": ANALYSIS_STATUS}
