"""The lifecycle label of a template, derived from its saved head and its signing operation.

The library shows each template's exact label and only groups labels into tabs. Creating,
Verifying, Needs status check and Failed stay distinct (guide §4), and an open operation outranks
anything a later save did, because it blocks every create until it settles:
- CREATING, VERIFYING or NEEDS_STATUS_CHECK, for any revision: that label.
- CREATED for the current revision: Completed. FAILED for the current revision: Failed.
- Otherwise, from the current revision: Approved, Needs review when it has fields, or Draft.
A CREATED or FAILED operation for an earlier revision does not describe the current one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Status:
    label: str
    tab: str  # the library tab that lists it
    tone: str  # chip colour: gray, yellow, blue, green or red


DRAFT = Status("Draft", "drafts", "gray")
NEEDS_REVIEW = Status("Needs review", "review", "yellow")
APPROVED = Status("Approved", "review", "blue")
CREATING = Status("Creating", "review", "blue")
VERIFYING = Status("Verifying", "review", "blue")
NEEDS_STATUS_CHECK = Status("Needs status check", "failed", "yellow")
COMPLETED = Status("Completed", "completed", "green")
FAILED = Status("Failed", "failed", "red")
UNREADABLE = Status("Unreadable", "failed", "red")

OPEN = {"CREATING": CREATING, "VERIFYING": VERIFYING, "NEEDS_STATUS_CHECK": NEEDS_STATUS_CHECK}
SETTLED = {"CREATED": COMPLETED, "FAILED": FAILED}
TABS = (
    ("all", "All templates"),
    ("drafts", "Drafts"),
    ("review", "Needs review"),
    ("completed", "Completed"),
    ("failed", "Failed"),
)


def status(summary: dict) -> Status:
    """`summary` is a row from Templates.summaries."""
    if summary.get("error"):
        return UNREADABLE
    operation = summary.get("operation")
    if operation is not None:
        if operation["state"] in OPEN:
            return OPEN[operation["state"]]
        if operation["revision"] == summary["revision"] and operation["state"] in SETTLED:
            return SETTLED[operation["state"]]
    if summary["approval"] is not None:
        return APPROVED
    return NEEDS_REVIEW if summary["field_count"] else DRAFT
