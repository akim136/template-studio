import asyncio
import dataclasses
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import app
from studio.state import api
from studio.state.store import StorageError
from tests.state.helpers import POLICY, field, two_page_pdf
from tests.test_health import STATE_ROUTES

ROOT = Path(__file__).resolve().parents[2]
SAVE_ID = "request-0001"


@pytest.fixture
def client(services, monkeypatch):
    monkeypatch.setattr(app.state, "services", services)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client


@pytest.fixture
def template(client):
    sealed = client.post("/api/sources", content=two_page_pdf()).json()
    response = client.post(
        "/api/templates", json={"source_sha256": sealed["sha256"], "policy": POLICY}
    )
    assert response.status_code == 201
    return response.json()


def save(client, template_id, base, fields, policy=POLICY, save_id=None):
    save_id = save_id or uuid.uuid4().hex
    body = {"base_revision": base, "policy": policy, "fields": fields, "save_id": save_id}
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


def test_a_retried_request_returns_the_save_that_landed(client, template):
    first = save(client, template["id"], 1, [field()], save_id="request-0001").json()
    again = save(client, template["id"], 1, [field()], save_id="request-0001")
    assert again.status_code == 200 and again.json()["new_revision"] is False
    assert again.json()["template"] == first["template"]
    assert save(client, template["id"], 1, [field()]).status_code == 409  # another request


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
        {"base_revision": "1", "policy": POLICY, "fields": [], "save_id": SAVE_ID},
        {"base_revision": True, "policy": POLICY, "fields": [], "save_id": SAVE_ID},
        {"base_revision": 1, "policy": POLICY, "fields": [], "save_id": SAVE_ID, "saved": True},
        {"base_revision": 1, "fields": [], "save_id": SAVE_ID},
        {"base_revision": 1, "policy": POLICY, "fields": [], "save_id": "short"},
        {"base_revision": 1, "policy": POLICY, "fields": []},  # no save_id
    ],
)
def test_request_bodies_are_strict(client, template, body):
    assert client.put(f"/api/templates/{template['id']}", json=body).status_code == 422


def test_a_storage_failure_answers_503_and_never_claims_the_outcome(
    client, template, services, monkeypatch
):
    def failing(*args):
        raise StorageError("cannot write head.json: No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(services.store, "replace", failing)
        response = save(client, template["id"], 1, [field()])
    assert response.status_code == 503
    assert "may or may not have been saved, so reload before retrying" in response.json()["detail"]
    assert client.get(f"/api/templates/{template['id']}").json()["revision"] == 1


def test_an_orphan_list_that_cannot_be_read_after_a_create_never_says_it_failed(
    client, template, services, monkeypatch
):
    url = f"/api/templates/{template['id']}"
    current = save(client, template["id"], 1, [field()]).json()["template"]
    client.post(f"{url}/review", json=bound(current))
    approved = client.post(f"{url}/approval", json=bound(current)).json()

    def failing(template_id):
        raise StorageError("cannot read orphans/index.json: Input/output error")

    monkeypatch.setattr(services.operations, "orphans", failing)
    created = client.post(f"{url}/operation", json=bound(approved))
    assert created.status_code == 503
    assert "may or may not have been saved, so reload before retrying" in created.json()["detail"]
    assert client.get(f"{url}/operation").json()["detail"].startswith("storage failed; reload")


def test_a_storage_failure_on_a_read_says_to_reload(client, template, services, monkeypatch):
    def failing(key):
        raise StorageError("cannot read head.json: Input/output error")

    monkeypatch.setattr(services.store, "get", failing)
    response = client.get(f"/api/templates/{template['id']}")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail.startswith("storage failed; reload and try again") and "saved" not in detail


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "evil.example"},  # a name rebound to this machine
        {"host": "192.168.1.20:8771"},  # reached over the network
        {"origin": "https://evil.example"},  # another site's page
        {"origin": "null"},
    ],
)
def test_state_answers_only_loopback_requests_from_loopback_pages(client, template, headers):
    response = client.get(f"/api/templates/{template['id']}", headers=headers)
    assert response.status_code == 403 and "loopback" in response.json()["detail"]
    upload = client.post("/api/sources", content=two_page_pdf(), headers=headers)
    assert upload.status_code == 403


@pytest.mark.parametrize("peer", ["192.168.1.20", "testclient", None])
def test_a_peer_that_is_not_on_this_machine_is_refused_whatever_its_host_header(services, peer):
    url = f"/api/templates/{'0' * 32}"
    reached, sent = gate(services, "GET", url, never_read, peer=peer)  # Host: 127.0.0.1
    assert reached == [] and sent[0]["status"] == 403


@pytest.mark.parametrize("host", ["127.0.0.1:8771", "localhost:8771", "[::1]:8771", "LOCALHOST"])
def test_loopback_hosts_and_pages_are_answered(client, template, host):
    headers = {"host": host, "origin": "http://localhost:8771"}
    assert client.get(f"/api/templates/{template['id']}", headers=headers).status_code == 200


def test_uploads_are_bounded_and_must_be_readable(client, monkeypatch):
    monkeypatch.setattr(api, "MAX_JSON_BYTES", 100)  # an upload has its own, larger bound
    assert client.post("/api/sources", content=two_page_pdf()).status_code == 201
    monkeypatch.setattr(api, "MAX_SOURCE_BYTES", 100)
    assert client.post("/api/sources", content=b"x" * 101).status_code == 413
    unreadable = client.post("/api/sources", content=b"%PDF-1.7 nope")
    assert unreadable.status_code == 422


def test_an_unknown_template_answers_404(client):
    assert client.get(f"/api/templates/{'0' * 32}").status_code == 404
    assert client.get("/api/templates/not-an-id").status_code == 404
    assert client.get(f"/api/templates/{'0' * 32}/operation").status_code == 404


@pytest.mark.parametrize("method, path", sorted(STATE_ROUTES))
def test_without_configured_state_every_state_route_answers_503_before_reading_a_body(
    monkeypatch, method, path
):
    monkeypatch.setattr(app.state, "services", None)
    url = path.replace("{template_id}", "0" * 32)
    headers = {"content-type": "application/json"}
    response = TestClient(app).request(method, url, content=b"{not json", headers=headers)
    assert response.status_code == 503, url
    assert "not configured" in response.json()["detail"]


def test_json_bodies_are_bounded_by_declared_and_by_received_length(client, template, monkeypatch):
    monkeypatch.setattr(api, "MAX_JSON_BYTES", 200)
    url = f"/api/templates/{template['id']}"
    declared = save(client, template["id"], 1, [field()] * 3)
    assert declared.status_code == 413 and "at most 200 bytes" in declared.json()["detail"]
    chunks = iter([b'{"base_revision": 1, ', b" " * 300, b"}"])  # sent without a length
    headers = {"content-type": "application/json"}
    assert client.put(url, content=chunks, headers=headers).status_code == 413
    assert client.get(url).json()["revision"] == 1
    assert save(client, template["id"], 1, []).status_code == 200


def test_state_is_configured_only_on_local_disk_outside_the_repository(tmp_path):
    assert api.services_from_environment({}, ROOT) is None
    local = {api.STATE_DIR_ENV: str(tmp_path / "state")}
    assert api.services_from_environment(local, ROOT) is not None
    assert api.services_from_environment({**local, "VERCEL": "1"}, ROOT) is None
    with pytest.raises(RuntimeError, match="outside the repository"):
        api.services_from_environment({api.STATE_DIR_ENV: str(ROOT / "state")}, ROOT)
    (tmp_path / "real").mkdir(mode=0o700)
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(StorageError, match="not a link"):
        api.services_from_environment({api.STATE_DIR_ENV: str(tmp_path / "link")}, ROOT)


def gate(services, method, path, receive, host="127.0.0.1", peer="127.0.0.1"):
    """Calls StateGate directly, so a test sees exactly what it reads and sends."""
    reached, sent = [], []

    async def app_behind(scope, receive, send):
        reached.append(scope["path"])

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", host.encode()), (b"content-length", b"9999999")],
        "client": None if peer is None else (peer, 50000),
        "app": SimpleNamespace(state=SimpleNamespace(services=services)),
    }
    asyncio.run(api.StateGate(app_behind)(scope, receive, send))
    return reached, sent


async def never_read():
    raise AssertionError("the gate read the body")


@pytest.mark.parametrize(
    "configured, host, status",
    [(False, "127.0.0.1", 503), (True, "evil.example", 403), (True, "127.0.0.1", 413)],
)
def test_the_gate_answers_without_reading_the_body(services, configured, host, status):
    url = f"/api/templates/{'0' * 32}"
    reached, sent = gate(services if configured else None, "PUT", url, never_read, host)
    assert reached == [] and sent[0]["status"] == status


def test_a_client_that_leaves_mid_body_gets_no_answer_and_reaches_nothing(services, monkeypatch):
    monkeypatch.setattr(api, "MAX_JSON_BYTES", 10**8)  # the declared length is within bounds
    messages = iter([{"type": "http.request", "body": b"{", "more_body": True}])

    async def receive():
        return next(messages, {"type": "http.disconnect"})

    reached, sent = gate(services, "PUT", f"/api/templates/{'0' * 32}", receive)
    assert reached == [] and sent == []


def test_an_orphan_is_shown_on_every_operation_answer_and_blocks_create_until_resolved(
    services, clock, monkeypatch
):
    from studio.state.signing import ORPHANED, Operations
    from tests.state.test_signing import Scripted, replaced_by_a_create

    adapter = Scripted()
    operations = Operations(services.store, services.templates, adapter, clock)
    monkeypatch.setattr(app.state, "services", dataclasses.replace(services, operations=operations))
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        sealed = client.post("/api/sources", content=two_page_pdf()).json()
        body = {"source_sha256": sealed["sha256"], "policy": POLICY}
        template = client.post("/api/templates", json=body).json()
        url = f"/api/templates/{template['id']}"
        current = save(client, template["id"], 1, [field()]).json()["template"]
        client.post(f"{url}/review", json=bound(current))
        approved = client.post(f"{url}/approval", json=bound(current)).json()
        adapter.during_create = lambda: replaced_by_a_create(operations, clock, approved)
        kept = client.post(f"{url}/operation", json=bound(approved)).json()
        shown = client.get(f"{url}/operation").json()
        assert shown["warning"] == ORPHANED and [o["id"] for o in shown["orphans"]] == [kept["id"]]
        assert client.post(f"{url}/operation/status-check").json()["warning"] == ORPHANED
        later = save(client, template["id"], 2, [field(x=80.0)]).json()["template"]
        client.post(f"{url}/review", json=bound(later))
        client.post(f"{url}/approval", json=bound(later))
        refused = client.post(f"{url}/operation", json=bound(later))
        assert refused.status_code == 409 and refused.json()["detail"]["reason"] == ORPHANED
        resolve = f"{url}/operation/orphans/{kept['id']}/resolve"
        claimed = {"note": "checked", "by": "someone@example.test"}
        assert client.post(resolve, json=claimed).status_code == 422
        assert (
            client.post(
                f"{url}/operation/orphans/{'0' * 32}/resolve", json={"note": "x"}
            ).status_code
            == 404
        )
        resolved = client.post(resolve, json={"note": "the provider holds both"}).json()
        assert resolved["status"] == "resolved" and resolved["resolution"]["by"] == "anonymous"
        assert "warning" not in client.get(f"{url}/operation").json()
        created = client.post(f"{url}/operation", json=bound(later)).json()
        assert created["state"] == "CREATED" and created["orphans"] == []
