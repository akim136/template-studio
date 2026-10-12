import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import app
from studio.state import api, identity, r2
from tests.state.helpers import POLICY, Clock, field, two_page_pdf
from tests.state.test_identity import FakeVercel
from tests.state.test_r2 import CREDENTIALS, FakeR2

ORIGIN = "https://studio.example.test"
AUTH = identity.AuthConfig("synthetic-client", "synthetic-client-secret", "s" * 40, ORIGIN)
EMAIL = "reviewer@example.test"
SAME_ORIGIN = {"origin": ORIGIN}
HOSTED = {
    "R2_ACCOUNT_ID": "0" * 32,
    "R2_BUCKET": "template-studio",
    "R2_ACCESS_KEY_ID": "synthetic-key-id",
    "R2_SECRET_ACCESS_KEY": "synthetic-r2-secret",
    "VERCEL_OAUTH_CLIENT_ID": "synthetic-client",
    "VERCEL_OAUTH_CLIENT_SECRET": "synthetic-client-secret",
    "STUDIO_SESSION_SECRET": "s" * 40,
    "STUDIO_PUBLIC_ORIGIN": ORIGIN,
}


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def bucket():
    return FakeR2()


@pytest.fixture
def hosted(bucket, clock):
    store = r2.R2ObjectStore(CREDENTIALS, bucket, clock=clock, sleep=lambda s: None)
    return api.hosted_services(store, AUTH, clock)


def client_for(services, monkeypatch, base_url=ORIGIN):
    monkeypatch.setattr(app.state, "services", services)
    return TestClient(app, base_url=base_url, client=("203.0.113.9", 50000))


@pytest.fixture
def anonymous(hosted, monkeypatch):
    with client_for(hosted, monkeypatch) as client:
        yield client


@pytest.fixture
def client(anonymous, clock):
    session = {"sub": "user-1", "email": EMAIL, "name": "R"}
    cookie = identity.seal(
        AUTH.session_secret, identity.SESSION_COOKIE, session, clock(), timedelta(hours=8)
    )
    anonymous.cookies.set(identity.SESSION_COOKIE, cookie)
    return anonymous


def uploaded(client, bucket, data):
    """Asks for an upload URL, then does what the browser does with it."""
    sha256 = hashlib.sha256(data).hexdigest()
    body = {"sha256": sha256, "size": len(data)}
    answer = client.post("/api/uploads", json=body, headers=SAME_ORIGIN)
    assert answer.status_code == 201, answer.text
    bucket.objects[urlsplit(answer.json()["url"]).path.split("/", 2)[2]] = data
    return sha256


def bound(snapshot):
    return {"revision": snapshot["revision"], "binding": snapshot["binding"]}


def test_sign_in_with_vercel_then_every_sign_off_carries_the_verified_email(
    anonymous, bucket, monkeypatch
):
    started = anonymous.get("/auth/login", follow_redirects=False)
    assert started.status_code == 303
    authorize = urlsplit(started.headers["location"])
    assert f"{authorize.scheme}://{authorize.netloc}{authorize.path}" == identity.AUTHORIZE_URL
    login_cookie = started.headers["set-cookie"].lower()
    assert all(
        part in login_cookie for part in ("path=/auth", "httponly", "secure", "samesite=lax")
    )
    state = parse_qs(authorize.query)["state"][0]
    monkeypatch.setattr(identity, "urllib_transport", FakeVercel())
    back = anonymous.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"] == "/auth/me"
    session_cookie = next(
        c.lower()
        for c in back.headers.get_list("set-cookie")
        if c.startswith(identity.SESSION_COOKIE)
    )
    for part in ("path=/", "httponly", "secure", "samesite=lax", "max-age=28800"):
        assert part in session_cookie
    assert anonymous.get("/auth/me").json() == {
        "email": EMAIL,
        "name": "R",
        "identity": "Sign in with Vercel",
    }

    sha256 = uploaded(anonymous, bucket, two_page_pdf())
    sealed = anonymous.post(f"/api/uploads/{sha256}/seal", headers=SAME_ORIGIN)
    assert sealed.status_code == 201 and sealed.json()["sha256"] == sha256
    body = {"source_sha256": sha256, "policy": POLICY}
    template = anonymous.post("/api/templates", json=body, headers=SAME_ORIGIN).json()
    url = f"/api/templates/{template['id']}"
    save = {"base_revision": 1, "policy": POLICY, "fields": [field()], "save_id": "request-0001"}
    current = anonymous.put(url, json=save, headers=SAME_ORIGIN).json()["template"]
    assert (
        anonymous.post(f"{url}/review", json=bound(current), headers=SAME_ORIGIN).status_code == 200
    )
    approved = anonymous.post(f"{url}/approval", json=bound(current), headers=SAME_ORIGIN).json()
    assert approved["review"]["by"] == EMAIL and approved["approval"]["by"] == EMAIL

    out = anonymous.post("/auth/logout", headers=SAME_ORIGIN)
    cleared = out.headers["set-cookie"].lower()
    assert cleared.startswith(identity.SESSION_COOKIE) and "max-age=0" in cleared
    assert out.json()["note"] == "signed out of the studio; this does not sign you out of Vercel"
    assert anonymous.get("/auth/me").status_code == 401
    assert anonymous.get(url).status_code == 401


def test_a_reviewer_name_in_the_body_is_refused_not_trusted(client, bucket):
    sha256 = uploaded(client, bucket, two_page_pdf())
    client.post(f"/api/uploads/{sha256}/seal", headers=SAME_ORIGIN)
    body = {"source_sha256": sha256, "policy": POLICY}
    template = client.post("/api/templates", json=body, headers=SAME_ORIGIN).json()
    claimed = {**bound(template), "by": "someone-else@example.test"}
    url = f"/api/templates/{template['id']}/review"
    assert client.post(url, json=claimed, headers=SAME_ORIGIN).status_code == 422


def test_sign_in_started_on_another_deployment_host_moves_to_the_public_origin(hosted, monkeypatch):
    with client_for(hosted, monkeypatch, "https://preview.example.test") as client:
        moved = client.get("/auth/login", follow_redirects=False)
    assert moved.status_code == 303 and moved.headers["location"] == f"{ORIGIN}/auth/login"
    assert "set-cookie" not in moved.headers


@pytest.mark.parametrize(
    "query", ["code=c&state=forged", "error=access_denied&state=x", "state=x", ""]
)
def test_a_callback_that_does_not_complete_this_browsers_sign_in_sets_no_session(
    anonymous, monkeypatch, query
):
    anonymous.get("/auth/login", follow_redirects=False)
    vercel = FakeVercel()
    monkeypatch.setattr(identity, "urllib_transport", vercel)
    refused = anonymous.get(f"/auth/callback?{query}", follow_redirects=False)
    assert refused.status_code == 400 and identity.SESSION_COOKIE not in refused.headers.get(
        "set-cookie", ""
    )
    assert vercel.calls == []


def test_without_a_valid_session_state_answers_401(anonymous, clock):
    url = f"/api/templates/{'0' * 32}"
    assert anonymous.get(url).status_code == 401
    assert anonymous.post("/api/uploads", json={}, headers=SAME_ORIGIN).status_code == 401
    session = {"sub": "u", "email": EMAIL, "name": ""}
    forged = identity.seal("x" * 40, identity.SESSION_COOKIE, session, clock(), timedelta(hours=1))
    anonymous.cookies.set(identity.SESSION_COOKIE, forged)
    assert anonymous.get(url).json() == {"detail": api.NOT_SIGNED_IN}
    login = identity.seal(
        AUTH.session_secret, identity.LOGIN_COOKIE, session, clock(), timedelta(hours=1)
    )
    anonymous.cookies.set(identity.SESSION_COOKIE, login)  # a login cookie is not a session
    assert anonymous.get(url).status_code == 401


def test_the_session_is_checked_before_any_body_is_read(anonymous):
    oversized = b"x" * (api.MAX_JSON_BYTES + 1)
    url = f"/api/templates/{'0' * 32}"
    answer = anonymous.put(url, content=oversized, headers=SAME_ORIGIN)
    assert answer.status_code == 401


def test_a_malformed_cookie_is_not_a_session_or_a_sign_in(anonymous, monkeypatch):
    anonymous.cookies.set(identity.SESSION_COOKIE, "A.A")
    assert anonymous.get(f"/api/templates/{'0' * 32}").status_code == 401
    assert anonymous.get("/auth/me").status_code == 401
    anonymous.cookies.set(identity.LOGIN_COOKIE, "A.A", path="/auth")
    monkeypatch.setattr(identity, "urllib_transport", FakeVercel())
    assert anonymous.get("/auth/callback?code=c&state=s").status_code == 400


def test_a_session_ends_after_eight_hours(client, clock):
    url = f"/api/templates/{'0' * 32}"
    assert client.get(url).status_code == 404
    clock.advance(hours=8)
    assert client.get(url).status_code == 401
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize(
    "headers", [{}, {"origin": "https://elsewhere.example.test"}, {"origin": "null"}]
)
def test_a_change_from_anywhere_but_the_studios_own_pages_is_refused(client, headers):
    assert client.post("/api/uploads", json={}, headers=headers).json() == {
        "detail": api.CROSS_ORIGIN
    }
    assert client.post("/auth/logout", headers=headers).status_code == 403


def test_an_upload_url_is_for_one_hash_and_one_length(client):
    data = two_page_pdf()
    sha256 = hashlib.sha256(data).hexdigest()
    body = {"sha256": sha256, "size": len(data)}
    answer = client.post("/api/uploads", json=body, headers=SAME_ORIGIN).json()
    assert answer["method"] == "PUT" and answer["expires_in"] == r2.UPLOAD_EXPIRY_S
    url = urlsplit(answer["url"])
    assert url.netloc == CREDENTIALS.host
    assert url.path == f"/template-studio/uploads/{sha256}.pdf"
    assert parse_qs(url.query)["X-Amz-SignedHeaders"] == ["content-length;host"]
    assert "synthetic-secret" not in answer["url"]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"sha256": "0" * 64, "size": api.MAX_SOURCE_BYTES + 1}, 413),
        ({"sha256": "0" * 64, "size": 0}, 422),
        ({"sha256": "0" * 63, "size": 10}, 422),
        ({"sha256": "A" * 64, "size": 10}, 422),
        ({"sha256": "0" * 64, "size": "10"}, 422),
    ],
)
def test_upload_requests_are_bounded_and_strict(client, body, status):
    assert client.post("/api/uploads", json=body, headers=SAME_ORIGIN).status_code == status


def test_sealing_rereads_and_rehashes_the_uploaded_bytes(client, bucket):
    data = two_page_pdf()
    sha256 = hashlib.sha256(data).hexdigest()
    seal = f"/api/uploads/{sha256}/seal"
    assert client.post(seal, headers=SAME_ORIGIN).status_code == 404
    bucket.objects[f"uploads/{sha256}.pdf"] = data + b"%"  # not the bytes the hash names
    refused = client.post(seal, headers=SAME_ORIGIN)
    assert refused.status_code == 422 and "do not match" in refused.text
    bucket.objects[f"uploads/{sha256}.pdf"] = data
    assert client.post(seal, headers=SAME_ORIGIN).status_code == 201
    assert f"sources/{sha256}.pdf" in bucket.objects


def test_an_uploaded_file_that_is_not_a_pdf_is_refused_at_seal(client, bucket):
    sha256 = uploaded(client, bucket, b"not a pdf")
    refused = client.post(f"/api/uploads/{sha256}/seal", headers=SAME_ORIGIN)
    assert refused.status_code == 422


def test_a_storage_failure_while_sealing_says_to_reload(client, bucket):
    sha256 = uploaded(client, bucket, two_page_pdf())
    bucket.script = [403]
    failed = client.post(f"/api/uploads/{sha256}/seal", headers=SAME_ORIGIN)
    assert failed.status_code == 503 and "reload and try again" in failed.json()["detail"]


def test_local_state_has_no_direct_upload_and_no_sign_in(services, monkeypatch):
    monkeypatch.setattr(app.state, "services", services)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        body = {"sha256": "0" * 64, "size": 10}
        assert client.post("/api/uploads", json=body).status_code == 409
        for method, path in [("GET", "/auth/login"), ("GET", "/auth/me"), ("POST", "/auth/logout")]:
            answer = client.request(method, path, follow_redirects=False)
            assert answer.json() == {"detail": api.SIGN_IN_NOT_CONFIGURED}


def test_hosted_state_is_configured_from_every_setting(tmp_path):
    configuration = api.configure({**HOSTED, "VERCEL": "1"}, tmp_path)
    assert configuration.state == "r2" and configuration.problems == ()
    assert isinstance(configuration.services.store, r2.R2ObjectStore)
    assert configuration.services.auth.origin == ORIGIN
    off_vercel = api.configure(HOSTED, tmp_path)
    assert off_vercel.state == "r2"
    local_wins = api.configure({**HOSTED, api.STATE_DIR_ENV: str(tmp_path / "s")}, tmp_path / "r")
    assert local_wins.state == "local"
    on_vercel = {**HOSTED, api.STATE_DIR_ENV: str(tmp_path / "s"), "VERCEL": "1"}
    assert api.configure(on_vercel, tmp_path / "r").state == "r2"  # local disk never on Vercel


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"R2_BUCKET": ""}, "R2_BUCKET is missing"),
        ({"STUDIO_SESSION_SECRET": '"' + "s" * 40 + '"'}, "has stray spaces or quotes"),
        ({"R2_SECRET_ACCESS_KEY": "synthetic-r2-secret\n"}, "has stray spaces or quotes"),
        ({"R2_ACCOUNT_ID": "Z" * 32}, "R2_ACCOUNT_ID is not"),
        ({"R2_BUCKET": "Template_Studio"}, "R2_BUCKET is not"),
        ({"STUDIO_SESSION_SECRET": "short-synthetic"}, "STUDIO_SESSION_SECRET is shorter"),
        ({"STUDIO_PUBLIC_ORIGIN": "http://studio.example.test"}, "STUDIO_PUBLIC_ORIGIN is not"),
        ({"STUDIO_PUBLIC_ORIGIN": f"{ORIGIN}/"}, "STUDIO_PUBLIC_ORIGIN is not"),
        ({"STUDIO_PUBLIC_ORIGIN": "https://:443"}, "STUDIO_PUBLIC_ORIGIN is not"),
        ({"STUDIO_PUBLIC_ORIGIN": "https://Studio.example.test"}, "STUDIO_PUBLIC_ORIGIN is not"),
        ({"STUDIO_PUBLIC_ORIGIN": f"{ORIGIN}:443"}, "STUDIO_PUBLIC_ORIGIN is not"),
        ({"STUDIO_PUBLIC_ORIGIN": f"{ORIGIN}:abc"}, "STUDIO_PUBLIC_ORIGIN is not"),
        ({"STUDIO_PUBLIC_ORIGIN": f"{ORIGIN}?"}, "STUDIO_PUBLIC_ORIGIN is not"),
        (
            {"STUDIO_PUBLIC_ORIGIN": "https://user@studio.example.test"},
            "STUDIO_PUBLIC_ORIGIN is not",
        ),
    ],
)
def test_a_missing_or_malformed_setting_turns_state_off_and_is_named_never_shown(
    tmp_path, capsys, changes, problem
):
    environ = {**HOSTED, **changes, "VERCEL": "1"}
    configuration = api.configure(environ, tmp_path)
    assert configuration.services is None and configuration.state == "not_configured"
    assert any(problem in line for line in configuration.problems)
    api.report(configuration)
    printed = capsys.readouterr().err + " ".join(configuration.problems)
    assert problem in printed
    for name, value in changes.items():
        assert not value.strip() or value.strip() not in printed, name


def test_on_vercel_with_nothing_set_every_setting_is_named(tmp_path):
    configuration = api.configure({"VERCEL": "1"}, tmp_path)
    assert configuration.problems == tuple(f"{name} is missing" for name in api.HOSTED_ENV)


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:8771", "http://127.0.0.1:8771", "http://[::1]:8771", f"{ORIGIN}:8443"],
)
def test_canonical_origins_are_accepted(tmp_path, origin):
    environ = {**HOSTED, "STUDIO_PUBLIC_ORIGIN": origin}
    assert api.configure(environ, tmp_path).problems == ()
