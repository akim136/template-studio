# Frontend framework (October 15, 2026)

**Status:** proposed. Alex decides. The guide's decision D2 and `AGENTS.md` exclude a framework
switch and a Node or Next.js backend, so adopting Next.js would start with a `scope_update` that
changes them.

**Question:** should the studio's frontend move to Next.js, stay as it is, or move to something
lighter?

**Recommendation:** stay with server-rendered pages and classic scripts for now, and add type
checking of those scripts without a build step. Next.js would add about ten times the page
script, a second runtime and a second copy of the view logic. Most of what it would gain is in one
page, the field editor.

## The frontend today

- **Pages.** FastAPI renders six Jinja pages (`templates/`, 620 lines) from view logic in
  `studio/ui/pages.py` (381 lines): the library, setup, drafting, the record, the editor and a
  message page.
- **Scripts.** Four classic scripts (`static/`, about 2,100 lines; `editor.js` is 1,055 of them)
  share their helpers through `static/studio.js`. There is no build step. There is one stylesheet
  (585 lines).
- **Security headers.** Each page sends:
  - `script-src 'self'` with no inline script, `style-src 'self'`, `img-src 'self' data:`;
  - `connect-src` for this origin, plus the R2 host for direct uploads;
  - `frame-ancestors 'none'` and `no-store`.
- **Identity.**
  - Hosted, people sign in with Vercel. FastAPI reads the session cookie and redirects a page
    request without one to `/auth/login`.
  - A change must carry the studio's own `Origin`.
  - Local mode answers loopback requests only.
- **Hosting.** One Vercel project detects the FastAPI app at the root, with no `package.json`.
  Drafting uses the OIDC token Vercel sends with each function request.
- **Tests.** About 50 `TestClient` tests in `tests/ui` (pages, editor, drafting, icons) check the
  HTML the server returns. The browser tests in `tests/e2e` drive the pages through text and roles.

## What the spike measured

The spike is the library page in Next.js 16.4.2 (App Router, React 19.3.1), on branch
`spike/next-frontend` under `spikes/next-frontend/`. It runs against the existing JSON API. It was
measured locally on one Mac, with a production build and two templates, by `smoke.py` in the
spike. Both frontends rendered the same rows. Next to each other, the pages looked the same,
except that the spike leaves out the header's icons.

| | Library today (FastAPI) | Library in Next.js |
|---|---|---|
| Script the browser runs | 1 file, 12.9 KB (4.5 KB gzip) | 6 files, 456 KB (132 KB gzip), plus 11 KB inline |
| Median HTML response, 30 requests | 1.0 ms | 4.3 ms (it makes two API calls first) |
| Install | nothing new | 30 npm packages, 375 MB on disk (the SWC compiler binary alone is 96 MB) |
| Build | none | 2.8 s warm, 5.3 s cold |
| Today's CSP, unchanged | works | 3 inline scripts blocked; the page never becomes interactive |
| CSP with a per-request nonce | not needed | works, no console errors |

**What it showed:**

1. **CSP.** The App Router streams its payload in inline scripts. Keeping `script-src` strict needs
   a nonce made per request in `proxy.ts`, and every page then renders on demand. That is
   acceptable here, since every page is `no-store` already. Without the nonce, the filter control
   was dead.
2. **Two copies of the view logic.**
   - The API returns a status label, not its tab or chip colour. The spike had to copy those from
     `studio/state/lifecycle.py`.
   - It also had to port the library's filtering, sorting and paging from `pages.py`.
   - A switch either moves each page's view model into the API (new endpoints, written in Python
     and tested there) or keeps two copies in step.
3. **Identity.** Next cannot read the session cookie without sharing the session secret. The
   header's name and email need a small endpoint such as `GET /api/me`. Hosted mode was not
   exercised.
4. **Configuration.** Next fixes rewrite destinations when it builds. The API's address is
   therefore a build setting, or the proxying moves into code that runs per request.
5. **Hosting.** Vercel documents *services*: several apps in one project, with `vercel.json`
   rewrites choosing a service by path, and bindings for server-to-server calls.
   - That keeps one origin, so the cookie and the `Origin` check still work.
   - It allows a page-by-page move: one rewrite per migrated path.
   - `/api` requests from the browser would still reach FastAPI directly, so drafting's OIDC
     token is unaffected.
   - None of this was tried, because nothing was deployed. Adding `vercel.json` services also
     replaces the zero-config detection the project relies on today.
6. **Tests.**
   - The browser tests can carry over if the markup keeps its text and roles. Their server
     fixture would have to build and start Next, so the browser job needs Node.
   - The `TestClient` page tests would be rewritten as browser or component tests.

## Options

| | A. Stay classic, keep tidying | B. A small library, no build step | C. Next.js, page by page |
|---|---|---|---|
| What | Today's pages; `// @ts-check` with JSDoc, and `tsc --noEmit` in CI as a checker only | htmx for the server-rendered pages, or Preact with `htm` for the editor, served from `static/` | Next.js as a second Vercel service; the pages move one at a time |
| Runtimes | one | one | two (Python and Node) |
| Page script | as today | plus about 5 to 15 KB gzip | plus about 130 KB gzip per page |
| CSP | unchanged | unchanged (no inline script) | per-request nonce |
| Work | small | medium, mostly the editor | large: six pages, 2,100 lines of script, view models in the API, `/api/me`, Node in CI, about 50 tests |
| Upkeep | as today | one vendored library | two dependency trees, plus Next and React security releases |
| Changes D2 | no | no (D2 allows limited JS) | yes |

## Recommendation

**Choose A now.** The studio has six pages, five of which mostly show saved state. All are
rendered on the server under a strict CSP. Next.js would cost a second runtime, about 130 KB of
page script and two copies of the view logic. What it buys, a component model and typed UI code,
matters mostly for one page, the editor. Type checking the existing scripts gets much of the
typing benefit without a build.

**What would change this:**

- **The editor needs much richer interaction**, such as multi-select, undo history, dragging across
  pages or several people editing at once. Then move the editor to option B (Preact, no build)
  first, or to C.
- **The studio must share UI with a React or Next.js product**, or a React team takes it over.
  Then choose C.
- **A preview proves Vercel services for this project:** one origin, the session cookie, the
  `Origin` check, drafting's OIDC token and the nonce CSP. That proof is a precondition for C,
  not a reason for it.

## If Alex chooses Next.js anyway

1. **Scope.** A `scope_update` that:
   - changes D2 and `AGENTS.md`;
   - declares the frontend feature, with its directory and `vercel.json` as paths;
   - adds Node to the browser job.
2. **API first, in FastAPI.** Add `GET /api/me`. Return each page's view model, or at least the
   status tab and tone. The Jinja pages keep working throughout.
3. **Hosting.** Set up `vercel.json` services: FastAPI keeps `/api`, `/auth`, `/healthz` and
   `/static`, and one page path goes to Next. Prove the points above on a preview before moving a
   second page.
4. **Pages, one pull request each.** In order:
   - the library (done in the spike);
   - the record;
   - setup, including the direct R2 upload;
   - drafting, with its step loop;
   - the editor, in two or three pull requests.

   Each pull request moves one rewrite and deletes that page's template, script and HTML tests.
   The browser journey must be green before and after.
5. **Last,** remove Jinja, `pages.py` and `static/*.js`.

That is about 8 to 10 pull requests, plus a preview check of the hosting.
