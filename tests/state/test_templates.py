import threading
from dataclasses import replace

import pytest

from studio.services.analysis import PageFlag
from studio.state import sources
from studio.state.errors import Conflict, Invalid, NotFound
from studio.state.store import StorageError, canonical
from studio.state.templates import INCOMPLETE
from tests.state.helpers import POLICY, field, two_page_pdf

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


def test_a_retried_save_is_recognized_only_by_its_save_id(services, template):
    save = services.templates.save
    first, changed = save(template["id"], 1, POLICY, [field()], save_id="tab-a-0001")
    assert changed and first["revision"] == 2
    again, changed = save(template["id"], 1, POLICY, [field()], save_id="tab-a-0001")
    assert again == first and not changed  # the same request, retried after it landed
    for other in ("tab-b-0001", None):  # another editor with the same content is stale
        with pytest.raises(Conflict):
            save(template["id"], 1, POLICY, [field()], save_id=other)
    with pytest.raises(Conflict):  # the same id with other content is not a retry either
        save(template["id"], 1, POLICY, [field(x=80.0)], save_id="tab-a-0001")


@pytest.mark.parametrize("save_id", ["short", "x" * 65, "has space", 7])
def test_a_save_id_is_8_to_64_letters_digits_dashes_or_underscores(services, template, save_id):
    with pytest.raises(Invalid, match="save_id"):
        services.templates.save(template["id"], 1, POLICY, [field()], save_id=save_id)


def test_any_save_makes_a_new_revision_and_clears_sign_offs_even_unchanged(services, saved):
    services.templates.confirm_review(saved["id"], 2, saved["binding"])
    services.templates.approve(saved["id"], 2, saved["binding"])
    again, changed = services.templates.save(saved["id"], 2, POLICY, [field()])
    assert changed and again["revision"] == 3 and again["binding"] == saved["binding"]
    assert again["review"] is None and again["approval"] is None
    with pytest.raises(Conflict, match="stale"):  # the old sign-off cannot be reused
        services.templates.approve(saved["id"], 2, saved["binding"])


def test_a_save_that_races_a_sign_off_on_its_base_still_lands(services, saved, monkeypatch):
    real, raced = services.store.replace, []

    def replace(key, data, etag):
        if key.endswith("head.json") and not raced:
            raced.append(True)
            services.templates.confirm_review(saved["id"], 2, saved["binding"])  # lands first
        return real(key, data, etag)

    monkeypatch.setattr(services.store, "replace", replace)
    edited, changed = services.templates.save(saved["id"], 2, POLICY, [field(x=80.0)])
    assert raced and changed and edited["revision"] == 3 and edited["review"] is None
    assert services.templates.get(saved["id"]) == edited


def race_before_sign_off(services, monkeypatch, competitor):
    real, raced = services.store.replace, []

    def replace(key, data, etag):
        if key.endswith("head.json") and not raced:
            raced.append(True)
            competitor()
        return real(key, data, etag)

    monkeypatch.setattr(services.store, "replace", replace)


def test_a_sign_off_that_loses_to_the_same_sign_off_returns_it(services, saved, monkeypatch):
    def same():
        services.templates.confirm_review(saved["id"], 2, saved["binding"])

    race_before_sign_off(services, monkeypatch, same)
    reviewed = services.templates.confirm_review(saved["id"], 2, saved["binding"])
    assert reviewed == services.templates.get(saved["id"]) and reviewed["review"] is not None


def test_a_sign_off_that_loses_to_a_save_is_stale(services, saved, monkeypatch):
    def edit():
        services.templates.save(saved["id"], 2, POLICY, [field(x=80.0)])

    race_before_sign_off(services, monkeypatch, edit)
    with pytest.raises(Conflict, match="stale") as refused:
        services.templates.confirm_review(saved["id"], 2, saved["binding"])
    assert refused.value.current["revision"] == 3 and refused.value.current["review"] is None


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


def test_pages_drafting_left_unchecked_carry_forward_until_a_newer_draft(services, template):
    drafted, _ = services.templates.save(template["id"], 1, POLICY, [field()], unchecked=[2])
    assert drafted["unchecked"] == {"revision": 2, "pages": [2]}
    edited, _ = services.templates.save(template["id"], 2, POLICY, [field(x=80.0)])
    assert edited["revision"] == 3 and edited["unchecked"] == {"revision": 2, "pages": [2]}
    redrafted, _ = services.templates.save(template["id"], 3, POLICY, [field()], unchecked=[])
    assert redrafted["unchecked"] is None
    assert services.templates.get(template["id"])["unchecked"] is None


def test_a_template_never_drafted_has_no_unchecked_pages(services, saved):
    assert saved["unchecked"] is None


def test_review_must_confirm_each_unchecked_page_by_hand(services, template):
    drafted, _ = services.templates.save(template["id"], 1, POLICY, [field()], unchecked=[2])
    for given in ([], [1, 2]):
        with pytest.raises(Invalid, match="page 2"):
            services.templates.confirm_review(template["id"], 2, drafted["binding"], "a", given)
    assert services.templates.get(template["id"])["review"] is None
    reviewed = services.templates.confirm_review(template["id"], 2, drafted["binding"], "a", [2])
    assert reviewed["review"]["checked_pages"] == [2]
    approved = services.templates.approve(template["id"], 2, drafted["binding"], "b")
    assert approved["approval"]["by"] == "b"


def test_approval_is_refused_while_an_unchecked_page_is_unconfirmed(services, template):
    drafted, _ = services.templates.save(template["id"], 1, POLICY, [field()], unchecked=[2])
    head, etag = services.templates.head(template["id"])
    review = {"revision": 2, "binding": drafted["binding"], "at": "t", "by": "a"}  # no pages
    services.store.replace(
        f"templates/{template['id']}/head.json", canonical({**head, "review": review}), etag
    )
    with pytest.raises(Invalid, match="page 2"):
        services.templates.approve(template["id"], 2, drafted["binding"])
    assert services.templates.get(template["id"])["approval"] is None


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


def test_an_incomplete_source_blocks_review_approval_and_create_and_shows_its_flags(
    services, monkeypatch
):
    real = sources.analyze

    def incomplete(data):
        flag = PageFlag(None, "synthetic gap", True)
        return replace(real(data), inventory="INCOMPLETE", flags=[flag])

    monkeypatch.setattr(sources, "analyze", incomplete)
    record = sources.seal(services.store, two_page_pdf())
    template = services.templates.create(record["sha256"], POLICY)
    saved, _ = services.templates.save(template["id"], 1, POLICY, [field()])
    assert saved["issues"] == [INCOMPLETE]
    gap = {"page": None, "reason": "synthetic gap", "blocks_complete": True}
    assert saved["source"] == {"inventory": "INCOMPLETE", "flags": [gap]}
    with pytest.raises(Invalid, match="INCOMPLETE"):
        services.templates.confirm_review(saved["id"], 2, saved["binding"])
    with pytest.raises(Invalid, match="INCOMPLETE"):
        services.operations.create(saved["id"], 2, saved["binding"])


def test_a_complete_source_is_shown_with_its_non_blocking_flags(services, saved):
    assert saved["source"] == {"inventory": "COMPLETE", "flags": []}
