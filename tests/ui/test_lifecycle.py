import pytest

from studio.state import lifecycle, sources
from studio.state.store import StorageError
from tests.state.helpers import POLICY, field, two_page_pdf


def summary(**overrides):
    base = {"revision": 3, "approval": None, "field_count": 2, "operation": None, "error": None}
    return {**base, **overrides}


def operation(state, revision=3):
    return {"state": state, "revision": revision}


@pytest.mark.parametrize(
    ("row", "label", "tab"),
    [
        (summary(field_count=0), "Draft", "drafts"),
        (summary(), "Needs review", "review"),
        (summary(approval={"revision": 3}), "Approved", "review"),
        (summary(operation=operation("CREATING")), "Creating", "review"),
        (summary(operation=operation("VERIFYING")), "Verifying", "review"),
        (summary(operation=operation("NEEDS_STATUS_CHECK")), "Needs status check", "failed"),
        (summary(operation=operation("CREATED")), "Completed", "completed"),
        (summary(operation=operation("FAILED")), "Failed", "failed"),
        (summary(error="revision 3 is missing or altered"), "Unreadable", "failed"),
    ],
)
def test_each_state_has_its_own_label(row, label, tab):
    found = lifecycle.status(row)
    assert (found.label, found.tab) == (label, tab)


@pytest.mark.parametrize("state", ["CREATING", "VERIFYING", "NEEDS_STATUS_CHECK"])
def test_an_open_operation_for_an_earlier_revision_still_shows(state):
    row = summary(operation=operation(state, revision=1))
    assert lifecycle.status(row) == lifecycle.OPEN[state]


@pytest.mark.parametrize(("state", "approved"), [("CREATED", False), ("FAILED", True)])
def test_a_settled_operation_for_an_earlier_revision_gives_way_to_the_current_one(state, approved):
    row = summary(operation=operation(state, revision=1), approval={"r": 3} if approved else None)
    assert lifecycle.status(row) == (lifecycle.APPROVED if approved else lifecycle.NEEDS_REVIEW)


def test_labels_are_distinct():
    labels = [
        value.label for value in vars(lifecycle).values() if isinstance(value, lifecycle.Status)
    ]
    assert len(labels) == len(set(labels))


def bound(snapshot):
    return {"revision": snapshot["revision"], "binding": snapshot["binding"]}


def test_the_listing_follows_a_template_from_draft_to_completed(services):
    source = sources.seal(services.store, two_page_pdf())
    created = services.templates.create(source["sha256"], POLICY)
    label = lambda: lifecycle.status(services.templates.summaries()[0]).label  # noqa: E731
    assert label() == "Draft"
    saved, _ = services.templates.save(created["id"], 1, POLICY, [field()], "save-00001")
    assert label() == "Needs review"
    services.templates.confirm_review(created["id"], saved["revision"], saved["binding"])
    services.templates.approve(created["id"], saved["revision"], saved["binding"])
    assert label() == "Approved"
    services.operations.create(created["id"], saved["revision"], saved["binding"])
    assert label() == "Completed"
    services.templates.save(created["id"], 2, POLICY, [field(x=80.0)], "save-00002")
    assert label() == "Needs review"


def test_the_listing_is_newest_first_and_marks_an_unreadable_template(services, clock):
    source = sources.seal(services.store, two_page_pdf())
    older = services.templates.create(source["sha256"], POLICY)
    clock.advance(minutes=5)
    newer = services.templates.create(source["sha256"], POLICY)
    services.store.put_new("templates/" + "f" * 32 + "/orphans/index.json", b"{}")  # no head
    services.store.put_new("templates/not-a-template/head.json", b"{}")
    rows = services.templates.summaries()
    assert [row["id"] for row in rows[:2]] == [newer["id"], older["id"]]
    assert rows[2]["id"] == "f" * 32 and rows[2]["error"]
    assert lifecycle.status(rows[2]).label == "Unreadable"
    assert len(rows) == 3  # names that are not template ids are skipped


def test_the_api_lists_templates_with_their_labels(local):
    sealed = local.post("/api/sources", content=two_page_pdf()).json()
    body = {"source_sha256": sealed["sha256"], "policy": POLICY}
    created = local.post("/api/templates", json=body).json()
    listed = local.get("/api/templates").json()["templates"]
    assert [(row["id"], row["status"]) for row in listed] == [(created["id"], "Draft")]


def test_a_listing_that_cannot_be_read_is_a_503_not_an_empty_library(local, services, monkeypatch):
    def broken(prefix):
        raise StorageError("synthetic: listing failed")

    monkeypatch.setattr(services.store, "children", broken)
    answer = local.get("/api/templates")
    assert answer.status_code == 503 and "nothing" not in answer.text.lower()


@pytest.mark.parametrize("bad", ["head.json", "operation.json"])
def test_a_malformed_record_marks_its_row_and_the_rest_still_list(services, clock, bad):
    source = sources.seal(services.store, two_page_pdf())
    broken = services.templates.create(source["sha256"], POLICY)
    clock.advance(minutes=1)
    fine = services.templates.create(source["sha256"], POLICY)
    key = f"templates/{broken['id']}/{bad}"
    stored = services.store.get(key)
    if stored is None:
        services.store.put_new(key, b"{truncated")
    else:
        services.store.replace(key, b"{truncated", stored.etag)
    rows = {row["id"]: row for row in services.templates.summaries()}
    assert "malformed" in rows[broken["id"]]["error"]
    assert lifecycle.status(rows[broken["id"]]).label == "Unreadable"
    assert rows[fine["id"]]["error"] is None
