import pytest

from studio.state import api, drafts, sources
from tests.analysis.fake_gateway import FakeGateway
from tests.analysis.pdf_factory import make_pdf
from tests.analysis.test_fields import FORM
from tests.state.helpers import POLICY
from tests.state.test_hosted import EMAIL, SAME_ORIGIN
from tests.ui.test_editor_page import embedded

TOKEN = {api.OIDC_HEADER: "oidc-for-this-request"}


@pytest.fixture(autouse=True)
def no_environment_token(monkeypatch):
    monkeypatch.delenv("VERCEL_OIDC_TOKEN", raising=False)


@pytest.fixture
def gateway_(hosted):
    hosted.drafts.transport = FakeGateway(model=drafts.ROUTE.model)
    return hosted.drafts.transport


@pytest.fixture
def template(hosted):
    sealed = sources.seal(hosted.store, make_pdf([FORM, "A page of plain prose."]))
    return hosted.templates.create(sealed["sha256"], POLICY)


def path(template, rest=""):
    return f"/api/templates/{template['id']}/drafts{rest}"


def test_local_state_does_not_draft(local, services):
    sealed = sources.seal(services.store, make_pdf([FORM]))
    template = services.templates.create(sealed["sha256"], POLICY)
    answer = local.post(path(template), json={"base_revision": 1})
    assert answer.status_code == 409 and "hosted studio" in answer.json()["detail"]
    assert local.get(path(template, "/latest")).status_code == 409


def test_drafting_needs_a_signed_in_request(visitor, template):
    body = {"base_revision": 1}
    assert visitor.post(path(template), json=body, headers=SAME_ORIGIN).status_code == 401


def test_a_change_from_another_site_is_refused(signed_in, template):
    other = {"origin": "https://elsewhere.example"}
    assert (
        signed_in.post(path(template), json={"base_revision": 1}, headers=other).status_code == 403
    )


def test_a_step_without_a_model_credential_sends_nothing(signed_in, template, gateway_):
    draft = signed_in.post(path(template), json={"base_revision": 1}, headers=SAME_ORIGIN).json()
    answer = signed_in.post(path(template, f"/{draft['id']}/step"), headers=SAME_ORIGIN)
    assert answer.status_code == 503 and "credential" in answer.json()["detail"]
    assert gateway_.requests == []


def drive(client, template, draft):
    step = path(template, f"/{draft['id']}/step")
    for _ in range(10):
        view = client.post(step, headers={**SAME_ORIGIN, **TOKEN})
        assert view.status_code == 200 and "oidc-for-this-request" not in view.text
        if view.json()["state"] != drafts.RUNNING:
            return view.json()
    raise AssertionError("the draft did not finish")


def test_the_browser_drives_a_draft_to_a_saved_revision(signed_in, template, gateway_):
    started = signed_in.post(path(template), json={"base_revision": 1}, headers=SAME_ORIGIN)
    assert started.status_code == 201
    draft = started.json()
    assert signed_in.get(path(template, "/latest")).json()["draft"]["id"] == draft["id"]
    finished = drive(signed_in, template, draft)
    # the step that finished drafting saved the fields: no click
    assert finished["state"] == drafts.APPLIED and finished["applied"]["revision"] == 2
    assert finished["applied"]["by"] == EMAIL and finished["applied"]["automatic"] is True
    headers = [h for _, _, h in gateway_.requests]
    assert headers and all(h["Authorization"] == "Bearer oidc-for-this-request" for h in headers)
    result = signed_in.get(path(template, f"/{draft['id']}/result")).json()
    assert result["status"] == "COMPLETE" and len(result["fields"]) == 1
    again = signed_in.post(path(template, f"/{draft['id']}/apply"), headers=SAME_ORIGIN).json()
    assert again["template"]["revision"] == 2 and again["draft"]["applied"] == finished["applied"]
    editor = embedded(signed_in.get(f"/templates/{template['id']}/edit"))
    assert editor["template"]["revision"] == 2
    assert editor["drafted"] == {
        "revision": 2,
        "status": "COMPLETE",
        "fields": 1,
        "rejected": 0,
        "unfinished": [],
    }


def test_a_draft_that_found_no_fields_goes_to_the_editor_saying_so(signed_in, template, hosted):
    hosted.drafts.transport = FakeGateway(model=drafts.ROUTE.model, fail_stage_b=True)
    draft = signed_in.post(path(template), json={"base_revision": 1}, headers=SAME_ORIGIN).json()
    finished = drive(signed_in, template, draft)
    assert finished["state"] == drafts.DRAFTED and "no fields" in finished["not_applied"]
    page = f"/templates/{template['id']}/edit"
    assert embedded(signed_in.get(page))["drafted"] is None  # only when the drafting page says
    named = embedded(signed_in.get(f"{page}?drafted={draft['id']}"))["drafted"]
    assert named["fields"] == 0 and named["unfinished"] == [1] and named["revision"] == 1
    assert embedded(signed_in.get(f"{page}?drafted=../../etc"))["drafted"] is None


def test_a_stale_start_and_an_unknown_draft_answer_plainly(signed_in, template):
    stale = signed_in.post(path(template), json={"base_revision": 7}, headers=SAME_ORIGIN)
    assert stale.status_code == 409 and "newer revision" in stale.json()["detail"]["reason"]
    assert signed_in.get(path(template, "/20261012t120000-0123abcd")).status_code == 404
    assert signed_in.get(path(template, "/latest")).json() == {"draft": None}
