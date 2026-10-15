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
- Review opens on a saved revision, becomes unavailable after a further unsaved edit, and opens
  again once that edit is saved.

Every test fails on a console error, an uncaught exception, an error answer or a failed request,
except the answer the editor expects (`GET …/operation` answers 404 before any create) and requests
cancelled by navigation. Only the studio is reachable:

- the browser refuses requests to any other origin, navigations included (the pages' content
  security policy already covers fetches);
- the server runs through `tests/e2e/serve.py`, which installs the loopback-only socket guard
  (`tests/e2e/guard.py`) in its process;
- the suite's own no-network guard is narrowed to the same loopback guard for these tests only.

`tests/e2e/test_guards.py` checks each guard. The checks were also confirmed to fail on purpose:
without the 404 allowance, with a JavaScript error injected into `static/editor.js`, without the
origin rule, without the failed-request listener, without the server's guard, and with the
unsaved-changes condition removed from review in `static/editor.js`.

The tests skip unless `STUDIO_E2E=1`; CI's browser job sets it and installs Chromium. Playwright
1.64.0 is a new dev dependency (with greenlet 3.5.6 and pyee 14.0.0); OSV reported no known
vulnerabilities in any of them on October 15, 2026. The README lists the commands.
