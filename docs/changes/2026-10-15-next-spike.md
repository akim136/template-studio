---
phase: P4
kind: feature
features: F-P4f-frontend-tidy
---

# Spike: the library page in Next.js (do not merge)

`spikes/next-frontend/` renders the library in Next.js 16 (App Router) against the studio's JSON API,
to measure what a switch would cost. It is evidence for the frontend decision record, not a change
to the studio: nothing in `app.py`, CI or the Vercel project uses it. No declared feature covers
`spikes/`, so the phase check refuses this branch, and it is never merged.
