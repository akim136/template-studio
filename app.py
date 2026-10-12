"""Homebase Template Studio entrypoint.

Only the liveness route exists. Its body reports what is actually implemented,
so no reviewer or client infers an analysis capability that is absent. The
active phase lives in docs/phase_scope.json, not here.
"""

from fastapi import FastAPI

ANALYSIS_STATUS = "NOT_IMPLEMENTED"

app = FastAPI(
    title="Homebase Template Studio",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "analysis": ANALYSIS_STATUS}
