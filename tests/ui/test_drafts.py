import json
from decimal import Decimal

import pytest

from studio.services import gateway, inference
from studio.services.analysis import analyze
from studio.state import drafts, lifecycle, sources
from studio.state.drafts import Drafts, Unavailable
from studio.state.errors import Conflict, Invalid
from studio.state.ledger import CAP_USD, HEAD, StoreLedger
from studio.state.store import canonical
from tests.analysis.fake_gateway import FakeGateway, first_candidate_answer, page_of
from tests.analysis.pdf_factory import make_pdf
from tests.analysis.test_fields import FORM
from tests.analysis.test_inference import stages
from tests.state.helpers import POLICY, field

MODEL = drafts.ROUTE.model


def fake(**kw):
    return FakeGateway(model=MODEL, **kw)


@pytest.fixture
def source():
    return make_pdf([FORM, "A page of plain prose with no fields."])


@pytest.fixture
def book(services, clock):
    return Drafts(services.store, services.templates, clock)


@pytest.fixture
def template(services, source):
    sealed = sources.seal(services.store, source)
    return services.templates.create(sealed["sha256"], POLICY)


def run_to_end(book, template, draft, transport, limit=20, token="t"):
    for _ in range(limit):
        view = book.step(template["id"], draft["id"], token, transport)
        if view["state"] != drafts.RUNNING:
            return view
    raise AssertionError("the draft did not finish")


def test_starting_analyzes_once_and_stores_every_page(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    assert draft["state"] == drafts.RUNNING and draft["preflight"] == "PENDING"
    assert [p["state"] for p in draft["pages"]] == ["pending", "pending"]
    assert draft["model"] == "openai/gpt-5.4-mini" and draft["inventory"] == "COMPLETE"
    prefix = f"templates/{template['id']}/drafts/{draft['id']}/pages/"
    assert services.store.children(prefix) == ["0001.json", "0002.json"]


def test_an_unfinished_draft_is_resumed_not_started_again(book, template):
    first = book.start(template["id"], 1, "anonymous")
    assert book.start(template["id"], 1, "anonymous")["id"] == first["id"]
    assert book.latest(template["id"])["id"] == first["id"]


def test_two_starts_that_raced_share_one_draft(book, template, services):
    book.latest = lambda template_id: None  # each looked before the other's draft existed
    first = book.start(template["id"], 1, "anonymous")
    second = book.start(template["id"], 1, "anonymous")
    assert second["id"] == first["id"]
    assert services.store.children(f"templates/{template['id']}/drafts/") == [first["id"]]


def test_a_start_that_never_finished_holds_its_slot_only_for_a_while(book, template, clock):
    real_put_new = book.store.put_new

    def dies_writing_pages(key, data):
        if "/pages/" in key:
            raise drafts.StorageError("the request died")
        return real_put_new(key, data)

    book.store.put_new = dies_writing_pages
    with pytest.raises(drafts.StorageError):
        book.start(template["id"], 1, "anonymous")
    book.store.put_new = real_put_new
    with pytest.raises(Conflict, match="starting a draft"):
        book.start(template["id"], 1, "anonymous")
    clock.advance(seconds=drafts.START_WINDOW.total_seconds() + 1)
    assert book.start(template["id"], 1, "anonymous")["state"] == drafts.RUNNING


def test_a_draft_starts_only_on_the_current_revision(book, template):
    with pytest.raises(Conflict):
        book.start(template["id"], 2, "anonymous")


def test_no_credential_no_call(book, template):
    draft = book.start(template["id"], 1, "anonymous")
    with pytest.raises(Unavailable):
        book.step(template["id"], draft["id"], None)


def test_each_step_makes_at_most_one_call_and_the_draft_completes(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    gateway_ = fake()
    calls = []
    view = draft
    while view["state"] == drafts.RUNNING:
        before = len(gateway_.chat_bodies())
        view = book.step(template["id"], draft["id"], "t", gateway_)
        calls.append(len(gateway_.chat_bodies()) - before)
    assert max(calls) == 1
    # preflight, page 1 stage A and B; page 2 has nothing to check, so only its stage A
    assert [page_of(b) for b in gateway_.chat_bodies()] == [None, 1, 1, 2]
    assert view["state"] == drafts.DRAFTED and view["done"] == 2
    assert view["spend"]["draft_usd"] > 0
    outcome = book.result(template["id"], draft["id"])
    assert outcome["status"] == "COMPLETE" and len(outcome["fields"]) == 1
    assert outcome["fields"][0]["id"] == "p0001-001" and outcome["fields"][0]["page"] == 1


def test_a_page_whose_draft_is_empty_is_not_checked_even_with_candidates(book, template):
    draft = book.start(template["id"], 1, "anonymous")
    gateway_ = fake(answer=stages([], []))
    views = [book.step(template["id"], draft["id"], "t", gateway_) for _ in range(3)]
    # preflight, then stage A of each page: page 1 has candidates, but nothing was drafted, so
    # each page is finished by its stage A step, with no stage B step left over
    assert [page_of(b) for b in gateway_.chat_bodies()] == [None, 1, 2]
    view = views[-1]
    assert view["state"] == drafts.DRAFTED and view["done"] == 2
    outcome = book.result(template["id"], draft["id"])
    assert outcome["status"] == "COMPLETE" and outcome["fields"] == []


def test_a_failed_draft_cut_short_before_its_check_was_marked_is_not_checked(
    book, template, monkeypatch
):
    def answer(body):  # page 1's draft is unusable; every other answer is the usual one
        drafting = "changes" not in body["response_format"]["json_schema"]["schema"]["properties"]
        return {"notes": ""} if drafting and page_of(body) == 1 else first_candidate_answer(body)

    draft = book.start(template["id"], 1, "anonymous")
    gateway_ = fake(answer=answer)
    book.step(template["id"], draft["id"], "t", gateway_)  # the preflight
    with monkeypatch.context() as cut:
        cut.setattr(book, "_finish_b", lambda *args: (_ for _ in ()).throw(RuntimeError("ended")))
        with pytest.raises(RuntimeError):  # page 1's stage A failed; its request ended there
            book.step(template["id"], draft["id"], "t", gateway_)
    run_to_end(book, template, draft, gateway_)
    outcome = book.result(template["id"], draft["id"])
    assert outcome["status"] == "PARTIAL" and [u["page"] for u in outcome["unfinished"]] == [1]
    assert outcome["unfinished"][0]["status"].startswith("NOT_RUN: stage A has no answer")


def test_a_step_reads_only_its_own_page(book, template, source, monkeypatch):
    from studio.services import candidates

    draft = book.start(template["id"], 1, "anonymous")
    gateway_ = fake()
    book.step(template["id"], draft["id"], "t", gateway_)  # the preflight
    opened = []
    real = candidates._one_page
    monkeypatch.setattr(
        candidates, "_one_page", lambda pdf, n, w: opened.append(n) or real(pdf, n, w)
    )
    book.step(template["id"], draft["id"], "t", gateway_)  # stage A of page 1
    assert opened == [1]
    for number in (1, 2):
        whole = [c for c in candidates.extract_candidates(source, [number]) if c.page == number]
        assert candidates.page_candidates(source, number) == whole[0]


def test_the_draft_sends_exactly_what_the_cli_sends(book, template, source):
    draft = book.start(template["id"], 1, "anonymous")
    in_app = fake()
    run_to_end(book, template, draft, in_app)
    cli = fake()
    config = inference.RunConfig(drafts.ROUTE, ["Client"], "lp", workers=1)
    cli_ledger = StoreLedger(book.store, book.clock, "cli-comparison")
    inference.run(
        source, analyze(source), config, token="t", ledger=cli_ledger, run_id="c", transport=cli
    )
    assert sorted(map(canonical, in_app.chat_bodies())) == sorted(map(canonical, cli.chat_bodies()))


def test_applying_saves_the_checked_fields_as_one_revision(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake())
    applied = book.apply(template["id"], draft["id"], "anonymous")
    saved = applied["template"]
    assert saved["revision"] == 2 and len(saved["fields"]) == 1
    assert saved["review"] is None and applied["draft"]["state"] == drafts.APPLIED
    assert lifecycle.status(services.templates.summaries()[0]).label == "Needs review"
    again = book.apply(template["id"], draft["id"], "anonymous")  # a retried apply
    assert again["template"]["revision"] == 2


def test_an_apply_whose_record_was_lost_is_finished_by_a_retry(book, template):
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake())
    real_write_once = book._write_once

    def fails_on_the_record(key, value):
        if key.endswith("applied.json"):
            raise drafts.StorageError("the request died")
        return real_write_once(key, value)

    book._write_once = fails_on_the_record
    with pytest.raises(drafts.StorageError):  # the revision was saved, the record was not
        book.apply(template["id"], draft["id"], "anonymous")
    book._write_once = real_write_once
    again = book.apply(template["id"], draft["id"], "anonymous")
    assert again["template"]["revision"] == 2 and again["draft"]["state"] == drafts.APPLIED


def test_a_draft_is_not_applied_over_later_edits(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake())
    services.templates.save(template["id"], 1, POLICY, [], "someone-else-saved")
    with pytest.raises(Conflict, match="changed after this draft started"):
        book.apply(template["id"], draft["id"], "anonymous")


def test_a_running_draft_cannot_be_applied(book, template):
    draft = book.start(template["id"], 1, "anonymous")
    with pytest.raises(Conflict, match="still running"):
        book.apply(template["id"], draft["id"], "anonymous")


def test_a_failed_check_leaves_its_page_out_and_never_uses_stage_a(book, template):
    draft = book.start(template["id"], 1, "anonymous")
    view = run_to_end(book, template, draft, fake(fail_stage_b=True))
    assert view["state"] == drafts.DRAFTED
    outcome = book.result(template["id"], draft["id"])
    assert outcome["status"] == "PARTIAL" and outcome["fields"] == []
    assert outcome["unfinished"][0]["page"] == 1
    assert outcome["unfinished"][0]["status"].startswith("FAILED")
    with pytest.raises(Invalid, match="no fields"):
        book.apply(template["id"], draft["id"], "anonymous")


def fails_checking(number):
    """A gateway failure for the check of page `number` only."""

    def fail(body):
        checking = "changes" in body["response_format"]["json_schema"]["schema"]["properties"]
        if checking and page_of(body) == number:
            return gateway.GatewayError("HTTP 500 from the gateway (synthetic)")
        return None

    return fail


def test_a_finished_draft_is_saved_without_a_click(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    running = book.view(template["id"], draft["id"])
    assert book.finish(template["id"], draft["id"]) == running  # nothing to save yet
    run_to_end(book, template, draft, fake())
    view = book.finish(template["id"], draft["id"])
    assert view["state"] == drafts.APPLIED and "not_applied" not in view
    applied = {k: v for k, v in view["applied"].items() if k != "at"}
    assert applied == {
        "revision": 2,
        "status": "COMPLETE",
        "fields": 1,
        "rejected": 0,
        "unfinished": [],
        "by": "anonymous",  # who started it
        "automatic": True,
    }
    assert lifecycle.status(services.templates.summaries()[0]).label == "Needs review"
    assert book.finish(template["id"], draft["id"])["applied"] == view["applied"]
    assert services.templates.get(template["id"])["revision"] == 2  # finishing again saves nothing
    outcome = book.outcome(template["id"], draft["id"], 2)
    assert outcome == {
        "revision": 2,
        "status": "COMPLETE",
        "fields": 1,
        "rejected": 0,
        "unfinished": [],
    }
    assert book.outcome(template["id"], draft["id"], 1) is None


def test_a_finish_that_cannot_save_says_why_and_leaves_the_draft_drafted(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake())
    services.templates.save(template["id"], 1, POLICY, [], "someone-else-saved")
    view = book.finish(template["id"], draft["id"])
    assert view["state"] == drafts.DRAFTED
    assert "changed after this draft started" in view["not_applied"]
    assert book.outcome(template["id"], draft["id"], 2) is None  # revision 2 is not the draft's


def test_a_draft_that_found_no_fields_tells_the_editor(book, template):
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake(fail_stage_b=True))
    view = book.finish(template["id"], draft["id"])
    assert view["state"] == drafts.DRAFTED and "no fields" in view["not_applied"]
    assert book.outcome(template["id"], draft["id"], 1) == {
        "revision": 1,
        "status": "PARTIAL",
        "fields": 0,
        "rejected": 0,
        "unfinished": [1],
    }


def test_a_partial_draft_is_saved_with_the_pages_it_could_not_check(book, services):
    sealed = sources.seal(services.store, make_pdf([FORM, FORM]))
    template = services.templates.create(sealed["sha256"], POLICY)
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake(fail=fails_checking(2)))
    view = book.finish(template["id"], draft["id"])
    assert view["state"] == drafts.APPLIED
    assert view["applied"]["status"] == "PARTIAL" and view["applied"]["unfinished"] == [2]
    assert book.outcome(template["id"], draft["id"], 2)["unfinished"] == [2]


def test_pages_a_partial_draft_left_unchecked_block_approval_of_later_revisions(book, services):
    sealed = sources.seal(services.store, make_pdf([FORM, FORM]))
    template = services.templates.create(sealed["sha256"], POLICY)
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake(fail=fails_checking(2)))
    applied = book.finish(template["id"], draft["id"])["applied"]
    drafted = services.templates.get(template["id"])
    assert drafted["unchecked"] == {"revision": applied["revision"], "pages": [2]}
    nudged = [{**f, "x": f["x"] + 1} for f in drafted["fields"]]
    edited, _ = services.templates.save(template["id"], 2, drafted["policy"], nudged)
    assert edited["revision"] == 3 and edited["unchecked"] == {"revision": 2, "pages": [2]}
    with pytest.raises(Invalid, match="page 2"):
        services.templates.confirm_review(template["id"], 3, edited["binding"], "anonymous")
    services.templates.confirm_review(template["id"], 3, edited["binding"], "anonymous", [2])
    approved = services.templates.approve(template["id"], 3, edited["binding"], "anonymous")
    assert approved["approval"] is not None


def test_a_complete_draft_clears_pages_an_earlier_draft_left_unchecked(book, services):
    sealed = sources.seal(services.store, make_pdf([FORM, FORM]))
    template = services.templates.create(sealed["sha256"], POLICY)
    first = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, first, fake(fail=fails_checking(2)))
    book.finish(template["id"], first["id"])
    second = book.start(template["id"], 2, "anonymous")
    run_to_end(book, template, second, fake())
    assert book.finish(template["id"], second["id"])["state"] == drafts.APPLIED
    assert services.templates.get(template["id"])["unchecked"] is None


def test_an_earlier_apply_record_still_reads_as_an_outcome(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake())
    book.apply(template["id"], draft["id"], "anonymous")
    key = f"templates/{template['id']}/drafts/{draft['id']}/applied.json"
    stored = services.store.get(key)
    record = json.loads(stored.data)
    for added in ("rejected", "unfinished", "automatic"):
        del record[added]
    services.store.replace(key, canonical(record), stored.etag)
    assert book.outcome(template["id"], draft["id"], 2)["unfinished"] == []


NEAR_CAP = str(CAP_USD - Decimal("2.28"))  # with the $2.276604 opening, $0.003396 short of the cap


def test_a_draft_that_would_pass_the_cap_stops_before_sending(book, template, services):
    head = {"opening_usd": "2.276604", "settled_usd": NEAR_CAP, "open": {}, "runs": {}}
    services.store.put_new(HEAD, canonical(head))
    draft = book.start(template["id"], 1, "anonymous")
    gateway_ = fake()
    view = book.step(template["id"], draft["id"], "t", gateway_)
    assert gateway_.requests == []  # nothing was sent
    assert view["state"] == drafts.STOPPED and "cap" in view["stop_reason"]
    assert view["preflight"].startswith("BUDGET_STOPPED")


def test_a_wrong_route_stops_the_draft(book, template):
    draft = book.start(template["id"], 1, "anonymous")
    view = book.step(template["id"], draft["id"], "t", FakeGateway(model="openai/gpt-4.1"))
    assert view["state"] == drafts.STOPPED and view["stop_reason"].startswith("route violation")
    assert book.step(template["id"], draft["id"], "t", fake())["state"] == drafts.STOPPED


CHANGES = {
    "route": (drafts, "ROUTE", gateway.Route("openai/gpt-4.1", "byok", accept_fallback=True)),
    "rules": (inference, "RULES", inference.RULES + "\n- New."),
    "prompt": (inference, "STAGE_B", inference.STAGE_B + " Now."),
}


@pytest.mark.parametrize("change", CHANGES.values(), ids=CHANGES.keys())
def test_a_draft_stops_when_its_configuration_changes_after_it_started(
    book, template, monkeypatch, change
):
    draft = book.start(template["id"], 1, "anonymous")
    transport = fake()
    assert book.step(template["id"], draft["id"], "t", transport)["state"] == drafts.RUNNING
    sent = len(transport.chat_bodies())
    monkeypatch.setattr(*change)
    view = book.step(template["id"], draft["id"], "t", transport)
    assert view["state"] == drafts.STOPPED
    assert view["stop_reason"] == drafts.CHANGED
    assert len(transport.chat_bodies()) == sent  # nothing was sent on the changed configuration


def test_a_draft_started_before_its_configuration_was_recorded_stops(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    key = f"templates/{template['id']}/drafts/{draft['id']}/draft.json"
    stored = services.store.get(key)
    header = json.loads(stored.data)
    del header["prompts_sha256"]
    services.store.replace(key, canonical(header), stored.etag)
    view = book.step(template["id"], draft["id"], "t", fake())
    assert view["state"] == drafts.STOPPED and view["stop_reason"] == drafts.CHANGED


def test_a_changed_configuration_stops_the_draft_before_reading_its_source(
    book, template, monkeypatch
):
    draft = book.start(template["id"], 1, "anonymous")
    monkeypatch.setattr(drafts, "ROUTE", gateway.Route("openai/gpt-4.1", "byok", True))

    def unread(store, sha256):
        raise AssertionError("the source was read")

    monkeypatch.setattr(sources, "source_bytes", unread)
    assert book.step(template["id"], draft["id"], "t", fake())["stop_reason"] == drafts.CHANGED


def test_a_finished_draft_is_applied_after_a_later_change_since_its_calls_were_checked(
    book, template, monkeypatch
):
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake())
    monkeypatch.setattr(inference, "STAGE_B", inference.STAGE_B + " Now.")
    assert book.apply(template["id"], draft["id"], "anonymous")["draft"]["state"] == drafts.APPLIED


def test_a_finished_draft_without_a_recorded_configuration_is_not_applied(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake())
    key = f"templates/{template['id']}/drafts/{draft['id']}/draft.json"
    stored = services.store.get(key)
    header = json.loads(stored.data)
    del header["prompts_sha256"]
    services.store.replace(key, canonical(header), stored.etag)
    with pytest.raises(Invalid, match="start a new draft"):
        book.apply(template["id"], draft["id"], "anonymous")
    view = book.finish(template["id"], draft["id"])
    assert view["state"] == drafts.DRAFTED and "start a new draft" in view["not_applied"]
    assert services.templates.get(template["id"])["revision"] == 1


def test_a_reopened_unrecorded_draft_says_it_cannot_be_saved(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake())
    assert "not_applied" not in book.view(template["id"], draft["id"])  # it recorded its config
    key = f"templates/{template['id']}/drafts/{draft['id']}/draft.json"
    stored = services.store.get(key)
    header = json.loads(stored.data)
    del header["prompts_sha256"]
    services.store.replace(key, canonical(header), stored.etag)
    # the drafting page hides its save button and offers a new draft when the view says why
    reopened = book.latest(template["id"])
    assert reopened["state"] == drafts.DRAFTED and reopened["not_applied"] == drafts.UNRECORDED


def test_an_unrecorded_draft_whose_apply_landed_is_finished_by_a_retry(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake())
    real_write_once = book._write_once

    def fails_on_the_record(key, value):
        if key.endswith("applied.json"):
            raise drafts.StorageError("the request died")
        return real_write_once(key, value)

    book._write_once = fails_on_the_record
    with pytest.raises(drafts.StorageError):  # the revision was saved, the record was not
        book.apply(template["id"], draft["id"], "anonymous")
    book._write_once = real_write_once
    key = f"templates/{template['id']}/drafts/{draft['id']}/draft.json"
    stored = services.store.get(key)
    header = json.loads(stored.data)
    del header["prompts_sha256"]  # as a draft started before the hash was recorded
    services.store.replace(key, canonical(header), stored.etag)
    assert "not_applied" not in book.view(template["id"], draft["id"])  # its save landed
    again = book.apply(template["id"], draft["id"], "anonymous")
    assert again["template"]["revision"] == 2 and again["draft"]["state"] == drafts.APPLIED


def test_a_failed_route_check_stops_the_draft_even_if_its_stop_was_lost(book, template):
    draft = book.start(template["id"], 1, "anonymous")
    real_write_once = book._write_once

    def loses_the_stop(key, value):
        if key.endswith(("stop.json", "violation.json")):
            raise drafts.StorageError("the request died")
        return real_write_once(key, value)

    book._write_once = loses_the_stop
    with pytest.raises(drafts.StorageError):  # the check's result was kept, its stop was not
        book.step(template["id"], draft["id"], "t", FakeGateway(model="openai/gpt-4.1"))
    book._write_once = real_write_once
    gateway_ = fake()
    view = book.step(template["id"], draft["id"], "t", gateway_)
    assert view["state"] == drafts.STOPPED and view["stop_reason"].startswith("preflight")
    assert gateway_.chat_bodies() == []


def test_a_claimed_call_is_never_sent_twice(book, template, services, clock):
    draft = book.start(template["id"], 1, "anonymous")
    gateway_ = fake()
    book.step(template["id"], draft["id"], "t", gateway_)  # the preflight
    prefix = f"templates/{template['id']}/drafts/{draft['id']}/steps/"
    claim = canonical({"at": clock().isoformat()})
    services.store.put_new(prefix + "p0001-a.started.json", claim)  # another request is on it
    view = book.step(template["id"], draft["id"], "t", gateway_)
    assert [page_of(b) for b in gateway_.chat_bodies()] == [None, 2]
    assert view["pages"][0]["state"] == "drafting"
    clock.advance(seconds=drafts.LEASE.total_seconds() + 1)  # that request never finished
    view = run_to_end(book, template, draft, gateway_)
    assert view["pages"][0]["state"] == "lost" and view["state"] == drafts.DRAFTED
    assert [page_of(b) for b in gateway_.chat_bodies()].count(1) == 0
    assert book.result(template["id"], draft["id"])["status"] == "PARTIAL"


def test_a_route_check_that_never_answered_stops_the_draft(book, template, services, clock):
    draft = book.start(template["id"], 1, "anonymous")
    prefix = f"templates/{template['id']}/drafts/{draft['id']}/steps/"
    services.store.put_new(
        prefix + "preflight.started.json", canonical({"at": clock().isoformat()})
    )
    assert book.view(template["id"], draft["id"])["state"] == drafts.RUNNING
    clock.advance(seconds=drafts.LEASE.total_seconds() + 1)
    view = book.view(template["id"], draft["id"])
    assert view["state"] == drafts.STOPPED and "outcome is unknown" in view["stop_reason"]


def test_records_never_hold_the_token(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    gateway_ = fake()
    run_to_end(book, template, draft, gateway_, token="secret-token")
    assert all("secret-token" in h["Authorization"] for _, _, h in gateway_.requests)
    book.apply(template["id"], draft["id"], "anonymous")
    root = f"templates/{template['id']}/drafts/{draft['id']}/"
    for folder in ("", "pages/", "steps/"):
        for name in services.store.children(root + folder):
            if name.endswith(".json"):
                assert b"secret-token" not in services.store.get(root + folder + name).data
    for name in services.store.children("spend/rows/"):
        assert b"secret-token" not in services.store.get("spend/rows/" + name).data


def test_the_stage_records_are_kept(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    run_to_end(book, template, draft, fake())
    steps = f"templates/{template['id']}/drafts/{draft['id']}/steps/"
    first = json.loads(services.store.get(steps + "p0001-a.json").data)
    check = json.loads(services.store.get(steps + "p0001-b.json").data)
    assert first["status"] == "RECEIVED" and check["status"] == "RECEIVED"
    assert first["evidence"]["image_sha256"] == check["evidence"]["image_sha256"]
    assert check["changes"] == [] and len(check["evidence"]["calls"]) == 1
    assert gateway.TIMEOUT_S < drafts.LEASE.total_seconds()


def test_a_step_passes_over_work_another_request_claimed(book, template, services, clock):
    draft = book.start(template["id"], 1, "anonymous")
    gateway_ = fake()
    book.step(template["id"], draft["id"], "t", gateway_)  # the preflight
    prefix = f"templates/{template['id']}/drafts/{draft['id']}/steps/"
    real_claim = book._claim

    def raced(prefix_, step):  # another request claims page 1 between the listing and the claim
        if step == "p0001-a":
            real_claim(prefix_, step)
            return False
        return real_claim(prefix_, step)

    book._claim = raced
    book.step(template["id"], draft["id"], "t", gateway_)
    assert [page_of(b) for b in gateway_.chat_bodies()] == [None, 2]  # one call, on page 2
    assert services.store.get(prefix + "p0002-a.json") is not None


def test_the_view_says_how_many_saved_fields_applying_replaces(book, template, services):
    services.templates.save(template["id"], 1, POLICY, [field()], "first-save")
    assert book.start(template["id"], 2, "anonymous")["base_fields"] == 1


def test_a_page_call_that_would_pass_the_cap_stops_the_draft(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    gateway_ = fake()
    book.step(template["id"], draft["id"], "t", gateway_)  # the preflight passes
    stored = services.store.get(HEAD)
    head = json.loads(stored.data) | {"settled_usd": NEAR_CAP}
    services.store.replace(HEAD, canonical(head), stored.etag)
    view = book.step(template["id"], draft["id"], "t", gateway_)
    assert len(gateway_.chat_bodies()) == 1  # only the preflight was sent
    assert view["state"] == drafts.STOPPED and view["stop_reason"].startswith("budget")
    assert book.result(template["id"], draft["id"])["status"] == "PARTIAL"


def test_drafting_works_on_r2(hosted):
    sealed = sources.seal(hosted.store, make_pdf([FORM]))
    template = hosted.templates.create(sealed["sha256"], POLICY)
    draft = hosted.drafts.start(template["id"], 1, "anonymous")
    view = run_to_end(hosted.drafts, template, draft, fake())
    assert view["state"] == drafts.DRAFTED
    applied = hosted.drafts.apply(template["id"], draft["id"], "anonymous")
    assert applied["template"]["revision"] == 2 and len(applied["template"]["fields"]) == 1


def test_a_step_that_began_before_a_stop_sends_nothing(book, template, services):
    draft = book.start(template["id"], 1, "anonymous")
    gateway_ = fake()
    book.step(template["id"], draft["id"], "t", gateway_)  # the preflight
    prefix = f"templates/{template['id']}/drafts/{draft['id']}/"
    real_claim = book._claim

    def stopped_meanwhile(prefix_, step):  # another request records a route violation first
        services.store.put_new(
            prefix + "violation.json", canonical({"reason": "route violation: x"})
        )
        return real_claim(prefix_, step)

    book._claim = stopped_meanwhile
    view = book.step(template["id"], draft["id"], "t", gateway_)
    assert len(gateway_.chat_bodies()) == 1  # only the preflight
    assert view["state"] == drafts.STOPPED
    first = json.loads(services.store.get(prefix + "steps/p0001-a.json").data)
    check = json.loads(services.store.get(prefix + "steps/p0001-b.json").data)
    assert first["status"] == "NOT_RUN: route violation: x"
    assert check["status"] == "NOT_RUN: stage A has no answer (NOT_RUN: route violation: x)"
