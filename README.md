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
document). A source over 25 MiB, 400 pages or 4,000,000 characters of text, or whose analysis passes
3 minutes (checked between pages and at the end), fails with a record that names the limit. Parser diagnostics go to stderr. Run
records contain source text, so keep them outside the repository.

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
With `--accept-fallback` (byok only), such a call passes instead, but only if it also shows a failed
attempt on your key: the run continues, and each page call that fell back is marked in its
evidence. Every byok run counts the calls that fell back, accepted or not, in `fallback_calls`; a
call that fell back and then failed appears only in that count and in its ledger row.
A call whose worst case could cross the run cap or the $10 total is never sent, and a call that
costs more than it reserved stops the run (`BUDGET_STOPPED`). A call the gateway refuses (HTTP 4xx,
not a timeout) is recorded at $0 and stops the run. The record (source text and model
answers) goes to `--out`, which must be outside the repository: it is created owner-only before any
call and made read-only at the end. Stdout gets a summary with counts and spend only.

## Saved state (local only)

```sh
STUDIO_STATE_DIR=/path/outside/repo uv run --locked uvicorn app:app --host 127.0.0.1 --port 8773
```

Without `STUDIO_STATE_DIR`, and always on Vercel, every `/api` route answers 503 before any body
is read: a deployment's disk is ephemeral, so saving there would be a false Saved. The directory
must be outside the repository, a real directory (not a link), owned by you and owner-only (mode
700). JSON bodies are at most 1 MiB, checked by declared length and by the bytes that arrive (413).
State routes answer only requests from a loopback address that are addressed to `127.0.0.1`,
`localhost` or `::1`, even if the server listens on every interface, and refuse (403) one sent by a
page on any other origin. That keeps other machines, other sites and rebound host
names out; it is not a login, and any process on this machine is answered. A failed read answers
503 and says to reload.

- `POST /api/sources` seals a PDF (raw body, at most 25 MiB) under its SHA-256, with its page
  inventory. This is the local stand-in for the direct R2 upload.
- `POST /api/templates` starts a template at revision 1 from a sealed source and a signing policy
  (`countersigner`, and the `required` field types per signer).
- `PUT /api/templates/{id}` saves `policy` and `fields` on top of `base_revision`. Every save makes
  a new revision, even with identical content, and clears review and approval. Each save carries a
  `save_id` the client chooses (8 to 64 letters, digits, `-` or `_`). A stale base answers 409 with
  the current template; a retry of the same request (same `save_id`, base and content) returns the
  revision it made instead, if nothing was saved on top of it, while another tab's save of the same
  content has its own `save_id` and gets 409. Invalid fields answer 422. A storage failure answers
  503 and says the save may or may not have landed, so reload before retrying.
- `POST .../review` and `POST .../approval` take the `revision` and `binding` (the hash of the
  source, policy and fields). Any other revision or binding is refused as stale (409). Every new
  revision starts unreviewed and unapproved. A source whose page inventory is `INCOMPLETE` can be
  saved against, but review, approval and create stay blocked, and its flags come with the
  template. A box on a page whose rotation the two PDF readers disagree on is refused, and the page
  is flagged. A template's source is fixed: a complete source needs a new template.
- `POST .../operation` creates the template through the simulated adapter, only for the approved
  current revision, and once per approved revision: a later approved revision is created again,
  and the operation lists the ones it replaced in `replaced`. An unknown outcome keeps its
  operation ID and offers only `POST .../operation/status-check`; a second create of the same
  revision is refused. A create the provider holds wins over a concurrent check that missed it.
  One reported or found after its operation was replaced takes the slot back if the replacement
  failed and nothing was created since; otherwise it is kept in the orphan list, because the
  provider may hold both. An orphan that cannot be written is named only in the 503 error.
- The create, read and status-check answers carry `orphans` (unresolved ones) and, while any
  remain, a `warning`. An unresolved orphan refuses every create with 409, listing them under
  `detail.current.orphans`, until someone checks the provider and posts
  `POST .../operation/orphans/{operation_id}/resolve` with a `note` saying what they found; the
  answer is the resolved record.

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
