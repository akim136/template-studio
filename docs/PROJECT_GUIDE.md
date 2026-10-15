# Homebase Template Studio: project guide

**Guide 1.0.15 · October 15, 2026 · Owner: Alex Kim**

**Status:** the active phase is P4: stage 5 (the model decision) and stage 6 (release evidence) from section 3, and the repository work Alex approved after the demo on October 15, 2026. The stage 6 release is `main` at `527b3ca` (section 6, Release); the demo customer picker (PR #34) and two solution-brief updates (PRs #35 and #36) merged after it. The stage 5 head-to-head trials have finished; the write-up and the model decision are pending (`docs/model-decision.md`). Current details are in section 9, open questions in section 10.
- `/healthz` reports analysis as `DRAFTING_HOSTED`: the hosted studio drafts fields, and local mode does not (`docs/decisions.md`).
- Vercel Authentication is off since October 15, 2026. Sign in with Vercel identifies each operator but does not authorize, so hosted storage holds synthetic files only (section 6). Named invitations are NOT_APPLIED.
- Inference goes through Vercel AI Gateway. The hosted studio's ledger is capped at $20 (Alex, October 15, 2026); the CLI and evaluation ledger keeps $10. Live inference always needs Alex's authorization.

This is the canonical guide for this repository. It condenses the private kickoff (guide 1.0.14, PR plan, implementation acceptance, quality and model gate, access runbook) into public-safe requirements.

The following stay in that private kickoff, outside Git:
- historical evidence;
- private case identities;
- the access roster.

Amend this file deliberately in the PR that changes its rules or the current state. Every PR also describes itself in one change note under `docs/changes/` (section 8). Never replace this guide with a chat export.

**Where things live:**
- this guide: the rules and the current state;
- `docs/decisions.md`: Alex's dated decisions, and items still marked open or proposed there;
- `docs/changes/`: one note per pull request, from October 15, 2026;
- `docs/history.md`: the record frozen at guide 1.0.14-repo.57, with the old status, the session record, the revision table and the access observations from October 11 to 12, 2026.

## 1. Decisions and precedence

Precedence, highest first:
1. Alex's latest explicit decisions and this repository's policy.
2. This guide.
3. The current PR plan and quality contract in the private kickoff.
4. Claude Design exports, which are appearance and interaction reference only.
5. Older plans and historical evidence.

| ID | Decision |
|---|---|
| D1 | A parallel internal app. The existing Claude skill, upload host, MCP, signing account, production infrastructure and other repositories stay unchanged. No cutover is required. |
| D2 | The stack is Python 3.13, uv, FastAPI, Jinja/HTML/CSS and limited browser JavaScript. Excluded: a Node or Next.js backend, a Python-in-browser runtime, a framework switch, and any shared cross-project platform. |
| D3 | Keep R2 behind a narrow, independently authenticated Cloudflare adapter. Keep a signing adapter that is simulated by default. |
| D4 | Access uses Vercel Authentication with All Deployments protection and named access for seven people. Protection is applied, and named invitations are NOT_APPLIED (section 6). |
| D5 | Seven PR-sized stages with one active phase. No automatic merge or phase advancement (section 3). |
| D6 | The UI and the evaluation runner call the **same** Python analysis service. |

## 2. Workspace record (PR1)

| Field | Value |
|---|---|
| Repository | Fresh local Git repository created for this isolated session. The kickoff packet and design archive sit outside the Git root, unchanged. Remote: private GitHub repository `akim136/template-studio`, created at Alex's request on October 11, 2026. |
| Isolation | Independent of earlier Template Studio checkouts and another parallel session. Nothing was copied from them or changed in them. |
| Branches | `main` holds the bootstrap commit, and `chore/studio-foundation` holds PR1. |
| Local preview | `127.0.0.1:8773`. The preferred ports 8771 and 8772 were held by other sessions. |
| Budget | Elapsed and remaining assessment time is UNKNOWN until Alex records it. Inference is capped at $10 (authorized October 11, 2026); spend was tracked in the session record (`docs/history.md`). |
| Private inputs | Hash-verified copies of `dev-a` and `dev-b` are read in place, outside every repository, and never copied in. Run records stay in a private directory outside the repository. Baseline answers stay unopened until the evaluator work, and only the evaluator reads them (`docs/history.md`, session record). |

## 3. Seven PR-sized stages

| PR | Phase and branch | Deliverable | Gate before the next phase |
|---|---|---|---|
| 1 | P0 `chore/studio-foundation` | **Foundation:** FastAPI skeleton with `GET /healthz`, locked tooling, focused privacy checks, one CI workflow, this guide, the design-import map and phase declarations. | Alex's manual bootstrap acceptance. No private or answer-bearing artifacts in history. |
| 2 | P1 `spike/studio-analysis` | **Real analysis proof** on real source bytes:<br>• audited generic extraction;<br>• full page inventory;<br>• bounded classification and self-check;<br>• first and self-checked drafts preserved;<br>• stderr kept separate from JSON.<br>One service for both the app and the evaluator wrapper. | One honest, unmodified initial analysis is preserved when inference is authorized. No answer leakage. No parity claim based on a model label. |
| 3 | P2 `feat/studio-state-boundary` | **State, approval and authenticated external boundary:**<br>• direct R2 upload and sealed source;<br>• revision store;<br>• policy validation;<br>• working vs saved state;<br>• revision-bound review and approval;<br>• no-side-effect signing adapter;<br>• uncertain-operation handling.<br>Vercel protection is configured at the authorized deployment gate, before any sharing. | Stale saves and stale approvals are rejected server-side. Source identity stays consistent. At most two targeted development iterations. |
| 4 | P3 `feat/studio-core-ui` | **Selected core interface:** library, setup and editor from the approved import rows, with real upload, analysis, save and review, and simulated create. The mock login is removed. | Alex corrects real generated fields, saves, reloads, and reconfirms review after consequential edits. |
| 5 | P4a `test/studio-model-decision` | **Baseline comparison and model decision:** the agreed development comparison, plus the bounded two-profile study when authorized. | AI-draft quality, reviewed correctness and operator effort are reported separately. Missing references or budget means REVIEW_REQUIRED. |
| 6 | P4b `test/studio-release-evidence` | **Release evidence:**<br>• approval bypass;<br>• conflict and discard;<br>• geometry and zoom;<br>• parser errors;<br>• unsupported input;<br>• missing pages;<br>• duplicate unknown create;<br>• data separation;<br>• access checks. | Critical tests pass. Unrun checks are reported. |
| 7 | P5 `docs/studio-case-study` | **Case study and authorized reviewer access:** verified named access for each submitted URL, synthetic data only, no bypass link. | A real deployed thin slice is presented, with its limits stated. |

One branch and diff per stage. Advancing a phase is a separate `scope_update` change that Alex reviews (section 8). Local-only work is described as a "local PR-ready diff", not an opened PR. The seven stages do not grant seven new time budgets.

## 4. Product journey, scope and state rules

**Journey:**
1. Vercel-managed admission
2. Library
3. Sponsor selection
4. Upload a supported PDF, or use a clearly labeled synthetic sample
5. Explicit signer policy
6. Actual AI field draft
7. Field correction
8. Acknowledged save
9. Current-revision review confirmation and approval
10. Simulated create and verify
11. Accurate library record

**Out of scope:**
- an extra review or issue queue;
- new field types;
- an account-management product;
- a model UI or selector;
- a custom login (passwords or accounts of the app's own; Sign in with Vercel only identifies people, section 6);
- a conditional-form engine.

**State rules (PR3 server, PR4 UI):**
- **Saving.** Working edits stay separate from acknowledged saved revisions. A storage failure never shows Saved. Conflict, discard and reload restore the right snapshot.
- **Review and approval are bound to a revision.** Confirmation and approval bind to the saved source, policy and field revision.
  - Consequential edits clear them, including undo/redo and signer-policy changes.
  - Zoom and selection do not clear them.
  - A stale approval sent directly to the API is rejected.
- **Required fields.** Required fields come from the case's signing policy. An empty map is blocked. There is no universal LP-signature rule.
- **Lifecycle labels.** Creating, Verifying, Needs status check (unknown) and Failed are distinct states.
  - An unknown outcome keeps its operation ID and offers only a status check.
  - There is no blind duplicate create.
- **Sample data.** A new file never shows sample-fixture fields. There is no silent fallback to the sample.

**Coordinates and policy:**
- Coordinates are 72-point, top-left, per-page source coordinates. `document_index` is zero-based and `page` is one-based.
- The merge whitelist is `name`, `emailAddress`, `homePhone`, `email`, `amount`, `units`.
- Roles are `Client` (index 0) and, when the policy allows a countersigner, `CounterSigner` (index 1).

## 5. Analysis and quality requirements (PR2 onward)

- **One service.** One Python analysis entrypoint serves both the UI and the evaluation wrapper. The wrapper may supply local bytes and no-side-effect adapters. It must never fork the parser, prompt, image policy, validator or self-check.
- **Three stages:**
  - A: first AI draft;
  - B: post-self-check, pre-human draft;
  - C: human corrections.

  Each is a separate, immutable record. A human correction is not an AI quality improvement.
- **Run record.** Each run records:
  - exact source identity (byte hash) and the full page inventory;
  - a per-page evidence ledger: text, geometry, image rendered, image submitted, inspection received, and any skipped, unsupported or error reason;
  - failures;
  - the requested and actual model and provider for each stage;
  - retries, usage and time.

  Missing pages, truncated output or invalid output never count as a complete analysis.
- **Diagnostics stay out of JSON.** Parser diagnostics go to stderr, and JSON output stays parseable. A regression test is required. One development case printed parser diagnostics on stdout and broke its JSON output.
  - PR2: while it analyzes, the wrapper points both Python's stdout and file descriptor 1 at stderr, because PDFium is native code. Regression tests cover a Python print and a direct write to file descriptor 1.
- **Underscores are not templates.** Underscore-run extraction is a mechanics capability, not complete templatization. Drawn rules, checkboxes and label-only fields are capabilities to measure, not to drop silently.
- **No substitutes.** Sample or fixture fields never stand in for real analysis.
- **Generator isolation.** The generating model's requests and tools receive only the source PDF, approved business rules and scoped tools. They must never reach:
  - baseline outputs;
  - per-case field recipes;
  - answer-bearing overlays;
  - annotations;
  - evaluator reports.

  Separate folders, `.gitignore` or `chmod` do not establish isolation. PR2 needs a test showing that evaluator material cannot be retrieved.
  - PR2: tests show the service opens no files and the wrapper opens only the source, not decoy evaluator files beside it.
  - Model route: a test runs the real `eval.infer` command with decoy baseline, overlay and evaluator files holding a canary beside the source, and captures every request at the transport. It checks that only the source was opened, that no request contains the canary, that each request has only the allowed keys (no tools, one provider, no fallback models), that the one route preflight carries no page content, that each stage A image is exactly the rendered source page, that each stage B image is exactly that page with the stage A draft drawn on it, and that the token appears in no output.
- **Development cases.**
  - `dev-a` (97 pages) and `dev-b` (64 pages) are private, *exposed* development cases, not holdouts.
  - Their references are the baseline's proposed references. Alex reviewed them on October 12, 2026 and confirmed the field selection (`docs/history.md`, Inference row). The scoring tolerances are still placeholders.
  - Baseline answers stay unopened until the evaluator work.
- **Model decision:**
  - Keep the first unmodified candidate result.
  - Allow at most two targeted development iterations, each with a diff, a linked test and a full development-set rerun.
  - Compare at most two model profiles on a frozen harness, and only when separately authorized.
  - No hidden provider fallback. Keep every trial.
  - The status is UNSELECTED, PROVISIONAL_DEVELOPMENT or SELECTED_FOR_SUPERVISED_PILOT.
  - A working editor or valid JSON does not establish model quality.
- **Live inference** is an explicit, authorized, budgeted run. CI never triggers it.

## 6. Access (Vercel Authentication off since October 15, 2026; invitations NOT_APPLIED)

- **Mechanism (until October 15, 2026; see Release below):** Vercel Authentication with All Deployments protection, and named deployment access for the seven people in the private roster. The roster stays out of Git, client code and fixtures.
- **Not allowed:** a custom login, an email-domain gate, a bearer bypass link, a shared password, an automatic plan upgrade, or an assumed application reviewer identity.
- **Admission is not app identity.** Vercel admission does not make someone a verified application actor. Local loopback use records sign-offs as `anonymous`.
- **App identity (decided October 12, 2026, Alex): Sign in with Vercel.** This overrides the kickoff's instruction not to show a second login. Vercel Authentication still decides who reaches a deployment; Sign in with Vercel (OAuth 2.0 with PKCE, scopes `openid email profile`) only tells the app who that person is. Hosted sign-offs record the email Vercel's userinfo endpoint returns, and only when Vercel marks it verified. The app keeps no list of people, reads no undocumented Vercel cookie or header, and trusts no name or email a browser sends (a `by` in a request body is refused). The session is an HttpOnly, Secure, SameSite=Lax cookie signed by the server, valid for 8 hours. Signing out clears it and says that it does not sign anyone out of Vercel.
- **Cloudflare adapter.** It requires its own authenticated boundary. Vercel cookies are not forwarded, and no bypass secret goes to the browser.
- **Reviewer data.** Shared reviewer demos use synthetic data only. Real create and send are disabled server-side. Hosted uploads go to R2, so a deployment can hold any PDF a signed-in person uploads. Alex decided on October 12, 2026 that his own testing on the hosted preview may use real PDFs (`docs/decisions.md`).
- **At the authorized deployment phase:**
  - verify the actual team, project, plan and effective access;
  - test signed-out, listed, non-listed, direct-API and alternate-URL access;
  - record only what was observed.
- **Release (observed October 15, 2026):**
  - Vercel Authentication is off for production and previews (Alex, at 00:54 UTC). Signed out, `/` answers 303 to `/auth/login`, which goes to Vercel's OAuth (PKCE with S256, scopes `openid email profile`); the API and `/auth/me` answer 401.
  - Sign in with Vercel gives identity, not authorization: the app keeps no list of people, so any Vercel account with a verified email can sign in, draft within the shared $20 cap, review and approve. For that reason production and every preview now use a new bucket that holds synthetic files only, through a token scoped to that bucket alone. The original bucket is kept and was not read, except for its ledger head: that ledger's total, $6.36028440, seeded the new ledger's opening balance, so the cap was not reset.
  - Rollback target R0: production redeployed at `3f03aa8` after the storage settings changed (`dpl_8XmtKtujsnsmmPi1XZEd9TGQgikF`); it reads and writes only the synthetic-only bucket.
  - Release: PRs #26, #27, #28, #29 and #31 merged in that order. `main` at `527b3ca` has the same tree as `b4a7dfd`, the commit validated on its preview; production is `dpl_G11Ri6HAuDtjPUwehv71gFHG1qMe`. Locally, 698 tests, Ruff and the phase and private checks pass.
  - Hosted checks, signed in through Alex's browser and driven by the coding session, on synthetic PDFs: on the `b4a7dfd` preview and again on production, a fresh draft, edit, save, reload, review, approval, invalidation by a later save, and simulated create; a rotated page AI left unchecked blocked approval until confirmed by hand, and a later save kept that record; a file that is not a PDF was refused. On production also: a second tab on the same draft did not save it twice, the library filters, sign-out and sign-in, and no runtime errors logged. Validation drafting spent $0.069 over 5 drafts.
  - NOT RUN: a signed-in check from an account outside the team; hosted failure injection (save retry and storage errors are covered by local tests); hosted layout at 1024 and 390 px (checked locally at exact widths on the same code).

## 7. Design source (reference only)

| Item | Value |
|---|---|
| Received archive | `Homebase Template Studio Prototype (4).zip`, SHA-256 `d23cb2f6c7b9125ac3804d18f13c0e987639f4596e3cb1c60d8dbea4cb365c08`. Newer than the reviewed (3) archive (`53dd96598e36…ffd0a`) and not yet reviewed by the kickoff. |
| Canonical entrypoint | `Template Studio Clean.dc.html` v2.3, SHA-256 `94a7b7b0880e80fbeec83852e47ede7c6535eac1a2ab3c4616f2cab6596c790f` |
| Lineage | `baseline/v2` is the reviewed (3) file (`7014350b8a3c…cecb`). `baseline/v2.1` is `139bdd521880…63d4`. |
| Backend | NOT ESTABLISHED. It is a React 18 prototype using localStorage and fixtures. |

Conflicts are recorded here. They are rejected at import, not fixed through another design round:
1. A client fixture (`fixtures/clean/studio.json`, `access.allowlist`) embeds the named access list. **Reject.** The roster never enters this repository or any client.
2. A mock "Sign in with your Vercel account" screen and signed-in-person attribution. **Reject.**
3. The archive's handoff suggests the app re-check the named emails server-side and attribute approvals to them. **Reject for now.** Managed admission is not verified app identity (section 6).
4. It ships font binaries, `uploads/` (including a demo PDF) and a `model_configuration` fixture, although its handoff says these are excluded. **Do not import.** There is no model UI.

Per-component decisions are in `docs/design-import.md`.

## 8. Phase control and checks

**Phase files.** `docs/phase_scope.json` holds the active phase, features, permitted paths and scope files. When the policy names a `changes` directory (`docs/changes/`, from repo.57 on), each PR declares itself in the one change note it adds there, `docs/changes/YYYY-MM-DD-short-name.md`. The note starts with `phase`, `kind` and comma-separated `features` lines between two `---` lines, then says what changed and why. Notes already merged never change. Before that, `docs/phase_change.json` (removed with the guide split) was each PR's fresh declaration, and every PR also amended this guide and added a row to its revision table (`docs/history.md`).

**Phase check.** `python3 scripts/check_repo.py phase --base main` reads the policy from the **base** revision, so a feature PR cannot widen its own allowlist. It requires:
- exactly one new, well-formed change note (or, under a base policy without `changes`, a fresh declaration and a guide update);
- a matching phase: a feature declares the base's active phase, and a `scope_update` declares the phase its proposed policy sets;
- declared feature IDs;
- changed paths inside those features.

A `kind: scope_update` change may touch only scope files, its change note (or the declaration) and this guide. It cannot bundle operational changes, and it must leave a valid policy. It is the only way a phase advances, which is why the app does not carry the phase. Exit code 3 means BOOTSTRAP_REQUIRED.

**Merge gate.** Run the checker from the base revision, not from the branch:
```sh
base=$(mktemp) && git show main:scripts/check_repo.py > "$base" && test -s "$base" \
  && python3 "$base" phase --base main; echo "exit $?"
```
Merge only on `exit 0`. Any other exit blocks the merge, including a failed `git show` or an empty checker. For PR1, `main` had neither the checker nor a policy, so the snippet stops at `git show` (exit 128). The branch copy reports BOOTSTRAP_REQUIRED, and Alex's manual review was the gate. A test runs this snippet.

**Limits:**
- CI runs code from the PR, so it is a backstop, not a tamper-proof gate.
- A JSON field cannot authenticate a reviewer.
- Checks cannot prove that code inside an allowed file stays in scope. Human diff review remains required.
- `pyproject.toml`, `tests/conftest.py` and `tests/test_repo_contract.py` are feature paths. A feature PR could therefore weaken test collection or the CI contract test without a `scope_update` (section 10).
- A `scope_update` can change the workflow, checker or agent rules without touching the policy. Alex reviews every one.
- Features are not tied to a phase. A P1 change may still declare `F-P0-foundation`, and features may share paths (`F-P1-analysis` includes `pyproject.toml` and `uv.lock` for the parser dependency).

**Privacy check.** `python3 scripts/check_repo.py private --staged|--all-history [--denylist FILE]`.
- The denylist of exact protected emails and specific confidential identifiers lives outside Git.
- Hosted CI runs without the denylist, so it checks only the generic rules.
- Before any reviewer that sends source outside the workspace, run the denylist check on the review payload and on history.

**CI.** One ordinary workflow, `.github/workflows/ci.yml`. It has read-only permissions, no secrets and no `pull_request_target`, and its actions are pinned to commit SHAs. It runs the generic private scan over all history; on pull requests, the merge gate above (the base revision's checker against the PR's base commit); a syntax check of `static/*.js` with `node --check`; then a locked sync, lint, a format check and the tests. A second job runs the Playwright browser tests in `tests/e2e/` with `STUDIO_E2E=1`, and passes without installing a browser until that directory exists. CI runs the base checker but is still not tamper-proof (Limits), so Alex runs the merge gate himself before each merge.

## 9. Current state

| Field | Value |
|---|---|
| Phase | P4. Features: `F-P4a-model-decision` (stage 5), `F-P4b-release-evidence` (stage 6), and, from October 15, 2026, `F-P4d-docs`, `F-P4e-browser-tests`, `F-P4f-frontend-tidy` and `F-P4g-inference` (`docs/phase_scope.json`). |
| Analysis | `DRAFTING_HOSTED`. The hosted studio drafts each page through `openai/gpt-5.4-mini` on Alex's key, with the fallback to Vercel's credentials accepted (`studio/state/drafts.py`): stage A drafts, then stage B checks the draft against the page image. Local mode does not draft. |
| Access | Section 6, Release. Named invitations NOT_APPLIED. |
| CI | Section 8. |
| Built so far | The session record in `docs/history.md` describes how the analysis service, model route, state and interface were built up to repo.57, including the model route's fallback and budget rules. It still describes the code unless a later change note says otherwise. |
| Next | The repository work Alex approved after the demo, as small pull requests each with one change note: this guide split, browser tests (Playwright), a tidy of the plain-JS frontend, and running stage B only on pages where the model drafted fields. A decision on moving the frontend to Next.js comes after the browser tests; until then section 1 (D2) and `AGENTS.md` exclude a framework switch. Stage 5: the write-up and model decision. Still open from earlier stages: approved real-document tolerances, and adopting the checker's 26 controls in this repository (NOT RUN). |

## 10. Open questions

- The assessment time budget, both elapsed and remaining.
- The test client: adopt `httpx2`, which Starlette 1.7 prefers, or keep `httpx` with the single targeted warning filter.
- Whether to send the design (4) conflicts back to Design, or only reject them at import.
- Whether `pyproject.toml`, `tests/conftest.py` and `tests/test_repo_contract.py` become scope files, or CI runs the scope tests with a fixed configuration (section 8).
- Whether a `scope_update` that changes the workflow, checker or agent rules needs its own kind.
- Whether the owner's full name stays in this guide once the repository's visibility is decided.

Items marked Open or Proposed in `docs/decisions.md` also stay open until Alex decides them.
