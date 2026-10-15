---
phase: P4
kind: feature
features: F-P4d-docs
---

# Frontend framework: decision record

`docs/decisions/2026-10-15-frontend-framework.md` sets out whether the studio's frontend should
move to Next.js, stay as server-rendered pages with classic scripts, or take a small no-build
library. It includes measurements from a spike of the library page in Next.js (branch
`spike/next-frontend`, never merged) and a page-by-page migration plan. The record recommends
staying as is and adding type checking of the scripts. It changes no code and no rule. D2 and
`AGENTS.md` stay as they are unless Alex decides otherwise.
