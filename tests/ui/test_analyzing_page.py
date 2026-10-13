import pytest

from studio.state import sources
from studio.ui.pages import ASSET_VERSION
from tests.state.helpers import POLICY, two_page_pdf

DETAILS = {
    "title": "Fund II <Subscription>",
    "sponsor_name": "Acme Capital",
    "sponsor_account_id": "",
}


def make(services):
    source = sources.seal(services.store, two_page_pdf())
    return services.templates.create(source["sha256"], POLICY, DETAILS)


def test_local_mode_says_where_drafting_runs_and_loads_no_driver(local, services):
    template = make(services)
    page = local.get(f"/templates/{template['id']}/analyzing")
    assert page.status_code == 200
    assert "Field drafting runs on the hosted studio." in page.text
    assert "analyzing.js" not in page.text and "data-stages" not in page.text
    record = local.get(f"/templates/{template['id']}")
    assert "Field drafting runs on the hosted studio" in record.text
    assert "/analyzing" not in record.text


def test_the_hosted_page_shows_the_stages_and_loads_the_driver(signed_in, hosted):
    template = make(hosted)
    page = signed_in.get(f"/templates/{template['id']}/analyzing")
    assert page.status_code == 200
    assert f'<script src="/static/analyzing.js?v={ASSET_VERSION}" defer></script>' in page.text
    for label in ("Upload verified", "Extract document", "Propose fields", "Validate draft"):
        assert label in page.text
    assert 'data-autostart="no"' in page.text and 'data-revision="1"' in page.text
    assert "Fund II &lt;Subscription&gt;" in page.text and "<Subscription>" not in page.text
    # the title names the draft in the notices of drafting that goes on after this page is left
    assert 'data-title="Fund II &lt;Subscription&gt;"' in page.text
    # no draft cost, spend total or note on model calls before the studio is opened to others
    for removed in ("data-spend", "All drafting", "Each page is drafted"):
        assert removed not in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    started = signed_in.get(f"/templates/{template['id']}/analyzing?start=1")
    assert 'data-autostart="yes"' in started.text
    record = signed_in.get(f"/templates/{template['id']}")
    assert f'href="/templates/{template["id"]}/analyzing">Draft fields</a>' in record.text


def test_hosted_setup_goes_on_to_drafting(signed_in):
    assert 'data-drafting="hosted"' in signed_in.get("/new").text


def test_local_setup_does_not(local):
    page = local.get("/new")
    assert 'data-drafting="local"' in page.text and "not in local mode" in page.text


@pytest.mark.parametrize("template_id", ["a" * 32, "not-an-id"])
def test_an_unknown_template_is_404(local, template_id):
    assert local.get(f"/templates/{template_id}/analyzing").status_code == 404


def test_a_visitor_without_a_session_is_sent_to_sign_in(visitor, hosted):
    template = make(hosted)
    page = visitor.get(f"/templates/{template['id']}/analyzing", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/auth/login"
