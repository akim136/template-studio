import hashlib
import json
import uuid

import pytest

from studio.state import sources
from studio.state.errors import Invalid
from studio.state.store import canonical
from studio.state.templates import DETAILS, parse_details
from tests.state.helpers import POLICY, field, two_page_pdf

TYPED = {
    "title": "Fund II - Subscription",
    "sponsor_name": "Acme Capital",
    "sponsor_account_id": "acct_42",
}


def bound(snapshot):
    return {"revision": snapshot["revision"], "binding": snapshot["binding"]}


def new_template(client, details=TYPED):
    sealed = client.post("/api/sources", content=two_page_pdf()).json()
    body = {"source_sha256": sealed["sha256"], "policy": POLICY}
    if details is not None:
        body["details"] = details
    answer = client.post("/api/templates", json=body)
    assert answer.status_code == 201, answer.text
    return answer.json()


def put(client, snapshot, fields, details=None):
    body = {
        "base_revision": snapshot["revision"],
        "policy": POLICY,
        "fields": fields,
        "save_id": uuid.uuid4().hex,
    }
    if details is not None:
        body["details"] = details
    return client.put(f"/api/templates/{snapshot['id']}", json=body)


def test_typed_details_are_trimmed_and_saved_with_revision_one(local):
    messy = {**TYPED, "title": "  Fund II \t- Subscription ", "sponsor_name": "Acme   Capital"}
    template = new_template(local, messy)
    assert template["details"] == TYPED
    assert local.get(f"/api/templates/{template['id']}").json()["details"] == TYPED


def test_a_template_created_without_details_has_blank_ones(local):
    assert new_template(local, details=None)["details"] == DETAILS


@pytest.mark.parametrize(
    "details",
    [
        {"title": "x"},
        {**TYPED, "extra": "x"},
        {**TYPED, "title": 7},
        {**TYPED, "title": "x" * 201},
        {**TYPED, "sponsor_name": "line\u0000break"},
        {**TYPED, "sponsor_account_id": "has space"},
        {**TYPED, "sponsor_account_id": "-leading"},
        {**TYPED, "sponsor_account_id": "a" * 65},
    ],
)
def test_malformed_details_are_refused(local, details):
    sealed = local.post("/api/sources", content=two_page_pdf()).json()
    body = {"source_sha256": sealed["sha256"], "policy": POLICY, "details": details}
    answer = local.post("/api/templates", json=body)
    assert answer.status_code == 422
    assert any("details" in error for error in answer.json()["detail"]["errors"])


def test_parse_details_allows_a_blank_account_id():
    assert parse_details({**TYPED, "sponsor_account_id": ""})["sponsor_account_id"] == ""
    with pytest.raises(Invalid):
        parse_details(None)


def test_a_save_without_details_keeps_them_and_one_with_details_changes_them(local):
    template = new_template(local)
    kept = put(local, template, [field()]).json()["template"]
    assert kept["details"] == TYPED
    renamed = {**TYPED, "title": "Fund II - Subscription (corrected)"}
    changed = put(local, kept, [field()], renamed).json()["template"]
    assert changed["details"] == renamed and changed["revision"] == 3


def test_details_are_part_of_the_binding_and_a_rename_clears_review(local):
    template = new_template(local)
    saved = put(local, template, [field()]).json()["template"]
    url = f"/api/templates/{saved['id']}"
    assert local.post(f"{url}/review", json=bound(saved)).status_code == 200
    renamed = put(local, saved, [field()], {**TYPED, "sponsor_account_id": "acct_43"})
    current = renamed.json()["template"]
    assert current["binding"] != saved["binding"]
    assert current["review"] is None and current["approval"] is None
    stale = local.post(f"{url}/review", json=bound(saved))
    assert stale.status_code == 409


def test_a_retried_save_with_the_same_details_returns_the_revision_it_made(local):
    template = new_template(local)
    body = {
        "base_revision": 1,
        "policy": POLICY,
        "fields": [field()],
        "save_id": "retry-0001",
        "details": TYPED,
    }
    first = local.put(f"/api/templates/{template['id']}", json=body).json()
    again = local.put(f"/api/templates/{template['id']}", json=body).json()
    assert first["new_revision"] and not again["new_revision"]
    assert again["template"]["revision"] == first["template"]["revision"] == 2


def test_a_revision_saved_before_details_existed_reads_as_blank(services):
    source = sources.seal(services.store, two_page_pdf())
    created = services.templates.create(source["sha256"], POLICY)
    head, _ = services.templates.head(created["id"])
    key = f"templates/{created['id']}/revisions/{head['object']}.json"
    old = json.loads(services.store.get(key).data)
    del old["details"]
    data = canonical(old)
    name = hashlib.sha256(data).hexdigest()
    services.store.put_new(f"templates/{created['id']}/revisions/{name}.json", data)
    head_key = f"templates/{created['id']}/head.json"
    stored = services.store.get(head_key)
    services.store.replace(head_key, canonical({**head, "object": name}), stored.etag)
    assert services.templates.get(created["id"])["details"] == DETAILS
    assert services.templates.summaries()[0]["details"] == DETAILS
