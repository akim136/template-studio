# Agent rules: Homebase Template Studio

The canonical guide is `docs/PROJECT_GUIDE.md`. Read it, `docs/phase_scope.json` and
`docs/phase_change.json` before changing anything.

- Work only in the active phase. Keep one branch and one diff per PR stage. Stop after the phase's
  diff and tests. Never auto-merge or advance a phase.
- Stack: Python 3.13, uv, FastAPI, Jinja/HTML/CSS and limited JS. No Node/Next.js backend and no
  framework switch. Use `--locked` uv commands.
- Before every commit, stage explicit paths and run
  `python3 scripts/check_repo.py private --staged --denylist <out-of-repo file>`.
- Never commit private PDFs, baseline or evaluator material, access-roster details, secrets, font
  binaries or design-archive bundles. Use synthetic test data.
- The generating model must never receive baseline outputs, field recipes, overlays, annotations or
  evaluator reports.
- Report truthfully. `/healthz` reports what analysis does (`ANALYSIS_STATUS` in `app.py`), and
  named Vercel invitations are `NOT_APPLIED`; a PR changes either only with evidence. Label
  commands EXECUTED / NOT RUN / PROPOSED.
- Without Alex's explicit authorization: no push or new remote, no deploy or cloud change, no
  protection change or bypass link, no invitations, no model inference spend, no production or
  other-repository changes.
