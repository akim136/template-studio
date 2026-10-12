"""Homebase Template Studio entrypoint (P0 foundation).

Only the liveness route exists. Its body reports what is actually implemented,
so no reviewer or client infers an analysis capability that is absent.
"""

from fastapi import FastAPI

PHASE = "P0"
ANALYSIS_STATUS = "NOT_IMPLEMENTED"

app = FastAPI(
    title="Homebase Template Studio",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "phase": PHASE, "analysis": ANALYSIS_STATUS}
