import base64
import hashlib
import http.client
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from studio.state import identity
from tests.state.helpers import Clock

CONFIG = identity.AuthConfig(
    "synthetic-client", "synthetic-client-secret", "s" * 40, "https://studio.example.test"
)
CLAIMS = {"sub": "user-1", "email": "Reviewer@Example.test", "email_verified": True, "name": "R"}
FORGED_BODY = base64.urlsafe_b64encode(b'{"a":2,"exp":9999999999}').decode().rstrip("=")


class FakeVercel:
    """Answers Sign in with Vercel's token and userinfo endpoints with synthetic values."""

    def __init__(self, claims=None, token_status=200, failure=None):
        self.claims = CLAIMS if claims is None else claims
        self.token_status = token_status
        self.failure = failure
        self.calls = []

    def __call__(self, url, form, headers, timeout):
        self.calls.append((url, form, dict(headers)))
        if self.failure:
            raise self.failure
        if url == identity.TOKEN_URL:
            return self.token_status, json.dumps({"access_token": "synthetic-access"}).encode()
        assert headers["authorization"] == "Bearer synthetic-access"
        return 200, json.dumps(self.claims).encode()


def started(clock):
    url, cookie = identity.start(CONFIG, clock())
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}, cookie


def test_a_sealed_value_reads_back_until_it_expires():
    clock = Clock()
    token = identity.seal("k" * 40, "p", {"a": 1}, clock(), timedelta(minutes=5))
    assert identity.unseal("k" * 40, "p", token, clock())["a"] == 1
    clock.advance(minutes=5)
    assert identity.unseal("k" * 40, "p", token, clock()) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t[:-2] + ("AA" if t[-2:] != "AA" else "BB"),  # a forged signature
        lambda t: FORGED_BODY + t[t.index(".") :],  # another payload under the old signature
        lambda t: t.replace(".", ""),
        lambda t: "",
    ],
)
def test_a_changed_or_malformed_value_is_refused(mutate):
    clock = Clock()
    token = identity.seal("k" * 40, "p", {"a": 1}, clock(), timedelta(minutes=5))
    assert identity.unseal("k" * 40, "p", mutate(token), clock()) is None


def test_a_value_sealed_for_one_purpose_or_key_does_not_pass_for_another():
    clock = Clock()
    token = identity.seal("k" * 40, identity.LOGIN_COOKIE, {"a": 1}, clock(), timedelta(hours=1))
    assert identity.unseal("k" * 40, identity.SESSION_COOKIE, token, clock()) is None
    assert identity.unseal("x" * 40, identity.LOGIN_COOKIE, token, clock()) is None


def test_sign_in_starts_with_pkce_and_a_state_bound_to_this_browser():
    clock = Clock()
    query, cookie = started(clock)
    assert query["client_id"] == "synthetic-client"
    assert query["redirect_uri"] == "https://studio.example.test/auth/callback"
    assert query["response_type"] == "code" and query["code_challenge_method"] == "S256"
    assert query["scope"] == "openid email profile"
    login = identity.unseal(CONFIG.session_secret, identity.LOGIN_COOKIE, cookie, clock())
    challenge = base64.urlsafe_b64encode(hashlib.sha256(login["v"].encode()).digest())
    assert query["code_challenge"] == challenge.rstrip(b"=").decode()
    assert query["state"] == login["state"] and "synthetic-client-secret" not in cookie


def test_a_verified_email_becomes_the_session_identity():
    clock = Clock()
    query, cookie = started(clock)
    vercel = FakeVercel()
    actor, session = identity.finish(CONFIG, "code-1", query["state"], cookie, clock(), vercel)
    assert actor == identity.Actor("user-1", "reviewer@example.test", "R")
    assert identity.session_actor(CONFIG, session, clock()) == actor
    token_form = vercel.calls[0][1]
    login = identity.unseal(CONFIG.session_secret, identity.LOGIN_COOKIE, cookie, clock())
    assert token_form["code_verifier"] == login["v"] and token_form["code"] == "code-1"
    assert token_form["redirect_uri"] == CONFIG.redirect_uri
    clock.advance(hours=8)
    assert identity.session_actor(CONFIG, session, clock()) is None


@pytest.mark.parametrize(
    ("claims", "message"),
    [
        ({**CLAIMS, "email_verified": False}, "not verified"),
        ({k: v for k, v in CLAIMS.items() if k != "email_verified"}, "not verified"),
        ({k: v for k, v in CLAIMS.items() if k != "email"}, "email scope"),
        ({**CLAIMS, "sub": ""}, "email scope"),
    ],
)
def test_no_session_without_a_verified_email(claims, message):
    clock = Clock()
    query, cookie = started(clock)
    with pytest.raises(identity.SignInFailed, match=message):
        identity.finish(CONFIG, "code-1", query["state"], cookie, clock(), FakeVercel(claims))


def test_a_response_for_another_browser_or_an_expired_start_is_refused_before_any_call():
    clock = Clock()
    query, cookie = started(clock)
    vercel = FakeVercel()
    with pytest.raises(identity.SignInFailed, match="does not match"):
        identity.finish(CONFIG, "code-1", "another-state", cookie, clock(), vercel)
    with pytest.raises(identity.SignInFailed, match="does not match"):
        identity.finish(CONFIG, None, query["state"], cookie, clock(), vercel)
    with pytest.raises(identity.SignInFailed, match="does not match"):
        identity.finish(CONFIG, "code-1", "\u00e9", cookie, clock(), vercel)
    clock.advance(minutes=10)
    with pytest.raises(identity.SignInFailed, match="expired"):
        identity.finish(CONFIG, "code-1", query["state"], cookie, clock(), vercel)
    assert vercel.calls == []


@pytest.mark.parametrize(
    ("vercel", "message"),
    [
        (FakeVercel(token_status=400), r"refused the code exchange \(400\)"),
        (FakeVercel(failure=TimeoutError("synthetic")), "did not answer the code exchange"),
        (FakeVercel(failure=http.client.IncompleteRead(b"")), "did not answer the code exchange"),
    ],
)
def test_vercel_failures_are_reported_without_secrets(vercel, message):
    clock = Clock()
    query, cookie = started(clock)
    with pytest.raises(identity.SignInFailed, match=message) as raised:
        identity.finish(CONFIG, "code-1", query["state"], cookie, clock(), vercel)
    assert "synthetic-client-secret" not in str(raised.value)


def test_the_configuration_never_shows_its_secrets():
    assert "synthetic-client-secret" not in repr(CONFIG) and "s" * 40 not in repr(CONFIG)
