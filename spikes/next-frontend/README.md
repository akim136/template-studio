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
- `smoke.py`: starts the studio in local mode, creates two templates, builds and starts this app
  against it, and compares both library pages in Chromium.

```
(cd spikes/next-frontend && npm ci)
uv run --locked python spikes/next-frontend/smoke.py
```

Left out on purpose: sign-in (hosted mode), the other pages, and the header's icons.
