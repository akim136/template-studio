# Homebase Template Studio

Internal application for preparing signing templates from sponsor documents. The operator journey
runs from sponsor and signer policy, through an AI field draft, field correction, review and
approval, to a library record.

**Status: P1 analysis spike.** The app serves only `GET /healthz`, which reports
`"analysis": "NOT_IMPLEMENTED"` because no app route analyzes anything yet. The shared analysis
service reads the text layer and geometry of every page, renders every page, and flags pages the
user must look at. Field drafting and self-check are `NOT_RUN` until the model route is wired in.
There is no authentication, storage or signing adapter yet. See
[`docs/PROJECT_GUIDE.md`](docs/PROJECT_GUIDE.md) for decisions, stages and gates, and
[`docs/design-import.md`](docs/design-import.md) for the design-import map.

Hosting: a private GitHub repository and a Vercel project. Vercel Authentication protects every
deployment, and no one else has been invited yet.

## Run locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```sh
uv sync --locked
uv run --locked uvicorn app:app --host 127.0.0.1 --port 8773
curl -s http://127.0.0.1:8773/healthz
```

Port 8771 is the usual preference. This workspace uses 8773 because 8771 and 8772 were in use.

## Analyze a source PDF locally

```sh
uv run --locked python -m eval.analyze SOURCE.pdf > /path/outside/the/repo/run.json
```

The wrapper calls `studio.services.analysis.analyze`, the same service the app will use. Stdout is
one JSON run record: source SHA-256, the page inventory (`COMPLETE` or `INCOMPLETE`), per-page
text, geometry and rendered-image evidence, and flags (per page, or `page: null` for the whole
document). Sources over 50 MiB are rejected. Parser diagnostics go to stderr. Run records contain
source text, so keep them outside the repository.

## Checks

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
python3 scripts/check_repo.py private --all-history [--denylist /path/outside/repo]
python3 scripts/check_repo.py phase --base main   # exit 3 = BOOTSTRAP_REQUIRED (manual gate)
```

Without `--denylist`, the private check applies only its generic rules: blocked file types and
private paths, PDF and ZIP magic bytes, and a 1 MiB size cap. That is what hosted CI runs. The
denylist of protected values stays outside Git.
