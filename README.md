# Homebase Template Studio

Internal application for preparing signing templates from sponsor documents. The operator journey
runs from sponsor and signer policy, through an AI field draft, field correction, review and
approval, to a library record.

**Status: P0 foundation.** Only `GET /healthz` exists. It reports `"analysis": "NOT_IMPLEMENTED"`.
There is no PDF processing, model client, authentication, storage or signing adapter yet. See
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
