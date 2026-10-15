---
phase: P4
kind: feature
features: F-P4g-inference
---

# Stage B checks only pages with a draft

Stage B, the self-check, ran on every page where stage A drafted a field or rejected one, and also
on every page with candidates (underscore runs, drawn rules or checkbox shapes). It now runs only
where stage A drafted or rejected a field. A page with candidates but an empty draft is finished by
stage A, as a page without candidates already was. Its stage B status is `NOT_NEEDED: stage A
drafted no fields and rejected none`, and it counts as inspected, because stage A's answer for it
was received and parsed. This supersedes the session record's Analysis row, which still lists
candidates as a reason to check a page.

One predicate, `inference.needs_check`, decides this for both callers: the CLI and evaluation
runner (`Runner.stage_b`) and the hosted draft steps (`studio/state/drafts.py`), where a page that
needs no check is finished by its stage A step and leaves no stage B step behind.

## Evidence (offline, no model calls)

An exact per-page replay of 26 existing runs (dev-a, dev-b and the synthetic document, with
`gpt-5.4-mini` and `gpt-5.6-luna`), scored with the frozen checker rules of October 12, 2026. A page
the rule sends to stage B keeps stage B's answer; any other page keeps stage A's draft.

| Rule | Criticals | Fix-ups | Stage B pages | Total cost |
|---|---|---|---|---|
| Before this change | 160 | 911 | 792 | $6.35 |
| Pages with drafted fields | 160 | 911 | 199 | $5.26 |
| No stage B | 162 | 975 | 0 | $3.87 |

- Scores were identical in every group (all runs, each model, the synthetic document).
- Stage B spend fell 44% ($2.48 to $1.39): 38% on `gpt-5.4-mini`, the hosted route, and 51% on
  `gpt-5.6-luna`.
- The references are baseline-derived and the tolerances are placeholders, so the scores measure
  agreement, not accuracy.
- The replay could not see rejected fields, because run records do not give their page. A page
  whose draft had only rejected fields therefore still goes to stage B. Such pages are among the
  593 the replay showed changed no score, so keeping them cannot change the replayed scores.
- No live run tested this change.

## What it gives up

Stage B can no longer add a field to a page where stage A drafted nothing. In the replay that never
changed a score, and the review confirmation still covers every page.

A hosted draft started before this change and continued after it mixes the two rules. Its frozen
configuration (`prompts_sha256`) covers what the model is told, not this rule, so the draft
continues, and each page's record names its own stage B status.
