# Homebase Template Studio

**Signing templates. Ready for the whole team.**

From sponsor PDF to reviewed template draft in one shared workspace. AI proposes the fields, while Homebase operations reviews and approves the exact saved revision.

**Audience:** Homebase's internal operations team. **Assessment scope:** Synthetic inputs, protected reviewer access, and simulated signing. No real signing template is created.

**Application:** [studio.akbuilds.dev](https://studio.akbuilds.dev/) (Vercel sign-in required)  
**Solution brief:** [akbuilds.dev](https://akbuilds.dev/) (public; `www` redirects here)  
**Source:** [github.com/akim136/template-studio](https://github.com/akim136/template-studio) (private; reviewer access is granted separately)

## 1. Customer problem

**Current workflow:** A founder operates Claude skills, Python document scripts and chat-based checks, then hands off through existing Cloudflare and Dropbox Sign services. This makes template preparation difficult to delegate and repeat consistently.

**Proposed outcome:** A teammate uploads a sponsor PDF, reviews AI-proposed fields directly on the document, saves an immutable revision and explicitly approves that exact revision. The existing production signing system remains separate.

**Value:** Less founder involvement, clearer corrections, auditability and controlled preparation. These business benefits are pilot hypotheses, not measured savings.

## 2. Current and implemented architecture

**Current:** Founder + Claude skill → Python extraction, interpretation and overlays → existing Cloudflare storage/signing Worker → Dropbox Sign and Homebase's existing workflow.

**Template Studio:** Internal operator → Vercel Authentication + Sign in with Vercel → Vercel Functions (Python 3.13/FastAPI, Jinja, plain JS) → pypdf/PDFium processing → AI Gateway to OpenAI for page-by-page Stage A drafts and Stage B overlay self-checks → visual editor → revision-bound human approval → *in-app simulated signing adapter*.

Cloudflare **R2** holds sealed PDFs, artifacts, immutable revisions, head pointers, spend records and orphan records. Writes use conditional operations. The browser uploads to R2 using presigned URLs, then the app re-hashes input. A local directory is used in local mode, which cannot draft.

**Boundary decision:** Keep R2 for storage and state. No D1, new Cloudflare signing Worker, Vercel Sandbox, real signing integration or server-side drafting queue is implemented in the app.

## 3. Vercel choices

| Primitive | Implementation rationale | Trade-off |
|---|---|---|
| **Functions** | Hosts the existing Python/FastAPI application and per-page parsing/drafting APIs. | Bounded execution, with browser-driven orchestration instead of a durable server queue. |
| **AI Gateway** | Calls OpenAI using a per-request Vercel OIDC token. The server checks the route and enforces a $10 spend cap. | The application owns model prompts, evidence, validation and measured quality. The model is fixed in code (`gpt-5.4-mini`), with no model selector. |
| **Preview Deployments** | Branch builds support code and UX review before release. | Previews are protected, but share production's R2 bucket and Sign in with Vercel app. Isolated preview data and credentials are still needed. |

Vercel Authentication protects preview and production deployments. Sign in with Vercel (OAuth/PKCE) records review and approval identity. Fluid Compute is enabled, but an actual performance benefit is not verified.

## 4. Demo instructions

1. Sign in on the protected application using a permitted account. Enter a sponsor name, title and signers, then upload a synthetic PDF.
2. On **Analyzing**, click **Start drafting**. Watch each page drafted, then checked. Page-level drafting proceeds with up to four concurrent requests while any Studio page is open.
3. Review the AI overlays in the editor. Correct one field, save and reopen the revision.
4. Confirm **I reviewed every field on revision N** and approve. Make another change to demonstrate automatic approval invalidation.
5. Reapprove and simulate template creation. Watch the create and verify steps. No HelloSign/Dropbox Sign API request is sent.

Application source: `main` at `a15cac6`, with PRs #13–#19 merged and PR #20 (`feat/completion-steps`) still open as of **October 12, 2026**. Signed-in production checks covered sign-in, a 400-page upload, review and approval. Hosted drafting was checked on a preview with a synthetic PDF; a drafting run on production is still to do.

## 5. Rollout

**Preview:** Protected, synthetic environment and simulated signing. **Validate:** Compare the same documents and rubric, assess quality, concurrency, persistence and failure states. **Canary:** One internal teammate, one supported document family, with the founder-operated process available as fallback. **Cutover:** Make Studio the internal preparation default after supervised acceptance, and integrate live signing only through separately approved work. **Rollback:** Stop new Studio processing, preserve R2 state, and return to the old process; reconcile external actions if live signing is ever enabled.

## 6. Success measures

**Technical:** Supported-page coverage, field/role/placement correctness, reliable conditional writes, approval enforcement, recoverability, latency, retries and model spend. **Business:** Operator independence, lower manual correction effort, fewer interrupted handoffs and lower cost per accepted draft with unchanged reviewed quality.

**Pilot gate:** Zero critical missed signatures, signer swaps or approval bypasses in the agreed evaluation cases. One teammate must complete the workflow unaided. AI draft quality, self-checked quality, and human-final quality are measured separately. These gates are proposed, not yet achieved.

## 7. Limitations and risks

- **Input boundaries:** 25 MiB, 400 pages, 4 million text characters and a 3-minute processing limit. Rotated pages are not sent to the model.
- **Draft continuity:** No local drafting or server queue. A browser tab must keep work moving; takeover by another tab has been tested in a local harness.
- **AI quality:** Two development cases returned `PARTIAL` on `gpt-4.1` and `COMPLETE` on backup `gpt-5.4-mini`. Scored against reviewed reference fields, both models still miss required signing fields and assign wrong signers, so the checker blocks both. Geometry tolerances are placeholders, so these scores stay provisional.
- **Access and storage:** Signed-in production checks passed and an account outside the team was refused. Named reviewer invitations are not yet sent. Previews share production's R2 bucket. R2 conditional writes do not replace broader tenant governance.
- **Signing:** The in-app adapter simulates create and read-back only. The “Open in signing provider” link goes to the provider's homepage and sends no template there. Customer self-service needs additional authentication, tenant isolation, quotas, indexing, queueing and production signing integration.

## 8. Build and validation

**Claude Design:** Template Studio Clean v2.3 prototype, visual language and interaction handoff.  
**Claude Code:** Application implementation, debugging, integration and tests.

**Executed checks:** On `main` at `a15cac6`, 673 pytest tests and Ruff pass, and GitHub CI is green. A local browser harness with synthetic PDFs and fake R2/Gateway exercised tab takeover, sign-out cleanup, malformed state, retry eligibility, the signing-provider redirect and completion-step timing. These are not signed-in production end-to-end results.

**AI evaluation:** The same `studio.services.analysis` powers the app and `eval/`. Stage A proposes fields using PDF image/text/candidate evidence; Stage B checks the drawn overlay against the page. Keep initial suggestions, AI self-checks and human corrections distinct, with gold references outside the model's generation environment.

**Still to do:** Decide PR #20, run hosted drafting on production, grant named reviewer access, complete the `studio.akbuilds.dev` callback and R2 origin, and record the final submission commit. Re-run the customer-flow validation after those changes.

*Implementation snapshot of October 12, 2026, checked against `main` at `a15cac6` and its project guide. Signed-in production checks were run by Alex; the coding session ran the tests.*
