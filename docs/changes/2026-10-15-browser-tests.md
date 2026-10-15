---
phase: P4
kind: feature
features: F-P4e-browser-tests, F-P4d-docs
---

# Browser tests

Until now no test ran the studio's JavaScript. `tests/e2e` drives a real Chromium (Playwright)
against the studio served by uvicorn in local mode, on synthetic PDFs from
`tests/analysis/pdf_factory.py`. Each test gets its own server on a loopback port and an empty state
directory, and the server's environment holds no hosted setting.

- A three-page PDF goes through setup (demo sponsor, upload until sealed, one-signer mode),
  the record, and the editor: the page image loads, a signature is placed and saved as revision 2,
  survives a reload, and review, approval and the simulated create finish with "Template
  created"; the library then lists the template.
- Setup refuses a file that is not a PDF and keeps "Create draft" disabled.
- Review stays unavailable while the editor has unsaved changes.

Every test fails on a console error, an uncaught exception or an error answer, except the one the
editor expects (`GET …/operation` answers 404 before any create). The suite's no-network guard is
narrowed to loopback for these tests only. Both checks were confirmed to fail on purpose: without
the 404 allowance, and with a JavaScript error injected into `static/editor.js`.

The tests skip unless `STUDIO_E2E=1`; CI's browser job sets it and installs Chromium. Playwright
1.64.0 is a new dev dependency (with greenlet 3.5.6 and pyee 14.0.0); OSV reported no known
vulnerabilities in any of them on October 15, 2026. The README lists the commands.
