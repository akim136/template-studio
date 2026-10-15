---
phase: P4
kind: feature
features: F-P4d-docs
---

# Split the project guide

The guide had grown to 104 KB: most of it was the session record and a revision table that every
pull request edited, so parallel pull requests collided on revision numbers. It now holds only the
rules and the current state (25 KB). The rest moved verbatim:

- `docs/decisions.md`: the dated decisions and the items marked Open or Proposed from section 10;
- `docs/history.md`: the status at repo.57, the access observations from October 11 to 12, 2026,
  the session record (section 9) and the revision table, frozen.

The guide's sections 1 to 8 keep their numbers. Section 9 is now a short current-state table and
section 10 the open questions. Section 6 keeps the current access rules and the release; the
older observations are in the history. Cross-references to the old sections 9 and 10 now point to
the new files, and the stale header line "Vercel Authentication protects all deployments" is
replaced by the current access state.

`docs/phase_change.json` is removed: this note is the declaration. `tests/test_repo_contract.py`
now checks the policy against the note merged last, read with the checker's own parser. "Last"
follows the first-parent history (merge order), not the notes' names, and when one merge brings
several notes the highest phase among them is current. Tests cover a phase advance declared only
in a note, same-day notes whose names sort out of order, and a merged stack. The README points to
the new files.
