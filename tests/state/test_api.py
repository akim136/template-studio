from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from studio.state import api
from studio.state.store import StorageError
from tests.state.helpers import POLICY, field, two_page_pdf

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(services, monkeypatch):
    monkeypatch.setattr(app.state, "services", services)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def template(client):
    sealed = client.post("/api/sources", content=two_page_pdf()).json()
    response = client.post(
        "/api/templates", json={"source_sha256": sealed["sha256"], "policy": POLICY}
    )
    assert response.status_code == 201
    return response.json()


def save(client, template_id, base, fields, policy=POLICY):
    body = {"base_revision": base, "policy": policy, "fields": fields}
    return client.put(f"/api/templates/{template_id}", json=body)


def bound(snapshot):
    return {"revision": snapshot["revision"], "binding": snapshot["binding"]}


def test_the_whole_journey_from_upload_to_a_simulated_create(client):
    upload = client.post("/api/sources", content=two_page_pdf())
    assert upload.status_code == 201 and len(upload.json()["pages"]) == 2
    body = {"source_sha256": upload.json()["sha256"], "policy": POLICY}
    template = client.post("/api/templates", json=body).json()
    saved = save(client, template["id"], 1, [field()])
    assert saved.status_code == 200 and saved.json()["saved"] and saved.json()["new_revision"]
    current = saved.json()["template"]
    assert client.get(f"/api/templates/{template['id']}").json() == current
    url = f"/api/templates/{template['id']}"
    assert client.post(f"{url}/review", json=bound(current)).status_code == 200
    assert client.post(f"{url}/approval", json=bound(current)).json()["approval"] is not None
    created = client.post(f"{url}/operation", json=bound(current))
    assert created.status_code == 200 and created.json()["state"] == "CREATED"
    assert client.get(f"{url}/operation").json() == created.json()
    assert client.post(f"{url}/operation/status-check").json() == created.json()


def test_a_stale_save_answers_409_with_the_current_template(client, template):
    newer = save(client, template["id"], 1, [field()]).json()["template"]
    stale = save(client, template["id"], 1, [field(x=80.0)])
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"] == newer


def test_a_stale_approval_sent_directly_to_the_api_is_refused(client, template):
    url = f"/api/templates/{template['id']}"
    first = save(client, template["id"], 1, [field()]).json()["template"]
    client.post(f"{url}/review", json=bound(first))
    save(client, template["id"], 2, [field(x=80.0)])
    refused = client.post(f"{url}/approval", json=bound(first))
    assert refused.status_code == 409 and "stale" in refused.json()["detail"]["reason"]
    assert client.get(url).json()["approval"] is None


def test_invalid_fields_answer_422_with_reasons(client, template):
    response = save(client, template["id"], 1, [field(page=7)])
    assert response.status_code == 422
    assert "not in the source" in response.json()["detail"]["errors"][0]


@pytest.mark.parametrize(
    "body",
    [
        {"base_revision": "1", "policy": POLICY, "fields": []},
        {"base_revision": True, "policy": POLICY, "fields": []},
        {"base_revision": 1, "policy": POLICY, "fields": [], "saved": True},
        {"base_revision": 1, "fields": []},
    ],
)
def test_request_bodies_are_strict(client, template, body):
    assert client.put(f"/api/templates/{template['id']}", json=body).status_code == 422


def test_a_storage_failure_answers_503_and_nothing_is_saved(
    client, template, services, monkeypatch
):
    def failing(*args):
        raise StorageError("cannot write head.json: No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(services.store, "replace", failing)
        response = save(client, template["id"], 1, [field()])
    assert response.status_code == 503 and "nothing was saved" in response.json()["detail"]
    assert client.get(f"/api/templates/{template['id']}").json()["revision"] == 1


def test_uploads_are_bounded_and_must_be_readable(client, monkeypatch):
    monkeypatch.setattr(api, "MAX_SOURCE_BYTES", 100)
    assert client.post("/api/sources", content=b"x" * 101).status_code == 413
    unreadable = client.post("/api/sources", content=b"%PDF-1.7 nope")
    assert unreadable.status_code == 422


def test_an_unknown_template_answers_404(client):
    assert client.get(f"/api/templates/{'0' * 32}").status_code == 404
    assert client.get("/api/templates/not-an-id").status_code == 404
    assert client.get(f"/api/templates/{'0' * 32}/operation").status_code == 404


def test_without_configured_state_every_state_route_answers_503(monkeypatch):
    monkeypatch.setattr(app.state, "services", None)
    client = TestClient(app)
    some_id = "0" * 32
    for method, path in [
        ("post", "/api/sources"),
        ("get", f"/api/templates/{some_id}"),
        ("post", f"/api/templates/{some_id}/operation/status-check"),
    ]:
        assert getattr(client, method)(path).status_code == 503, path


def test_state_is_configured_only_on_local_disk_outside_the_repository(tmp_path):
    assert api.services_from_environment({}, ROOT) is None
    local = {api.STATE_DIR_ENV: str(tmp_path / "state")}
    assert api.services_from_environment(local, ROOT) is not None
    assert api.services_from_environment({**local, "VERCEL": "1"}, ROOT) is None
    with pytest.raises(RuntimeError, match="outside the repository"):
        api.services_from_environment({api.STATE_DIR_ENV: str(ROOT / "state")}, ROOT)
