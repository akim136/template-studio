"""Who is using the hosted studio: Sign in with Vercel, and a signed session cookie.

Vercel Authentication decides who may reach a deployment at all. It does not tell the app who the
visitor is, so the app asks Vercel through Sign in with Vercel (OAuth 2.0 authorization code with
PKCE), Alex's decision of October 12, 2026. The identity is the email Vercel's userinfo endpoint
returns, and only when Vercel marks it verified. Nothing a browser sends is trusted as a name or
an email, and the app keeps no list of people: who gets in stays Vercel Authentication's job.

The session is a cookie the server signs with STUDIO_SESSION_SECRET (HMAC-SHA256): HttpOnly,
Secure, SameSite=Lax, valid for SESSION_TTL. Signing out clears it; it does not sign anyone out of
Vercel. The OAuth state and PKCE verifier travel in a second signed cookie that lasts LOGIN_TTL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

AUTHORIZE_URL = "https://vercel.com/oauth/authorize"
TOKEN_URL = "https://api.vercel.com/login/oauth/token"
USERINFO_URL = "https://api.vercel.com/login/oauth/userinfo"
SCOPES = "openid email profile"
SESSION_COOKIE = "studio_session"
LOGIN_COOKIE = "studio_login"
SESSION_TTL = timedelta(hours=8)
LOGIN_TTL = timedelta(minutes=10)
TIMEOUT_S = 15
MIN_SECRET_CHARS = 32

# (url, form or None, headers, timeout) -> (status, body). Raises OSError when no answer arrives.
Transport = Callable[[str, Mapping[str, str] | None, Mapping[str, str], float], tuple[int, bytes]]


class SignInFailed(Exception):
    """Sign-in did not produce a verified identity. The message is safe to show."""


def urllib_transport(
    url: str, form: Mapping[str, str] | None, headers: Mapping[str, str], timeout: float
) -> tuple[int, bytes]:
    data = urllib.parse.urlencode(form).encode() if form is not None else b""
    request = urllib.request.Request(url, data=data, method="POST", headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


@dataclass(frozen=True)
class AuthConfig:
    client_id: str
    client_secret: str = field(repr=False)
    session_secret: str = field(repr=False)
    origin: str  # the public origin sign-in returns to, e.g. https://example.vercel.app

    @property
    def redirect_uri(self) -> str:
        return f"{self.origin}/auth/callback"


@dataclass(frozen=True)
class Actor:
    sub: str
    email: str
    name: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def seal(secret: str, purpose: str, payload: dict, now: datetime, ttl: timedelta) -> str:
    """Signs `payload` for one purpose (a session cookie cannot pass as a login cookie)."""
    body = _b64(json.dumps({**payload, "exp": int((now + ttl).timestamp())}).encode())
    mac = hmac.new(secret.encode(), f"{purpose}.{body}".encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def unseal(secret: str, purpose: str, token: str | None, now: datetime) -> dict | None:
    if not token or token.count(".") != 1:
        return None
    body, mac = token.split(".")
    expected = hmac.new(secret.encode(), f"{purpose}.{body}".encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_unb64(mac), expected):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("exp"), int):
        return None
    return payload if payload["exp"] > now.timestamp() else None


def session_actor(config: AuthConfig, cookie: str | None, now: datetime) -> Actor | None:
    payload = unseal(config.session_secret, SESSION_COOKIE, cookie, now)
    if payload is None:
        return None
    try:
        return Actor(str(payload["sub"]), str(payload["email"]), str(payload["name"]))
    except KeyError:
        return None


def start(config: AuthConfig, now: datetime) -> tuple[str, str]:
    """Returns the authorization URL and the login cookie that must come back with the code."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    query = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    login = {"state": state, "v": verifier}
    cookie = seal(config.session_secret, LOGIN_COOKIE, login, now, LOGIN_TTL)
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(query)}", cookie


def finish(
    config: AuthConfig,
    code: str | None,
    state: str | None,
    login_cookie: str | None,
    now: datetime,
    transport: Transport = urllib_transport,
) -> tuple[Actor, str]:
    """Exchanges the code for the user's verified identity; returns it and its session cookie."""
    login = unseal(config.session_secret, LOGIN_COOKIE, login_cookie, now)
    if login is None:
        raise SignInFailed("the sign-in expired or did not start here; sign in again")
    if (
        not code
        or not state
        or not hmac.compare_digest(str(login["state"]).encode(), state.encode())
    ):
        raise SignInFailed("the sign-in response does not match this browser; sign in again")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": str(login["v"]),
        "redirect_uri": config.redirect_uri,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }
    headers = {"content-type": "application/x-www-form-urlencoded", "accept": "application/json"}
    token = _json(transport, TOKEN_URL, form, headers, "the code exchange")
    access = token.get("access_token")
    if not isinstance(access, str) or not access:
        raise SignInFailed("Vercel returned no access token")
    auth = {"authorization": f"Bearer {access}", "accept": "application/json"}
    claims = _json(transport, USERINFO_URL, None, auth, "the identity lookup")
    sub, email = claims.get("sub"), claims.get("email")
    if not isinstance(sub, str) or not sub or not isinstance(email, str) or "@" not in email:
        raise SignInFailed("Vercel did not share an email address; allow the email scope")
    if claims.get("email_verified") is not True:
        raise SignInFailed("Vercel has not verified this account's email address")
    name = claims.get("name") if isinstance(claims.get("name"), str) else ""
    actor = Actor(sub, email.lower(), name)
    payload = {"sub": actor.sub, "email": actor.email, "name": actor.name}
    return actor, seal(config.session_secret, SESSION_COOKIE, payload, now, SESSION_TTL)


def _json(
    transport: Transport,
    url: str,
    form: Mapping[str, str] | None,
    headers: Mapping[str, str],
    step: str,
) -> dict:
    try:
        status, body = transport(url, form, headers, TIMEOUT_S)
    except (OSError, http.client.HTTPException):
        raise SignInFailed(f"Vercel did not answer {step}; try again") from None
    if status != 200:
        raise SignInFailed(f"Vercel refused {step} ({status}); sign in again")
    try:
        answer = json.loads(body)
    except ValueError:
        raise SignInFailed(f"Vercel's answer to {step} was unreadable") from None
    if not isinstance(answer, dict):
        raise SignInFailed(f"Vercel's answer to {step} was unreadable")
    return answer
