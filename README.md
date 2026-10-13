# Homebase Template Studio

An internal tool that turns a sponsor's subscription document into a signing template. An operator
uploads the PDF and sets who signs, the studio drafts the signature fields with a model, the
operator corrects them, someone reviews and approves that exact revision, and the studio creates
the template and keeps it in the library.

## Status

P3, the core interface. Working today:

- Library, setup, Analyzing, field editor and record pages.
- Field drafting on the hosted studio only: each page is drafted and then checked by one model
  call each, within a $10 spend cap. Drafting goes on while any studio page is open. A finished
  draft saves itself, and the template then needs review. Local mode does not draft.
- Saved revisions; review and approval bound to one revision; and a create through a **simulated**
  signing adapter. There is no real signing provider.

`GET /healthz` reports `"analysis": "DRAFTING_HOSTED"`: the hosted studio drafts fields, and local
mode does not.

## How it is built

| Part | What it uses |
|---|---|
| App | Python 3.13 and FastAPI, with server-rendered Jinja pages and plain JavaScript |
| Hosting | One Vercel project. Vercel Authentication protects production and every preview |
| Identity | Sign in with Vercel (OAuth with PKCE). Review and approval record the verified email. The app keeps no list of people |
| Saved state | Cloudflare R2: sealed sources, immutable revisions and a head pointer, every write conditional. A local run uses a directory instead |
| Uploads | The browser sends the PDF straight to R2 with a presigned URL. The app re-hashes it before use |
| Analysis | One service (`studio.services.analysis`) reads, renders and flags every page. The app and `eval/` share it |
| Drafting | Vercel AI Gateway to OpenAI only. Each call names one model and one credential, and the route is checked before any page is sent |
| Signing | A simulated adapter |

## Run locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```sh
uv sync --locked
STUDIO_STATE_DIR=/path/outside/repo uv run --locked uvicorn app:app --host 127.0.0.1 --port 8771
```

Open `http://127.0.0.1:8771/`. The state directory must be outside the repository, a real directory,
owned by you, with mode 700. Without it, the pages and `/api` answer 503 instead of pretending to
save. Local state answers only loopback requests from the studio's own pages. That is not a login.

## Hosted settings

Vercel uses hosted mode only. A missing or malformed setting, including stray spaces or quotes,
keeps state off, and `/healthz` and the startup log name the setting without showing its value.

| Setting | What it is |
|---|---|
| `R2_ACCOUNT_ID`, `R2_BUCKET` | The Cloudflare account (32 hex characters) and the bucket |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | An R2 token limited to Object Read & Write on that bucket |
| `VERCEL_OAUTH_CLIENT_ID`, `VERCEL_OAUTH_CLIENT_SECRET` | The Sign in with Vercel app |
| `STUDIO_SESSION_SECRET` | At least 32 random characters. Signs the session cookie |
| `STUDIO_PUBLIC_ORIGIN` | The origin sign-in returns to, such as `https://host` (no path). A preview may leave it unset and use its branch URL |

Drafting needs no setting of its own. Its model credential is the OIDC token Vercel sends with each
request, and its spend ledger lives in R2. On a preview, use the branch URL
(`...-git-<branch>-...`), because changes from any other URL are refused.

## Command-line tools

```sh
uv run --locked python -m eval.analyze SOURCE.pdf > /path/outside/repo/run.json
uv run --locked python -m eval.infer SOURCE.pdf --model openai/gpt-4.1 --credential byok \
    --roles Client,CounterSigner --signer-mode MODE --run-cap 2.00 \
    --ledger PRIVATE/spend-ledger.jsonl --env-file PRIVATE/.env.local --out PRIVATE/run.json
```

`eval.analyze` prints one JSON run record with the page inventory, evidence and flags. `eval.infer`
drafts fields through the same gateway route rules as the app, with its own ledger and run cap.
Both records contain source text, so keep them outside the repository. The route, fallback and
budget rules are in the guide (section 9, "Model route").

## Checks

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
python3 scripts/check_repo.py private --all-history [--denylist /path/outside/repo]
python3 scripts/check_repo.py phase --base main   # exit 3 = BOOTSTRAP_REQUIRED (manual gate)
```

Without `--denylist`, the private check applies only its generic rules: blocked file types and
private paths, PDF and ZIP magic bytes, and a 1 MiB size cap. Hosted CI runs exactly that. The
denylist of protected values stays outside Git.

## More

- [`docs/PROJECT_GUIDE.md`](docs/PROJECT_GUIDE.md): decisions, stages and gates, access, the
  state and model-route rules, the session record and open decisions.
- [`docs/design-import.md`](docs/design-import.md): what was taken from the design prototype,
  and why.
- `tests/state` and `tests/ui`: the exact `/api` behaviour, such as saves and conflicts, review and
  approval, create, status checks and orphans.
- [`solution-brief/`](solution-brief/README.md): the static solution brief for Vercel's
  assessment. Its own Vercel project serves it, and it does not change the app.
