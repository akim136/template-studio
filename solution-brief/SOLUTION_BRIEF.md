# Homebase Template Studio

**Signing templates. Ready for the whole team.**

From sponsor PDF to reviewed template draft in one shared workspace. AI proposes the fields, while Homebase operations reviews and approves the exact saved revision.

**Audience:** Homebase's internal operations team. **Assessment scope:** Synthetic inputs, Sign in with Vercel access, and simulated signing. No real signing template is created.

**Application:** [studio.akbuilds.dev](https://studio.akbuilds.dev/) (Sign in with Vercel required)  
**Solution brief:** [akbuilds.dev](https://akbuilds.dev/) (public; `www` redirects here)  
**Source:** [github.com/akim136/template-studio](https://github.com/akim136/template-studio) (public)

## 1. Customer problem

**Current workflow:** A founder operates Claude skills, Python document scripts and chat-based checks, then hands off through existing Cloudflare and Dropbox Sign services. This makes template preparation difficult to delegate and repeat consistently.

**Proposed outcome:** A teammate uploads a sponsor PDF, reviews AI-proposed fields directly on the document, saves an immutable revision and explicitly approves that exact revision. The existing production signing system remains separate.

**Value:** Less founder involvement, clearer corrections, auditability and controlled preparation. These business benefits are pilot hypotheses, not measured savings.

## 2. Current and implemented architecture

**Current:** Founder + Claude skill → Python extraction, interpretation and overlays → existing Cloudflare storage/signing Worker → Dropbox Sign and Homebase's existing workflow.

**Template Studio:** Internal operator → Sign in with Vercel (identity) → Vercel Functions (Python 3.13/FastAPI, Jinja, plain JS) → pypdf/PDFium processing → AI Gateway to OpenAI for page-by-page Stage A drafts and Stage B overlay self-checks → visual editor → revision-bound human approval → *in-app simulated signing adapter*.

Cloudflare **R2** holds sealed PDFs, artifacts, immutable revisions, head pointers, spend records and orphan records. Writes use conditional operations. The browser uploads to R2 using presigned URLs, then the app re-hashes input. Production and previews share one synthetic-only bucket, reached with a token scoped to that bucket alone. A local directory is used in local mode, which cannot draft.

**Boundary decision:** Keep R2 for storage and state. No D1, new Cloudflare signing Worker, Vercel Sandbox, real signing integration or server-side drafting queue is implemented in the app.

## 3. Vercel choices

| Primitive | Implementation rationale | Trade-off |
|---|---|---|
| **Functions** | Hosts the existing Python/FastAPI application and per-page parsing/drafting APIs. | Bounded execution, with browser-driven orchestration instead of a durable server queue. |
| **AI Gateway** | Calls OpenAI using a per-request Vercel OIDC token. The server checks the route and enforces a $20 spend cap. | The application owns model prompts, evidence, validation and measured quality. The model is fixed in code (`gpt-5.4-mini`), with no model selector. |
| **Preview Deployments** | Branch builds support code and UX review before release. | Previews are not behind Vercel Authentication; they share production's Sign in with Vercel app and synthetic-only R2 bucket. Separate preview data and credentials are still needed. |

Vercel Authentication is off for production and previews. Sign in with Vercel (OAuth/PKCE) provides identity, the verified email recorded on each review and approval, not authorization: there is no allowlist, so any Vercel account can sign in. That is why the hosted studio stores synthetic documents only. Fluid Compute is enabled, but an actual performance benefit is not verified.

## 4. Demo instructions

1. Sign in to the application with a Vercel account. Enter a sponsor name, title and signers, then upload a synthetic PDF.
2. On **Analyzing**, click **Start drafting**. Watch each page drafted, then checked. Page-level drafting proceeds with up to four concurrent requests while any Studio page is open.
3. Review the AI overlays in the editor. Correct one field, save and reopen the revision.
4. Confirm **I reviewed all N pages of revision R, including pages without fields**. If AI drafting left a page unchecked (a rotated page, for example), tick **I checked page N by hand** for each one first; approval is refused until every such page is confirmed. Approve, then make another change to demonstrate automatic approval invalidation.
5. Reapprove and simulate template creation. Watch the create and verify steps. No HelloSign/Dropbox Sign API request is sent.

Application source: `main` at `527b3ca`, with PRs #13–#29 and #31 merged as of **October 15, 2026**; its application code is the validated commit `b4a7dfd`. Local, preview and production evidence is listed separately in section 8.

## 5. Rollout

**Preview:** Synthetic-only environment, Sign in with Vercel and simulated signing. **Validate:** Compare the same documents and rubric, assess quality, concurrency, persistence and failure states. **Canary:** One internal teammate, one supported document family, with the founder-operated process available as fallback. **Cutover:** Make Studio the internal preparation default after supervised acceptance, and integrate live signing only through separately approved work. **Rollback:** Stop new Studio processing, preserve R2 state, and return to the old process; reconcile external actions if live signing is ever enabled.

## 6. Success measures

**Technical:** Supported-page coverage, field/role/placement correctness, reliable conditional writes, approval enforcement, recoverability, latency, retries and model spend. **Business:** Operator independence, lower manual correction effort, fewer interrupted handoffs and lower cost per accepted draft with unchanged reviewed quality.

**Pilot gate:** Zero critical missed signatures, signer swaps or approval bypasses in the agreed evaluation cases. One teammate must complete the workflow unaided. AI draft quality, self-checked quality, and human-final quality are measured separately. These gates are proposed, not yet achieved.

## 7. Limitations and risks

- **Input boundaries:** 25 MiB, 400 pages, 4 million text characters and a 3-minute processing limit. Rotated pages are not sent to the model, so each must be confirmed as checked by hand before approval.
- **Draft continuity:** No local drafting or server queue. A browser tab must keep work moving; takeover by another tab has been tested in a local harness. A draft records its model route and prompt hash; if a deployment changes either, the draft stops at its next step ("drafting was updated"), keeps its finished pages, and the operator starts a new draft. Drafting does not carry on across such a deployment.
- **AI quality:** Two development cases returned `PARTIAL` on `gpt-4.1` and `COMPLETE` on backup `gpt-5.4-mini`. Scored against UNREVIEWED proposed reference fields (baseline-derived, so agreement rather than accuracy), both models still miss required signing fields and assign wrong signers, so the checker blocks both. Geometry tolerances are placeholders, so these scores stay provisional.
- **Access and storage:** Sign in with Vercel identifies each operator but does not authorize: any Vercel account can sign in, use the demo and spend from the shared $20 model cap, so the hosted studio holds synthetic documents only. An account outside the team was refused on October 12, while Vercel Authentication was on; it is now off. Named reviewer access is not set up. Previews share production's synthetic-only R2 bucket. R2 conditional writes do not replace broader tenant governance.
- **Signing:** The in-app adapter simulates create and read-back only. The “Open in signing provider” link goes to the provider's homepage and sends no template there. Customer self-service needs additional authentication, tenant isolation, quotas, indexing, queueing and production signing integration.

## 8. Build and validation

**Claude Design:** Template Studio Clean v2.3 prototype, visual language and interaction handoff.  
**Claude Code:** Application implementation, debugging, integration and tests.

**Local (October 15, 2026, `b4a7dfd`):** 698 pytest tests, Ruff and the repository's privacy and phase checks pass, and GitHub CI is green on each merged PR. A local browser harness with synthetic PDFs and fake R2/Gateway exercised tab takeover, sign-out cleanup, malformed state, retry eligibility, save retry after a storage failure, the signing-provider redirect and completion-step timing.

**Preview (October 15, 2026, `b4a7dfd`):** signed in, a synthetic 2-page PDF went through upload, a fresh draft, an edit, save, reload, review, approval, invalidation by a later save, and simulated create; a 3-page PDF whose rotated page AI left unchecked could not be approved until that page was confirmed by hand; a file that is not a PDF was refused.

**Production (`527b3ca`):** signed in on studio.akbuilds.dev after the merges, the same synthetic PDFs went through a fresh draft, edit, save, reload, review, approval, invalidation by a later save, and simulated create. The rotated page had to be confirmed by hand, and a later save kept that record. A second tab on the same draft did not save it twice. Library filters, sign-out and sign-in, and the non-PDF refusal also passed, with no runtime errors logged.

**Not run:** a signed-in check from an account outside the team; hosted failure injection (save retry and storage errors are covered locally); hosted layout at 1024 and 390 px (checked locally at exact widths on the same code).

**AI evaluation:** The same `studio.services.analysis` powers the app and `eval/`. Stage A proposes fields using PDF image/text/candidate evidence; Stage B checks the drawn overlay against the page. Keep initial suggestions, AI self-checks and human corrections distinct, with gold references outside the model's generation environment. A head-to-head with the earlier claude.ai skill workflow on held-out documents is in progress; no result yet.

**Still to do:** named reviewer access before any real document, separate preview data and credentials, agreed geometry tolerances, and live signing only through separately approved work.

*Release snapshot of October 15, 2026, checked against `main` at `527b3ca` and its project guide. Signed-in checks ran in Alex's signed-in browser, driven by the coding session, which also ran the tests.*
