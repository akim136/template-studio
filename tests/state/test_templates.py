import threading

import pytest

from studio.state.errors import Conflict, Invalid, NotFound
from studio.state.store import StorageError
from tests.state.helpers import POLICY, field

COUNTERSIGNED = {"countersigner": True, "required": [{"signer": 0, "type": "signature"}]}


@pytest.fixture
def template(services, source):
    return services.templates.create(source["sha256"], POLICY)


@pytest.fixture
def saved(services, template):
    snapshot, changed = services.templates.save(template["id"], 1, POLICY, [field()])
    assert changed
    return snapshot


def test_a_new_template_starts_empty_and_cannot_be_reviewed(services, template, source):
    assert template["revision"] == 1 and template["fields"] == []
    assert template["source_sha256"] == source["sha256"]
    assert template["issues"] == ["the field map is empty"]
    with pytest.raises(Invalid, match="empty"):
        services.templates.confirm_review(template["id"], 1, template["binding"])


@pytest.mark.parametrize("sha", ["0" * 64, "not-a-hash", None])
def test_a_template_needs_a_sealed_source(services, sha):
    with pytest.raises(Invalid):
        services.templates.create(sha, POLICY)


@pytest.mark.parametrize("template_id", ["0" * 32, "../head", "A" * 32])
def test_an_unknown_template_is_not_found(services, template_id):
    with pytest.raises(NotFound):
        services.templates.get(template_id)


def test_a_save_based_on_the_head_makes_the_next_revision(services, template, saved):
    assert saved["revision"] == 2 and saved["fields"] == [field()] and saved["issues"] == []
    assert saved["binding"] != template["binding"]
    assert services.templates.get(template["id"]) == saved


def test_a_stale_save_is_refused_with_the_current_revision(services, template, saved):
    with pytest.raises(Conflict) as refused:
        services.templates.save(template["id"], 1, POLICY, [field(x=80.0)])
    assert refused.value.current == saved
    assert services.templates.get(template["id"]) == saved


def test_a_retried_save_that_already_landed_is_not_a_conflict(services, template, saved):
    again, changed = services.templates.save(template["id"], 1, POLICY, [field()])
    assert again == saved and not changed


def test_a_save_that_changes_nothing_keeps_the_revision_and_its_review(services, saved):
    reviewed = services.templates.confirm_review(saved["id"], 2, saved["binding"])
    again, changed = services.templates.save(saved["id"], 2, POLICY, [field()])
    assert not changed and again["revision"] == 2 and again["review"] == reviewed["review"]


@pytest.mark.parametrize(
    ("policy", "fields"),
    [(POLICY, [field(x=80.0)]), (COUNTERSIGNED, [field()])],
    ids=["field edit", "policy change"],
)
def test_a_consequential_save_clears_review_and_approval(services, saved, policy, fields):
    services.templates.confirm_review(saved["id"], 2, saved["binding"])
    approved = services.templates.approve(saved["id"], 2, saved["binding"])
    assert approved["approval"]["by"] == "anonymous"
    edited, changed = services.templates.save(saved["id"], 2, policy, fields)
    assert changed and edited["revision"] == 3
    assert edited["review"] is None and edited["approval"] is None


def test_review_and_approval_for_a_stale_revision_or_binding_are_refused(services, saved):
    services.templates.confirm_review(saved["id"], 2, saved["binding"])
    edited, _ = services.templates.save(saved["id"], 2, POLICY, [field(x=80.0)])
    for revision, bound in [(2, saved["binding"]), (3, saved["binding"]), (3, "f" * 64)]:
        with pytest.raises(Conflict, match="stale") as refused:
            services.templates.approve(saved["id"], revision, bound)
        assert refused.value.current == edited
    assert services.templates.get(saved["id"])["approval"] is None


def test_approval_needs_a_confirmed_review_of_the_same_revision(services, saved):
    with pytest.raises(Invalid, match="review of this revision is not confirmed"):
        services.templates.approve(saved["id"], 2, saved["binding"])


def test_unmet_policy_requirements_block_review(services, template):
    unmet, _ = services.templates.save(template["id"], 1, POLICY, [field(required=False)])
    with pytest.raises(Invalid, match="requires a required signature field for Client"):
        services.templates.confirm_review(unmet["id"], 2, unmet["binding"])


def test_invalid_fields_and_policies_are_refused_and_nothing_is_saved(services, template):
    with pytest.raises(Invalid, match="not in the source"):
        services.templates.save(template["id"], 1, POLICY, [field(page=3)])
    with pytest.raises(Invalid, match="policy:"):
        services.templates.save(template["id"], 1, {"countersigner": 1, "required": []}, [])
    assert services.templates.get(template["id"]) == template


def test_a_storage_failure_is_never_reported_as_saved(services, template, monkeypatch):
    def failing(*args):
        raise StorageError("cannot write templates/head.json: No space left on device")

    monkeypatch.setattr(services.store, "replace", failing)
    with pytest.raises(StorageError):
        services.templates.save(template["id"], 1, POLICY, [field()])
    monkeypatch.undo()
    assert services.templates.get(template["id"]) == template


def test_concurrent_saves_from_one_revision_have_exactly_one_winner(services, template):
    start, outcomes = threading.Barrier(6), []

    def attempt(index: int) -> None:
        start.wait()
        try:
            services.templates.save(template["id"], 1, POLICY, [field(x=72.0 + index)])
            outcomes.append("saved")
        except Conflict:
            outcomes.append("conflict")

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["conflict"] * 5 + ["saved"]
    assert services.templates.get(template["id"])["revision"] == 2


def test_an_altered_revision_is_detected_on_read(services, saved, tmp_path):
    folder = tmp_path / "state" / "templates" / saved["id"] / "revisions"
    for path in folder.iterdir():
        path.write_bytes(path.read_bytes().replace(b"72.0", b"73.0"))
    with pytest.raises(StorageError, match="missing or altered"):
        services.templates.get(saved["id"])
