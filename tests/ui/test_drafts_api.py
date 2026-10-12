import pytest

from studio.state import api, drafts, sources
from tests.analysis.fake_gateway import FakeGateway
from tests.analysis.pdf_factory import make_pdf
from tests.analysis.test_fields import FORM
from tests.state.helpers import POLICY
from tests.state.test_hosted import EMAIL, SAME_ORIGIN

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


def test_the_browser_drives_a_draft_to_a_saved_revision(signed_in, template, gateway_):
    started = signed_in.post(path(template), json={"base_revision": 1}, headers=SAME_ORIGIN)
    assert started.status_code == 201
    draft = started.json()
    assert signed_in.get(path(template, "/latest")).json()["draft"]["id"] == draft["id"]
    step = path(template, f"/{draft['id']}/step")
    for _ in range(10):
        view = signed_in.post(step, headers={**SAME_ORIGIN, **TOKEN})
        assert view.status_code == 200 and "oidc-for-this-request" not in view.text
        if view.json()["state"] != drafts.RUNNING:
            break
    assert view.json()["state"] == drafts.DRAFTED
    headers = [h for _, _, h in gateway_.requests]
    assert headers and all(h["Authorization"] == "Bearer oidc-for-this-request" for h in headers)
    result = signed_in.get(path(template, f"/{draft['id']}/result")).json()
    assert result["status"] == "COMPLETE" and len(result["fields"]) == 1
    applied = signed_in.post(path(template, f"/{draft['id']}/apply"), headers=SAME_ORIGIN).json()
    assert applied["template"]["revision"] == 2
    assert (
        applied["draft"]["state"] == drafts.APPLIED and applied["draft"]["applied"]["by"] == EMAIL
    )


def test_a_stale_start_and_an_unknown_draft_answer_plainly(signed_in, template):
    stale = signed_in.post(path(template), json={"base_revision": 7}, headers=SAME_ORIGIN)
    assert stale.status_code == 409 and "newer revision" in stale.json()["detail"]["reason"]
    assert signed_in.get(path(template, "/20261012t120000-0123abcd")).status_code == 404
    assert signed_in.get(path(template, "/latest")).json() == {"draft": None}
