import io
import json
import re
import uuid

import pypdfium2
import pytest

from studio.state import sources
from studio.state.store import StorageError
from tests.state.helpers import POLICY, field, two_page_pdf

DETAILS = {
    "title": "Fund II <Subscription>",
    "sponsor_name": "Acme Capital",
    "sponsor_account_id": "",
}


def make(services, fields=()):
    source = sources.seal(services.store, two_page_pdf())
    created = services.templates.create(source["sha256"], POLICY, DETAILS)
    if fields:
        saved, _ = services.templates.save(created["id"], 1, POLICY, list(fields), uuid.uuid4().hex)
        return saved
    return created


def embedded(page):
    found = re.search(
        r'<script type="application/json" data-editor-data>(.*?)</script>', page.text, re.S
    )
    return json.loads(found.group(1))


def test_the_editor_carries_the_saved_revision_and_page_sizes(local, services):
    template = make(services, [field()])
    page = local.get(f"/templates/{template['id']}/edit")
    assert page.status_code == 200
    assert '<script src="/static/editor.js" defer></script>' in page.text
    data = embedded(page)
    assert data["template"]["revision"] == 2 and data["template"]["fields"] == template["fields"]
    assert [p["page"] for p in data["pages"]] == [1, 2]
    assert all(p["width"] and p["rotation"] == 0 for p in data["pages"])
    assert data["roles"] == ["Client", "CounterSigner"]
    assert "amount" in data["merge_names"] and data["operation"] is None


def test_typed_values_cannot_close_the_data_block(local, services):
    page = local.get(f"/templates/{make(services)['id']}/edit")
    assert "<Subscription>" not in page.text  # escaped in the title and in the JSON block
    assert embedded(page)["template"]["details"]["title"] == DETAILS["title"]


def test_the_editor_page_has_nothing_inline(local, services):
    page = local.get(f"/templates/{make(services)['id']}/edit")
    policy = page.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "unsafe-inline" not in policy
    assert "img-src 'self' data:" in policy  # the page images come from the studio itself
    assert " style=" not in page.text and "<script>" not in page.text


def test_the_record_page_links_to_the_editor(local, services):
    template = make(services)
    record = local.get(f"/templates/{template['id']}")
    assert f'href="/templates/{template["id"]}/edit">Edit fields</a>' in record.text


@pytest.mark.parametrize("template_id", ["a" * 32, "not-an-id"])
def test_an_unknown_template_is_404(local, template_id):
    page = local.get(f"/templates/{template_id}/edit")
    assert page.status_code == 404 and "No such template" in page.text


def test_a_template_that_cannot_be_read_says_so(local, services, monkeypatch):
    template = make(services)

    def fail(*args):
        raise StorageError("down")

    monkeypatch.setattr(services.templates, "get", fail)
    page = local.get(f"/templates/{template['id']}/edit")
    assert page.status_code == 503 and "could not be loaded" in page.text


def test_a_source_record_gone_after_the_template_was_read_says_so(local, services, monkeypatch):
    template = make(services)
    real = sources.load
    calls = []
    monkeypatch.setattr(
        sources, "load", lambda *a: None if calls.append(a) or len(calls) > 1 else real(*a)
    )
    page = local.get(f"/templates/{template['id']}/edit")
    assert page.status_code == 503 and "sealed source" in page.text


def test_a_source_with_no_pages_has_nothing_to_edit(local, services):
    empty = pypdfium2.PdfDocument.new()
    out = io.BytesIO()
    empty.save(out)
    source = sources.seal(services.store, out.getvalue())  # sealed, with an INCOMPLETE inventory
    template = services.templates.create(source["sha256"], POLICY, DETAILS)
    page = local.get(f"/templates/{template['id']}/edit")
    assert page.status_code == 422 and "no pages" in page.text
    assert "data-editor-data" not in page.text


def test_a_visitor_without_a_session_is_sent_to_sign_in(visitor, hosted):
    template = make(hosted)
    answer = visitor.get(f"/templates/{template['id']}/edit", follow_redirects=False)
    assert answer.status_code == 303 and answer.headers["location"] == "/auth/login"


def test_a_signed_in_browser_gets_the_editor(signed_in, hosted):
    template = make(hosted)
    page = signed_in.get(f"/templates/{template['id']}/edit")
    assert page.status_code == 200 and embedded(page)["template"]["id"] == template["id"]


def test_the_editor_script_is_served(local):
    script = local.get("/static/editor.js")
    assert script.status_code == 200 and "save_id" in script.text
