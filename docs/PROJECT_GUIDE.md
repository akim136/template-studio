# Homebase Template Studio: project guide

**Revision 1.0.14-repo.6 · October 12, 2026 · Owner: Alex Kim**
**Status:** P0 is complete. The foundation merged through PR #1 and a contract fix through PR #2, both after green checks. PR #3 is the `scope_update` that advances the active phase to P1. This revision is PR2's draft analysis spike, a P1 feature stacked on it.
- Analysis is NOT_IMPLEMENTED in the app. PR2 (draft) adds the shared analysis service: text, geometry, page images and a full page inventory. Field drafting and self-check are NOT_RUN; the model route is next.
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
  - PR2 so far: tests show the service opens no files and the wrapper opens only the source, not decoy evaluator files beside it. Generator request isolation is NOT_RUN, because there is no model client yet.
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
| Phase | P1, set by the `scope_update` in PR #3. P0 ended with PR #1 (foundation) and PR #2 (contract fix), both merged with merge commits after green checks. |
| Reviews | PR1: `/codex-review` approve with 3 medium findings; reviewer subagent 8 FIX and 4 ESCALATE. PR2 (PR #4): `/codex-review` approved each round; round 3 raised a render-budget finding (fixed) and an unused-helper note (kept for the model route). Reviewer subagent round 2: 11 FIX, all fixed with tests; 2 ESCALATE, resolved under Alex's rule that anything incomplete is flagged (section 10). Alex is the approver. |
| Analysis | The app: NOT_IMPLEMENTED. PR2 (draft): `studio.services.analysis` reads the text layer and geometry of every leaf of the page tree (pypdf), renders every page (PDFium), and checks both counts against `/Count`. `COMPLETE` requires every page to be found, read and rendered. Everything the user must look at is flagged. Blocking flags keep the inventory INCOMPLETE: extraction or render failures, a blank render of a page that has a text layer, and document-level gaps (`page: null`) such as a count mismatch or a missing `/Count`. Blank or text-less pages get a non-blocking flag. Every render is bounded in pixels and every PDFium object is closed under its lock. Sources over 50 MiB are rejected before parsing. Parser failures become `FAILED` records that keep the source hash. Stages A and B are NOT_RUN until the model route is wired in. Generator request isolation is NOT_RUN. The comparator checker and its 26 controls are not adopted yet (NOT RUN). |
| Vercel access | Protection applied to all deployments. Alex's signed-in check passed. Named invitations NOT_APPLIED (section 6). |
| Hosted CI | Passed on PR #1, PR #2 and each push to `main`, generic rules only. |
| Private inputs | Mechanics-only runs, no inference. EXECUTED October 11, 2026 on `ea57ac6` plus the uncommitted rendering changes, and again October 12, 2026 on commit `bb63e5c`: `dev-a` 97 pages, `dev-b` 64 pages; both hashes match the intake record; both inventories COMPLETE. `dev-a` page 92 renders blank and has no text layer (non-blocking flag). `dev-a` has 19 parser diagnostics, all on stderr with valid JSON on stdout, which is the real-document check of the stdout regression. |
| Inference | No document content sent. $0.0027 spent on a text-only smoke test and access probes (EXECUTED October 12, 2026; ledger outside the repository). Observed: of 9 OpenAI models probed, Alex's key reaches only `gpt-6-astra` and `gpt-4.1`. For the others the gateway retries on Vercel's system credentials, so the model route must record the credential each call used. |
| Next | The model route: Vercel AI Gateway restricted to OpenAI (Alex's key), a $10 cap enforced in code, stages A and B, and a generator-isolation test on the actual model request. Then the first unmodified run on `dev-a` and `dev-b`. |

## 10. Open decisions

- The assessment time budget, both elapsed and remaining.
- Evaluator-only storage for the baseline. (Decided: the private PDFs are read in place, and more sources are expected in R2.)
- Decided October 11, 2026: pypdf 6.20.0 for text and geometry, pypdfium2 5.14.0 (PDFium) for page images, and `cryptography` 50.0.2 for AES, all BSD or Apache licensed. OSV reported no known vulnerabilities in any of the 29 locked packages on that date. Still open: a native-dependency smoke test on the Vercel runtime (hosted CI already renders on Linux x86_64).
- Source budgets: decided at 50 MiB, enforced in the service (uploads are usually about 10 MB). Renders are bounded in pixels. Page, extracted-text and time limits for the PR3 upload route are still open.
- Decided: `COMPLETE` requires page-image evidence, and pages without it are flagged to the user. Applied October 12, 2026: a blank render of a page with a text layer is not evidence and blocks; document-level gaps are flags with `page: null`.
- Decided: the model route is Vercel AI Gateway restricted to OpenAI, using Alex's OpenAI key, with a spend cap of $10 for PR2. Still open: reference adjudication and tolerances.
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
