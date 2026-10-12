# Homebase Template Studio

Internal application for preparing signing templates from sponsor documents. The operator journey
runs from sponsor and signer policy, through an AI field draft, field correction, review and
approval, to a library record.

**Status: P2 state boundary (local only).** `GET /healthz` reports `"analysis": "NOT_IMPLEMENTED"`
because no app route analyzes anything yet. The shared analysis service reads the text layer and
geometry of every page, renders every page, and flags pages the user must look at. Field drafting
and self-check are `NOT_RUN` until the model route is wired in. The saved-state routes under `/api`
run against local disk only, with a simulated signing adapter. There is no R2 storage, no app
identity and no real signing provider. See
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

## Draft fields through the model route

```sh
uv run --locked python -m eval.infer SOURCE.pdf --model openai/gpt-4.1 --credential byok \
    --roles Client,CounterSigner --signer-mode MODE --run-cap 2.00 \
    --ledger PRIVATE/spend-ledger.jsonl --env-file PRIVATE/.env.local --out PRIVATE/run.json
```

Calls go through Vercel AI Gateway to OpenAI only, with no tools and no fallback models. Every route
choice and cap is explicit. One call with no page content checks the route, and no page is sent
unless it passes.
After every call, a model, provider or credential other than the one requested stops the run
(`ROUTE_VIOLATION`). The gateway can still fall back from your key to Vercel's credentials mid-run,
and that shows only after the call, so up to `--workers` pages (at most 4) may already be in flight.
A call whose worst case could cross the run cap or the $10 total is never sent, and a call that
costs more than it reserved stops the run (`BUDGET_STOPPED`). A call the gateway refuses (HTTP 4xx,
not a timeout) is recorded at $0 and stops the run. The record (source text and model
answers) goes to `--out`, which must be outside the repository: it is created owner-only before any
call and made read-only at the end. Stdout gets a summary with counts and spend only.

## Saved state (local only)

```sh
STUDIO_STATE_DIR=/path/outside/repo uv run --locked uvicorn app:app --host 127.0.0.1 --port 8773
```

Without `STUDIO_STATE_DIR`, and always on Vercel, every `/api` route answers 503: a deployment's
disk is ephemeral, so saving there would be a false Saved. The directory must be outside the
repository.

- `POST /api/sources` seals a PDF (raw body, at most 50 MiB) under its SHA-256, with its page
  inventory. This is the local stand-in for the direct R2 upload.
- `POST /api/templates` starts a template at revision 1 from a sealed source and a signing policy
  (`countersigner`, and the `required` field types per signer).
- `PUT /api/templates/{id}` saves `policy` and `fields` on top of `base_revision`. A stale base
  answers 409 with the current template, invalid fields answer 422, and a storage failure answers
  503: nothing is reported as saved unless it was.
- `POST .../review` and `POST .../approval` take the `revision` and `binding` (the hash of the
  source, policy and fields). Any other revision or binding is refused as stale (409). Every new
  revision starts unreviewed and unapproved.
- `POST .../operation` creates the template through the simulated adapter, only for the approved
  current revision. An unknown outcome keeps its operation ID and offers only
  `POST .../operation/status-check`; a second create is refused.

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
