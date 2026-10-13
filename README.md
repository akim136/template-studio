# Homebase Template Studio

Internal application for preparing signing templates from sponsor documents. The operator journey
runs from sponsor and signer policy, through an AI field draft, field correction, review and
approval, to a library record.

**Status: P3 core interface, slice 4c: library, setup, record, Analyzing and field editor pages (local, and hosted on production and every preview), with in-app field drafting on the hosted studio only.** `GET /healthz` reports `"analysis": "DRAFTING_HOSTED"`:
fields are drafted on the hosted studio only. The shared analysis service reads the text layer and geometry of every page, renders
every page, and flags pages the user must look at. On the hosted studio, the Analyzing page drafts
and self-checks fields with the model route, one model call per request, within the $10 spend cap;
local mode does not draft. The saved-state routes under `/api`
run against local disk, or on a deployment against Cloudflare R2 with Sign in with Vercel, always
with a simulated signing adapter. There is no real signing provider. See
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

## Saved state (local)

```sh
STUDIO_STATE_DIR=/path/outside/repo uv run --locked uvicorn app:app --host 127.0.0.1 --port 8773
```

Then open `http://127.0.0.1:8773/` for the library. Setup (`/new`) uploads a PDF, takes the typed
sponsor account and the signature policy, and saves revision 1 with no fields; the record page shows a
saved template read-only, and its Edit fields link opens the field editor. The editor shows each page
with the saved fields over it: add, move, resize and change fields, undo and redo, then save a new
revision; review and approve that exact revision and run the simulated create from its review
drawer. Drafting fields (the Analyzing page) runs on the hosted studio only. The pages follow the
same rules as the `/api` routes below.

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
- `GET /api/sources/{sha256}/pages/{n}.png` returns one page of a sealed source as a PNG for the
  editor, rendered once from the sealed bytes and kept beside the source. It is sent with
  `no-store`, so the browser does not keep the document's pages.
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
  So does an unknown outcome: one that arrives after a status check declared the operation FAILED
  reopens it, and nothing found counts as failed only two minutes after the call returned. One
  reported, found or left unknown after its operation was replaced takes the slot back if the
  replacement failed and nothing was created since; otherwise it is kept in the orphan list,
  because the provider may hold both. An orphan that cannot be written is named only in the 503
  error.
- The create, read and status-check answers carry `orphans` (unresolved ones) and, while any
  remain, a `warning`. An unresolved orphan refuses every create with 409, listing them under
  `detail.current.orphans`, until someone checks the provider and posts
  `POST .../operation/orphans/{operation_id}/resolve` with a `note` saying what they found; the
  answer is the resolved record.

## Hosted state (R2 and Sign in with Vercel)

Hosted mode is the only mode on Vercel. It needs every one of these settings; a missing or
malformed one (stray spaces or quotes included) keeps state off, and `/healthz` and the startup
log name it without showing its value:

| Setting | What it is |
|---|---|
| `R2_ACCOUNT_ID`, `R2_BUCKET` | The Cloudflare account (32 hex characters) and the bucket |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | An R2 token limited to Object Read & Write on that bucket |
| `VERCEL_OAUTH_CLIENT_ID`, `VERCEL_OAUTH_CLIENT_SECRET` | The Sign in with Vercel app |
| `STUDIO_SESSION_SECRET` | At least 32 random characters; signs the session cookie |
| `STUDIO_PUBLIC_ORIGIN` | The origin sign-in returns to, such as `https://host` (no path). A Vercel preview may leave it unset and use its branch URL (`https://` and `VERCEL_BRANCH_URL`) |

Vercel Authentication decides who reaches a deployment. Sign in with Vercel only tells the app who
that person is: `GET /auth/login` starts it (OAuth 2.0 with PKCE), `GET /auth/callback` finishes
it, `GET /auth/me` shows the verified email, and `POST /auth/logout` clears the studio's session,
not the Vercel one. Review and approval record that email. The app keeps no list of people.

Every hosted `/api` route needs a session (401 without one) and, for a change, a request from the
studio's own origin (403 otherwise). On a preview, use the branch URL (`...-git-<branch>-...`): a
change sent from another of its URLs is refused, and sign-in moves to the branch URL. Uploads skip
the function body limit:

- `POST /api/uploads` with `{"sha256", "size"}` returns a presigned `PUT` URL for the key named
  by that hash and for that length, valid for 10 minutes. The browser sends the PDF straight to R2,
  which does not check the bytes; seal does.
- `POST /api/uploads/{sha256}/seal` reads the upload back, re-hashes it and seals it as a source
  (404 if nothing was uploaded, 422 if the bytes do not match the hash or are not a usable PDF).

Run hosted mode locally against the bucket by setting all eight (with
`STUDIO_PUBLIC_ORIGIN=http://localhost:8771`) and no `STUDIO_STATE_DIR`. The Sign in with Vercel
app must list `http://localhost:8771/auth/callback`; the bucket's CORS allows that origin.

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
