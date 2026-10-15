# Spike: the library page in Next.js (do not merge)

A throwaway measurement for the frontend decision (`docs/decisions/2026-10-15-frontend-framework.md`
on the docs branch). It is not deployed, not wired into `app.py`, CI or any Vercel setting, and its
directory is ignored by Vercel's root build, which detects only the FastAPI app.

- `app/page.tsx`: the library (`templates/library.html` plus the view logic of `pages.library`) as a
  Server Component. The status tab and tone are copied from `studio/state/lifecycle.py`, because the
  API returns only the label.
- `app/studio.ts`: fetches `/api/templates` and `/healthz` from FastAPI on the server, passing the
  viewer's cookie through.
- `proxy.ts`: today's security headers, with a per-request nonce added to `script-src`, since the App
  Router streams its payload in inline scripts. `SPIKE_CSP=classic` sends today's policy unchanged.
- `next.config.ts`: `/api`, `/static` and `/favicon.ico` are proxied to FastAPI (`STUDIO_API`, read
  at build time), so the browser sees one origin.
- `proxy.ts` also refuses any request whose `Host` or `Origin` is not a loopback address, and the
  scripts bind to 127.0.0.1. FastAPI's local mode checks the peer address, which is always loopback
  once this server sits in front of it, so the proxy has to repeat the check.
- `smoke.py`: starts the studio in local mode, creates three templates and an unreadable one,
  builds and starts this app against it, and compares both library pages in Chromium: the rows
  shown, a casefolded search, a filter, and the page when FastAPI is down.

```
(cd spikes/next-frontend && npm ci)
uv run --locked python spikes/next-frontend/smoke.py
```

Left out on purpose: sign-in (hosted mode), the other pages, and the header's icons.

Known costs a real port would have to address:

- The page fetches every template and filters, sorts and pages them on the Next server, as
  `pages.library` does in Python. A real port would move that into an API view model.
- Every page load also asks `/healthz` for the state mode, uncached, to show the local badge.
- `fold()` imitates Python's `casefold` by upper-casing then lower-casing. It agrees for the cases
  checked (such as "ß"), not for every character. Keeping two copies of the view logic in step is
  the cost the decision record describes.
