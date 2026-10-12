from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import app
from studio.state import api, identity, r2
from tests.state.helpers import Clock
from tests.state.test_hosted import AUTH, EMAIL, ORIGIN
from tests.state.test_r2 import CREDENTIALS
from tests.ui.fakes import FakeBucket


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def services(tmp_path, clock):
    return api.local_services(tmp_path / "state", clock)


@pytest.fixture
def local(services, monkeypatch):
    """A browser on this machine, in local mode."""
    monkeypatch.setattr(app.state, "services", services)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client


@pytest.fixture
def bucket():
    return FakeBucket()


@pytest.fixture
def hosted(bucket, clock):
    store = r2.R2ObjectStore(CREDENTIALS, bucket, clock=clock, sleep=lambda s: None)
    return api.hosted_services(store, AUTH, clock)


@pytest.fixture
def visitor(hosted, monkeypatch):
    """A browser on the hosted studio, not signed in."""
    monkeypatch.setattr(app.state, "services", hosted)
    with TestClient(app, base_url=ORIGIN, client=("203.0.113.9", 50000)) as client:
        yield client


@pytest.fixture
def signed_in(visitor, clock):
    session = {"sub": "user-1", "email": EMAIL, "name": "Rae Reviewer"}
    cookie = identity.seal(
        AUTH.session_secret, identity.SESSION_COOKIE, session, clock(), timedelta(hours=8)
    )
    visitor.cookies.set(identity.SESSION_COOKIE, cookie)
    return visitor
