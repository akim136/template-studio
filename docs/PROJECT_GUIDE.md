# Homebase Template Studio: project guide

**Revision 1.0.14-repo.17 · October 12, 2026 · Owner: Alex Kim**
**Status:** P0 is complete (PR #1, PR #2). P1 is active (PR #3). The analysis service merged through PR #4 on October 12, 2026, after green checks. The model route (PR #5) is open; its first unmodified run on both development cases is preserved, PARTIAL and not yet scored (section 9). The `scope_update` that advances the active phase to P2 (PR #6) is stacked on PR #5, and PR3's local state core is stacked on PR #6. The P1 gate (one honest, unmodified initial analysis preserved) comes first: that run is preserved for Alex to accept, unless he waives the gate.
- Analysis is NOT_IMPLEMENTED in the app. PR2 (PR #4, merged) added the shared analysis service: text, geometry, page images and a full page inventory. The model route (stacked on PR2) drafts and self-checks fields through the gateway, outside the app. Alex picked `gpt-4.1` on his key, with a fallback to Vercel's credentials accepted and recorded, and `o4-mini` as a backup. The first unmodified run on both development cases is PARTIAL and not yet scored (Inference row).
- Vercel Authentication protects all deployments. Named invitations are NOT_APPLIED.
- Inference is authorized through Vercel AI Gateway with a $10 cap. Spend so far: $0.0027, on a text-only gateway smoke test and model-access probes. No document content has been sent.

This is the canonical guide for this repository. It condenses the private kickoff (guide 1.0.14, PR plan, implementation acceptance, quality and model gate, access runbook) into public-safe requirements.

The following stay in that private kickoff, outside Git:
- historical evidence;
- private case identities;
- the access roster.

Amend this file deliberately in the PR that changes behavior or status. Never replace it with a chat export.

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
| Budget | Elapsed and remaining assessment time is UNKNOWN until Alex records it. Inference is capped at $10 (authorized October 11, 2026); spend is in section 9. |
| Private inputs | Hash-verified copies of `dev-a` and `dev-b` are read in place, outside every repository, and never copied in. Run records stay in a private directory outside the repository. Baseline answers stay unopened. |

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
- a custom login;
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
  - Their references are UNREVIEWED.
  - Baseline answers stay unopened until the evaluator work.
- **Model decision:**
  - Keep the first unmodified candidate result.
  - Allow at most two targeted development iterations, each with a diff, a linked test and a full development-set rerun.
  - Compare at most two model profiles on a frozen harness, and only when separately authorized.
  - No hidden provider fallback. Keep every trial.
  - The status is UNSELECTED, PROVISIONAL_DEVELOPMENT or SELECTED_FOR_SUPERVISED_PILOT.
  - A working editor or valid JSON does not establish model quality.
- **Live inference** is an explicit, authorized, budgeted run. CI never triggers it.

## 6. Access (protection applied, invitations NOT_APPLIED)

- **Mechanism:** Vercel Authentication with All Deployments protection, and named deployment access for the seven people in the private roster. The roster stays out of Git, client code and fixtures.
- **Not allowed:** a custom login, an email-domain gate, a bearer bypass link, a shared password, an automatic plan upgrade, or an assumed application reviewer identity.
- **Admission is not app identity.** Vercel admission does not make someone a verified application actor. Until identity is deliberately added, demo actions are recorded as anonymous to the app.
- **Cloudflare adapter.** It requires its own authenticated boundary. Vercel cookies are not forwarded, and no bypass secret goes to the browser.
- **Reviewer data.** Shared reviewer demos use synthetic data only. Real create and send are disabled server-side.
- **At the authorized deployment phase:**
  - verify the actual team, project, plan and effective access;
  - test signed-out, listed, non-listed, direct-API and alternate-URL access;
  - record only what was observed.
- **PR1 status (observed October 11, 2026):**
  - Vercel project `template-studio` on a Hobby-plan team, linked to the GitHub repository, with production branch `main`.
  - Vercel Authentication covers all deployments (`ssoProtection.deploymentType: all`, read back through the API). There is no password, trusted-IP or automation bypass, and no share link was created.
  - Signed out, the generated URL, both production aliases and the branch alias redirect `/`, `/healthz` and `/openapi.json` to Vercel's login with no app output.
  - The first deployment became production from the PR1 branch head, because the project had no production deployment yet. Since the PR1 merge, production deploys from `main`.
  - Named invitations are NOT_APPLIED. Hobby allows one external collaborator, and Alex chose none for now.
  - Signed in, Alex opened production `/healthz` and saw `{"status":"ok","analysis":"NOT_IMPLEMENTED"}` (EXECUTED by Alex, October 11, 2026). The non-listed-user test is pending.

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

**Phase files.** `docs/phase_scope.json` holds the active phase, features, permitted paths and scope files. `docs/phase_change.json` is the current PR's fresh declaration.

**Phase check.** `python3 scripts/check_repo.py phase --base main` reads the policy from the **base** revision, so a feature PR cannot widen its own allowlist. It requires:
- a fresh declaration;
- a guide update;
- a matching phase: a feature declares the base's active phase, and a `scope_update` declares the phase its proposed policy sets;
- declared feature IDs;
- changed paths inside those features.

A `kind: scope_update` change may touch only scope files, the declaration and this guide. It cannot bundle operational changes, and it must leave a valid policy. It is the only way a phase advances, which is why the app does not carry the phase. Exit code 3 means BOOTSTRAP_REQUIRED.

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

**CI.** One ordinary workflow, `.github/workflows/ci.yml`. It has read-only permissions, no secrets and no `pull_request_target`, and its actions are pinned to commit SHAs. It runs the generic private scan over all history, then a locked sync, lint, a format check and the tests. It does not run the phase check; the merge gate above is manual.

## 9. Session record

| Field | Value |
|---|---|
| Phase | P2 once this `scope_update` merges. P1 was set by PR #3; PR #4 (analysis service) merged with a merge commit on October 12, 2026, after green checks; PR #5 (model route) is open. P0 ended with PR #1 and PR #2. |
| Reviews | PR1: `/codex-review` approve with 3 medium findings; reviewer subagent 8 FIX and 4 ESCALATE. PR2 (PR #4): `/codex-review` approved each round; round 3 raised a render-budget finding (fixed) and an unused-helper note (kept for the model route); round 4 raised an unbounded wrapper read (fixed) and missing text, page and time budgets (open, section 10). Reviewer subagent round 2: 11 FIX, all fixed with tests; 2 ESCALATE, resolved under Alex's rule that anything incomplete is flagged (section 10). Model route (PR #5): reviewer subagent 9 FIX, all fixed with tests, and 2 ESCALATE (section 10); `/codex-review` round 1 requested changes with 3 high findings (credential fallback seen only after a call, pages fanned out before the route was proven, a world-readable run record) and 9 others, all fixed or recorded in section 10. Round 2 resolved all 12; it raised 1 high finding (the run record was created only after the calls; fixed) and 8 others: 6 fixed, the per-call ledger reparse kept (the $10 cap bounds the ledger to a few thousand rows), and the failed-attempt rule escalated (section 10). Round 3, the last automatic round, raised 1 high finding (a preflight that failed without stopping the run still let pages be sent; fixed: no page is sent unless the preflight passes), a page whose candidate scan failed lost its size (fixed), an unbounded `--pages` range (fixed), the char and path scans bounded by matches rather than by rows read (fixed in the repo.12 and repo.14 changes: every character and page object read counts) and unused ledger row kinds (kept: they record the earlier probes). The repo.12 changes: `/codex-review` round 4 approved with 3 medium findings (a fallback accepted without a failed attempt on Alex's key, and a page tree pypdf expanded before its pages were counted; all fixed with tests). Reviewer subagent: 8 FIX, 7 fixed with tests; one did not hold (the gpt-5-mini and gpt-5-nano multipliers match the guide as read on October 12, 2026, now cited in the code), and 1 ESCALATE (section 10). A second reviewer subagent verified those fixes as correct and raised 4 FIX (tests for the unaccepted-fallback count and for rounding in both resize steps, each shown to fail with its fix removed, and wording on what the record marks); all fixed. PR #7 (PR3 slice 1): round 1, reviewer subagent 10 FIX (fixed) and 2 ESCALATE (incomplete sources, since decided by Alex; one signing operation per template or per revision, open in section 10); `/codex-review` 1 high (a route test tied to one router layout) and 3 medium (an existing state directory's mode, bodies read before the configuration check, a storage error after the rename reported as unsaved), all fixed. Round 2: reviewer subagent 6 FIX, all fixed; `/codex-review` 1 high (the local API has no authentication: mitigated by the loopback gate, identity open in section 10) and 3 medium (a found create losing to a concurrent FAILED, the state directory resolved before the link check, retries recognised by content), all fixed. Round 3, the last automatic `/codex-review` round: approve, 0 blocking, 1 medium (the gate trusted headers without checking the peer address; fixed). A verification reviewer subagent confirmed the round 2 and 3 fixes and raised 7 FIX (a create whose replacement failed now takes the slot back, an orphan that cannot be written is named in the error, missing parent folders are synced, wording, two coverage tests), all fixed with tests, and 1 ESCALATE (orphan visibility, section 10). Alex is the approver. |
| Analysis | The app: NOT_IMPLEMENTED. PR2 (draft): `studio.services.analysis` reads the text layer and geometry of every leaf of the page tree (pypdf), renders every page (PDFium), and checks both counts against `/Count`. `COMPLETE` requires every page to be found, read and rendered. Everything the user must look at is flagged. Blocking flags keep the inventory INCOMPLETE: extraction or render failures, a blank render of a page that has a text layer, and document-level gaps (`page: null`) such as a count mismatch or a missing `/Count`. Blank or text-less pages get a non-blocking flag. Every render is bounded in pixels and every PDFium object is closed under its lock. Sources over 25 MiB are rejected before parsing, and the wrapper opens only regular files and never holds an oversize file in memory. A source with more than 400 pages, more than 4,000,000 characters of text, or whose analysis passes 3 minutes fails with a `FAILED` record that names the limit; pages are counted before any is read or rendered, and time is checked between pages. Parser failures become `FAILED` records that keep the source hash. `analyze()` itself never calls a model. Model route (stacked PR): stage A drafts each page from its image, text layer and candidates (underscore runs, drawn rules and checkbox shapes with exact geometry); stage B checks pages that have draft fields, rejected draft fields or candidates, using the same image with the draft drawn on it. A field anchored to a candidate takes the candidate's geometry. Coordinates are measured from the top-left of the page's visible area (the crop box within the media box, as PDFium renders it), and a page whose visible area differs from its media box carries a note. Rotated pages are not sent; they need an audited normalizer, so the run stays PARTIAL and the page is flagged. Invalid fields are rejected with a reason, and a rejected final field keeps the run PARTIAL. A page whose stage B failed has no final fields. Profile PROVISIONAL_DEVELOPMENT. Tested with a fake gateway, and run once on both development cases with `gpt-4.1` (Inference row): PARTIAL, not yet scored. Generator request isolation is tested at the transport (section 5). The comparator checker and its 26 controls are not adopted yet (NOT RUN). |
| Vercel access | Protection applied to all deployments. Alex's signed-in check passed. Named invitations NOT_APPLIED (section 6). |
| Hosted CI | Passed on PR #1, PR #2 and each push to `main`, generic rules only. |
| Private inputs | Mechanics-only runs, no inference. EXECUTED October 11, 2026 on `ea57ac6` plus the uncommitted rendering changes, and again October 12, 2026 on commit `bb63e5c`: `dev-a` 97 pages, `dev-b` 64 pages; both hashes match the intake record; both inventories COMPLETE. `dev-a` page 92 renders blank and has no text layer (non-blocking flag). `dev-a` has 19 parser diagnostics, all on stderr with valid JSON on stdout, which is the real-document check of the stdout regression. |
| Inference | First unmodified run, EXECUTED October 12, 2026 on `e5fdbc9`: `gpt-4.1` on Alex's key with `--accept-fallback`, 4 workers. Every call answered on Alex's key, with no fallback. `dev-a`: preflight passed, 97/97 pages inspected, 155 calls, $0.82 (cap $3). `dev-b`: preflight passed, 64/64 pages inspected, 84 calls, $0.46 (cap $2). Both runs are PARTIAL, not failed: stage B produced 2 fields per case that validation rejected, all for the same reason. Each is a Client text field carrying a merge name (`amount` and `units` in `dev-a`; `homePhone` and `email` in `dev-b`), and only the sender's fields may carry one. Answers are validated, not coerced, so the fields stay rejected. The records are read-only, outside the repository. They have not been scored: the checker waits on reference adjudication and tolerances (section 10). Total spend $1.28 of the $10 cap, including $0.0027 on the earlier text-only smoke test and access probes (ledger outside the repository). Observed: of 9 OpenAI models probed, Alex's key reaches only `gpt-6-astra` and `gpt-4.1`. For the others (`gpt-5`, `gpt-5.2`, `gpt-5.4`, `gpt-5.5`, `gpt-6-sol`, `gpt-6.1-sol`, `gpt-6-luna`) the gateway retries on Vercel's system credentials, so the model route must record the credential each call used. |
| Model route | Before any page is sent, one call with no page content proves the route. Each call names one model and one credential. After the call, the routing metadata must show one model attempt, on that model, answered by OpenAI on that credential; otherwise the run stops as `ROUTE_VIOLATION`. Every attempt, failed or not, must be on OpenAI; a failed attempt on the other credential is recorded in the evidence (section 10). The gateway has no option to refuse its fallback from Alex's key to Vercel's credentials (Vercel documentation, checked October 12, 2026), so a fallback that starts mid-run is seen only after the call, when up to `--workers` page requests (at most 4) may already be in flight (section 10). Each call reserves its worst-case cost before it is sent and settles the market cost afterwards (BYOK calls report $0 at the gateway). A cost that cannot be read counts at its worst case. The caps bound what is reserved: a call that costs more than its reservation is recorded at its real cost and stops the run (`BUDGET_STOPPED`), so recorded spend can pass a cap by at most that call's overrun. Money is exact decimal from the gateway's response to the ledger. A call the gateway refuses (HTTP 4xx other than a timeout) generated nothing: it settles at $0 and stops the run, so a refusal cannot repeat page by page. An unreadable response also stops the run. Timeouts, 5xx and network errors keep their worst case and fail only their page. Models offered: `gpt-4.1`; `gpt-5`, `gpt-5-mini`, `gpt-5-nano`, `gpt-5.2`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.6-sol`, `gpt-5.6-terra` and `gpt-5.6-luna`; `gpt-6.1-sol` and `gpt-6-luna`. `gpt-6-astra` and `gpt-5.5` are not offered. Each page image is reserved at the tokens OpenAI's published rule counts for that image's size on that model (tiles for `gpt-4.1` and `gpt-5`, patches times a multiplier for the rest); models the rule does not list are reserved at the larger of the biggest multiplier and `gpt-4.1`'s tiles. `o4-mini` is offered as a backup only: OpenAI has deprecated it. A BYOK route can accept a fallback to Vercel's credentials (`--accept-fallback`): the run then continues, and each page call that fell back is marked in its evidence. `fallback_calls` counts every call that fell back on a byok run, accepted or not. The run record is created owner-only before any call and made read-only at the end, so a bad `--out` never surfaces after spend. |
| State | PR3 slice 1 (draft, local only): a store where every write is conditional (`put_new` if absent, `replace` only with the etag that was read), on local disk behind the interface an R2 backend would implement. Sources are sealed under their SHA-256 with a page inventory measured in the visible-area frame, and their bytes are re-hashed on every read. A template is a head pointer plus immutable, hash-named revision objects. A save names its base revision, and every save makes a new revision, even with identical content (Alex chose this stricter rule on October 12, 2026). Each save carries a client-chosen `save_id`, and only a retry with the same `save_id`, base and content returns the revision it made, if nothing was saved on top of it; another tab's identical save is stale. A stale base answers 409 with the current template, and a storage failure answers 503 saying the outcome is unknown, never Saved. Review and approval record the revision and its binding (the hash of source, policy and fields); every new revision starts without them, and any other revision or binding is refused as stale. A source whose inventory is `INCOMPLETE` blocks review, approval and create, and its flags travel with the template; a page whose rotation pypdf and PDFium disagree on is recorded as unknown and flagged (without blocking the inventory), and no box can be placed on it. A template's source is fixed. Policy errors (unknown types, a countersigner the policy does not allow, sender on a non-merge field, a page outside the source, rotated or unmeasured pages, boxes off the page) refuse the save; policy issues (an empty map, an unmet requirement) block review, approval and create. The signing adapter is simulated, holds no credentials and has no send; an operation is recorded before the call, an unknown outcome keeps its ID and offers only a status check, and "not found" counts as failed only after a two-minute grace and never for a create the adapter reported. A create the provider holds wins over a concurrent FAILED; one reported or found after its operation was replaced takes the slot back if the replacement failed, and is otherwise kept as an orphan record to resolve by hand; a status check that changes nothing writes nothing. On Vercel nothing is configured, so every state route answers 503 before any body is read. Locally, state routes answer only requests from a loopback peer, addressed to a loopback host and sent from a loopback page (403 otherwise); this is not an identity (section 10). JSON bodies are capped at 1 MiB. The local store refuses a state directory that is a link, someone else's, or open to group or others, and makes each folder it creates, missing parents included, owner-only and synced into its parent. |
| Next | Score the first run with the checker and its 26 controls, once Alex settles reference adjudication and tolerances (section 10). Whether a PARTIAL run counts as failed, and so triggers the `o4-mini` backup, is Alex's call; it has not been run. After this `scope_update`: PR3 `feat/studio-state-boundary`, a P2 feature PR declaring `F-P2-state-boundary`. It is built locally first: a revision store, saved versus working state, review and approval bound to a saved revision, policy validation, a signing adapter with no side effects, and uncertain-operation handling. Direct R2 upload waits on the R2 decisions in section 10. PR3 slice 1, the local state core, is the State row; the R2 backend and direct upload follow once the bucket, a scoped token and CORS are provisioned (section 10). |

## 10. Open decisions

- The assessment time budget, both elapsed and remaining.
- Evaluator-only storage for the baseline. (Decided: the private PDFs are read in place, and more sources are expected in R2.)
- Decided October 11, 2026: pypdf 6.20.0 for text and geometry, pypdfium2 5.14.0 (PDFium) for page images, and `cryptography` 50.0.2 for AES, all BSD or Apache licensed. OSV reported no known vulnerabilities in any of the 29 locked packages on that date. Still open: a native-dependency smoke test on the Vercel runtime (hosted CI already renders on Linux x86_64).
- Decided October 12, 2026 (Alex): source limits of 25 MiB (replacing 50 MiB; uploads are usually about 10 MB), 400 pages, 4,000,000 characters of extracted text and 3 minutes per analysis, enforced in the service. Renders are bounded in pixels. pypdf expands the page tree only to four entries per allowed page, so shared /Pages nodes cannot multiply a small file into millions of pages. Time is checked between pages and once more at the end, so one slow page can overshoot the limit by that page before the analysis fails. The model route's candidate scan reads at most 100,000 characters and 50,000 page objects of any kind per page and stops after 2,000 shapes of each kind; each bound is noted on the page when it applies.
- Open (reviewer escalation): PDFium is held by one lock for a whole inventory, and the time limit starts before that lock is taken. Once the PR3 upload route runs analyses side by side, a small source could fail its time limit while it waits behind another document's render. Should waiting count against a source's 3 minutes, or should the clock start when its render begins? This cannot happen in today's single-run CLI.
- Decided: `COMPLETE` requires page-image evidence, and pages without it are flagged to the user. Applied October 12, 2026: a blank render of a page with a text layer is not evidence and blocks; document-level gaps are flags with `page: null`.
- Decided: the model route is Vercel AI Gateway restricted to OpenAI, using Alex's OpenAI key, with a spend cap of $10 for PR2. Still open: reference adjudication and tolerances.
- Model route, BYOK fallback: the gateway cannot refuse a fallback from Alex's key to Vercel's credentials, and the routing metadata shows one only after the call. No page is sent unless the preflight passes, so a route that is already failing sends nothing. A fallback that starts mid-run can carry up to `--workers` pages (at most 4) on Vercel's credentials before the run stops. This applies only to models Alex's key reaches: of those offered, `gpt-4.1`. The other offered models the October 12 probes reached (`gpt-5`, `gpt-5.2`, `gpt-5.4`, `gpt-6.1-sol`, `gpt-6-luna`) ran on Vercel's credentials (`--credential system`), where there is no fallback; the rest were not probed, and the preflight finds out before any page is sent. Decided October 12, 2026 (Alex): accept the fallback and continue, recording it. `--accept-fallback` (BYOK only) lets a call answered on Vercel's credentials pass; the record marks each such call and counts them in `fallback_calls`. Every other route check still applies.
- Decided October 12, 2026 (Alex): the first unmodified run uses `gpt-4.1` on his key with the fallback accepted; `o4-mini` is the backup if that run fails. OpenAI has deprecated `o4-mini` and scheduled it for shutdown, so it is offered for this backup only. OpenAI's vision guide (read October 12, 2026) marks `gpt-5-mini` and `gpt-5-nano` the same way; they stay offered until Alex decides otherwise.
- Decided October 12, 2026 (Alex): Vercel's credentials may be used where needed. With `--credential system`, the gateway first tries Alex's key, and for models his key cannot reach that attempt fails; each such request reaches OpenAI on his account before Vercel's credentials answer. These failed attempts are recorded in the evidence, not treated as violations.
- Decided October 12, 2026 (Alex): a call the gateway refuses (HTTP 4xx) settles at $0. Because a $0 refusal no longer counts toward the caps, it also stops the run. A timeout (408, 499) is not a refusal and keeps its worst case, as do other failures.
- Decided October 12, 2026 (Alex confirmed): this route reads `source_pt_top_left_72` as the top-left of the visible area, on unrotated pages only. The checker's references must use the same frame before the checker is adopted; this matters only for pages whose crop box differs from the media box.
- Decided October 12, 2026 (Alex): code in the kickoff package and in other checkouts, all written with Claude Code, is reference only and is never copied into this repository. A scan that day of every file PRs #1 to #5 committed found no copied code: the shared lines are imports and standard idioms, and the frame name and merge-field names come from the kickoff guide. Open: whether the kickoff checker runs in place, outside the repository, as an evaluator, or is reimplemented from it.
- P2 revision store: saved revisions as immutable R2 objects with a head pointer written conditionally, so no new service is added (proposed), or a database.
- P2 R2 setup: decided October 12, 2026 (Alex): a new bucket in the akim04 Cloudflare account, beside the existing buckets, which stay untouched. Not provisioned yet: the bucket, a token scoped to it, and CORS for the direct upload. Until then the store and upload path run against a local backend.
- Decided October 12, 2026 (Alex): every save makes a new revision and clears review and approval, even with identical content. Applied under the `COMPLETE` decision: a source whose inventory is `INCOMPLETE` blocks review, approval and create. A template's source is fixed, so a complete source needs a new template.
- Open: whether a template has one signing operation for its whole life, or one per approved revision. Today a created template refuses a second create, so a revision edited and approved after creation cannot be created; the alternative creates it as a new provider template and keeps the earlier one.
- Open (PR #7 review): the local state API has no identity. It answers only requests from a loopback peer, addressed to a loopback host and sent from a loopback page, so other machines (even when the server listens on every interface), other sites' pages and rebound host names are refused, but any process on this machine is answered. That follows the access decision (no custom login, no assumed reviewer identity). Whether the local API needs more, such as a per-launch token, is open; a hosted state route waits on R2 and sits behind Vercel Authentication.
- Open (PR #7 review): an orphan (a create reported or found after its operation was replaced by one that did not fail) is kept under `templates/{id}/orphans/`, but it does not block a new create and `GET .../operation` does not show it, so after the replacement fails a third create is allowed and the provider may hold two. Should an unresolved orphan refuse create, be listed on the operation, or both?
- Named access for seven people: a Pro upgrade or another route, since Hobby allows one external collaborator.
- The test client: adopt `httpx2`, which Starlette 1.7 prefers, or keep `httpx` with the single targeted warning filter.
- Whether to send the design (4) conflicts back to Design, or only reject them at import.
- Whether `pyproject.toml`, `tests/conftest.py` and `tests/test_repo_contract.py` become scope files, or CI runs the scope tests with a fixed configuration (section 8).
- Whether a `scope_update` that changes the workflow, checker or agent rules needs its own kind.
- Whether the owner's full name stays in this guide once the repository's visibility is decided.

| Revision | Change |
|---|---|
| 1.0.14-repo.1 | Repository guide created from kickoff 1.0.14 for the isolated PR1 foundation. |
| 1.0.14-repo.2 | The contract test no longer pins P0. Rehearsing the P1 `scope_update` after the PR1 merge showed CI would fail, and only a feature change could fix it. |
| 1.0.14-repo.3 | `scope_update` to P1, adding `F-P1-analysis`. Records Alex's signed-in check. |
| 1.0.14-repo.4 | PR2 draft: shared analysis service with full page inventory, evaluation wrapper, stdout/JSON tests, and file-isolation tests for the wrapper and service. pypdf is proposed. Review fixes: encrypted page trees, failure records, kept geometry. |
| 1.0.14-repo.5 | Alex's decisions: pypdf, PDFium rendering, `cryptography`, a 50 MB cap, COMPLETE requires page images, and Vercel AI Gateway to OpenAI with a $10 cap. Mechanics-only run on both development cases. |
| 1.0.14-repo.6 | PR #4 review fixes: bounded and closed renders, file-descriptor-level stdout, document-level and blank-with-text flags. Mechanics rerun on `bb63e5c`. Gateway smoke test and access probes. |
| 1.0.14-repo.7 | Model route: gateway client with route checks and a spend ledger, candidates, stages A and B, an overlay for the self-check, a checker envelope, and a transport-level isolation test. No development-case run yet. |
| 1.0.14-repo.8 | PR #5 review fixes: a content-free route preflight, fatal and network errors, overrun stops, exact decimal money, owner-only records, visible-area geometry, rotated pages not sent, rejected final fields keep the run PARTIAL, a bounded candidate scan. Round 2: the run record is reserved before any call, at most 4 workers, every attempt must be on OpenAI, Decimal money end to end, model answers validated not coerced, candidates read only on requested pages. Round 3: no page is sent unless the preflight passes, a page whose scan fails keeps its size, `--pages` is bounded. Four route questions added to section 10. |
| 1.0.14-repo.9 | Alex's decisions of October 12: Vercel's credentials accepted where needed, refused calls settle at $0, the coordinate frame confirmed, `gpt-6-astra` and `gpt-5.5` not offered and the other GPT-5 models added, and kickoff and other-checkout code kept as reference only. Page images are reserved at the high-detail maximum. |
| 1.0.14-repo.10 | `scope_update` to P2, adding `F-P2-state-boundary`. Records the PR #4 merge and two P2 decisions in section 10. |
| 1.0.14-repo.11 | PR3 slice 1: the local state core (State row). Conditional-write store, sealed sources, saved revisions, revision-bound review and approval, policy validation, a simulated signing adapter and uncertain-operation handling. |
| 1.0.14-repo.12 | Alex's later decisions of October 12: `gpt-4.1` on his key for the first run with an accepted, recorded fallback (`--accept-fallback`), `o4-mini` as the backup, and source limits of 25 MiB, 400 pages, 4,000,000 characters and 3 minutes. Page images are reserved at their own size by OpenAI's published rule. The candidate scan is bounded by what it reads. |
| 1.0.14-repo.13 | PR #7 review fixes: every save makes a new revision; incomplete sources block review, approval and create; unknown rotation refuses boxes; a state directory must be real and owner-only; created folders are synced; 503 before any body is read; 1 MiB JSON bodies; 503 says the outcome is unknown. R2 account decided. |
| 1.0.14-repo.14 | Review fixes to repo.12: a fallback passes only after a failed attempt on Alex's key, and every call that fell back is counted, including one that then failed; the ledger records a fallback whether or not it was accepted. The page tree is expanded only to a bound, time is checked after the last page, tile sizes round up, and the path scan counts objects of every kind. gpt-5-mini and gpt-5-nano are deprecated. |
| 1.0.14-repo.15 | PR #7 round 2 fixes: a retry is recognised by its `save_id`, not by content; state routes answer only loopback peers addressing a loopback host from loopback pages (the missing identity is in section 10); a create the provider holds wins over a concurrent FAILED; creates on a replaced operation are kept as orphans; status checks that change nothing write nothing; unknown rotation is flagged; a link given as the state directory is refused; a failed read says to reload. |
| 1.0.14-repo.16 | First unmodified run of `gpt-4.1` on `dev-a` and `dev-b` (Inference row): every call on Alex's key, $1.28, both PARTIAL on 2 rejected merge-name fields each, not yet scored. Verification-review fixes: tests for the unaccepted-fallback count and both resize roundings; wording on what the record marks. Repo.15 is used by PR #7. |
| 1.0.14-repo.17 | PR #6 (carrying PR #5 at repo.16) merged into PR #7, so uploads are capped at 25 MiB. PR #7 round 4 fixes: a create whose replacement failed takes the slot back; an orphan that cannot be written is named in the error; missing parents of the state directory are made owner-only and synced; a retry returns its revision only if nothing was saved on top of it; orphan visibility is open (section 10). PR #7 reviews recorded. |
