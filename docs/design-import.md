# Design import map

The source is `Template Studio Clean.dc.html` v2.3 (SHA-256 `94a7b7b0880e80fbeec83852e47ede7c6535eac1a2ab3c4616f2cab6596c790f`) from archive (4). Section 7 of `PROJECT_GUIDE.md` covers this source's identity and its conflicts.

The export is a React 18 prototype backed by localStorage and fixtures. It serves as an appearance and interaction reference only.
- No row below establishes a backend, model behavior or analysis quality.
- Nothing is imported in PR1.
- Target paths are **proposed** and will be confirmed in the PR that owns each row.
- Any dependency change requires its own approval.

| ID | Export component/file | Decision | Target file (proposed) | PR | API/state owner | Acceptance test | Dependency change |
|---|---|---|---|---|---|---|---|
| D-01 | Layout shell and design tokens (`_ds/…/tokens/styles.css`, inline CSS) | Translate into plain CSS variables | `templates/base.html`, `static/studio.css` | 4 | Presentation only | Pages render with the tokens. No font binaries are served. | None. System font stack unless a licensed font is approved. |
| D-02 | Library screen: tabs and sort (`inTab`), status folding (`SHOWN`) | Translate | `templates/library.html` | 4 | Server template records (PR3) | Rows and tabs come from server records. No fixture rows appear in a real session. | None |
| D-03 | Lifecycle labels (`LIB_STATUS`): Draft, Needs review, Approved, Creating, Verifying, Needs status check, Completed, Failed | Server-side truth, rendered by the UI | `studio/state/lifecycle.py` | 3 (UI 4) | Server | Creating, Verifying, Needs status check and Failed stay distinct, and the library never folds them into an inaccurate label. | None |
| D-04 | Setup: sponsor and signer-policy selection (`applyMode`, "Switch policy" confirm) | Translate. The policy is validated server-side. | `templates/setup.html`, `studio/policy.py` | 4 (rules 3) | Server policy | A policy switch is confirmed and clears confirmation. Required fields come from the case policy. | None |
| D-05 | File intake (`takeFile`, fixture `isFixture` path) | Replace with direct R2 upload through the authenticated adapter | `static/upload.js`, `studio/adapters/storage.py` | 3 (UI 4) | Cloudflare adapter and server | Hash and page count are carried end to end. A new file never shows fixture pages or fields. | Adapter client to be decided in PR3 |
| D-06 | Analyzing screen | Translate, driven by real analysis status | `templates/analyzing.html` | 4 (contract 2) | Analysis service (PR2) | Status reflects a real run, including partial or failed coverage. No sample fields. | None |
| D-07 | Editor canvas: placement, drag, resize, zoom, keyboard nudge, undo/redo | Keep and translate as one small JS module | `static/editor.js`, `templates/editor.html` | 4 | Client working state, server-validated geometry | Source coordinates stay stable across zoom. Pointer and keyboard edits produce equal numeric geometry. | None. No frontend framework. |
| D-08 | Field inspector: type, recipient, required flag, merge name | Translate | `templates/editor.html` | 4 (rules 3) | Server validation | The recipient is explicit for new fields. Merge names come from the whitelist. No new field types. | None |
| D-09 | Save, conflict and discard (`doSave`, `discardChanges`, `persist`, `writeStore`) | Replace localStorage with the server revision store | `studio/state/revisions.py`, `static/editor.js` | 3 (UI 4) | Server | A storage failure never shows Saved. Stale saves are rejected. Discard and reload restore the acknowledged snapshot. | Storage choice decided in PR3 |
| D-10 | Page confirmation (`setPagesConfirmed`, `confirmBinding`, `confirmationValid`) | Re-implement server-side, keeping the export's binding fields: revision, field hash, account, signature mode, mode, document SHA-256 | `studio/state/review.py` | 3 (UI 4) | Server | Consequential edits clear confirmation. Zoom and selection do not. | None |
| D-11 | Approval (`approveReasons`, `commit()`) | Re-implement server-side | `studio/state/review.py` | 3 (UI 4) | Server | A stale or bypassed approval is rejected by a direct API call. An empty map is blocked. | None |
| D-12 | Create, verify and status check (`runCreate`, `checkStatus`, operation ID) | Server-side, with a simulated signing adapter | `studio/adapters/signing.py` | 3 (UI 4) | Server and adapter | An unknown outcome keeps its operation ID and offers only a status check. No duplicate create. No real create or send. | None |
| D-13 | Record view and draft-from-record (`draftFromRecord`) | Translate | `templates/record.html` | 4 | Server | The record reflects server state. A new draft never mutates the saved record. | None |
| D-14 | Login screen, mock "Continue with Vercel", `demo_actor` and signed-in attribution, client `allowed(email)` | **Reject** | — | — | Vercel Authentication (outside the app) | No app login route or client identity check exists. Demo actions are not attributed to a named person. | — |
| D-15 | `fixtures/clean/studio.json` `access.allowlist`, and the suggested server re-check of named emails | **Reject**. The roster stays out of Git and every client. | — | — | — | The private check, using the out-of-repo denylist, finds no roster value in the payload or history. | — |
| D-16 | `model_configuration` fixture, any model selector or model UI | **Reject**. There is no model UI. The route is recorded in the run record, not chosen by the operator. | — | — | Analysis service | No model choice is exposed in templates or APIs. | — |
| D-17 | `qa/scenario-tests.js`, `qa/layout-audit.js`, `scenarios`, `fixtures/clean/*.json`, `assets/clean/page_0N.png`, `apps/app/public/logo_name.svg` | Test-only or deferred. Import only selected synthetic items, with provenance. | `tests/fixtures/` (synthetic) | 3–6 as needed | — | Each fixture is labeled synthetic. None is used as analysis output. | None |

The following are **not imported**:
- `support.js` and `_ds/…/_ds_bundle.js` (runtime bundles);
- `_ds/…/fonts/` (font binaries);
- `uploads/`, which includes a demo PDF and older handoff copies;
- `screenshots/`;
- `baseline/` and `release/` copies;
- the non-Clean `Template Studio.dc.html`;
- the root `fixtures/*.json` used by the non-Clean prototype.
