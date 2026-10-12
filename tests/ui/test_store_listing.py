from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest

from studio.state import r2
from studio.state.store import LocalObjectStore, StorageError
from tests.ui.fakes import listing

CREDENTIALS = r2.Credentials("0" * 32, "template-studio", "synthetic-key-id", "synthetic-secret")
NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)


class FakeListing:
    """Answers only listings, `page` names at a time; `script` injects failures first."""

    def __init__(self, keys, page=1000, script=()):
        self.keys = keys
        self.page = page
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers)))
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, BaseException):
                raise step
            return step, {}, b""
        assert urlsplit(url).path == "/template-studio"
        return listing(self.keys, url, self.page)


def r2_store(fake):
    return r2.R2ObjectStore(CREDENTIALS, fake, clock=lambda: NOW, sleep=lambda s: None)


KEYS = [
    "templates/" + "a" * 32 + "/head.json",
    "templates/" + "a" * 32 + "/revisions/x.json",
    "templates/" + "b" * 32 + "/head.json",
    "sources/" + "c" * 64 + ".json",
]


def test_local_children_lists_folders_and_objects_and_skips_temporaries(tmp_path):
    store = LocalObjectStore(tmp_path / "state")
    for key in KEYS:
        store.put_new(key, b"{}")
    (tmp_path / "state" / "templates" / ".head.json.1.2.tmp").write_bytes(b"partial")
    assert store.children("templates/") == ["a" * 32, "b" * 32]
    assert store.children("templates/" + "a" * 32 + "/") == ["head.json", "revisions"]
    assert store.children("nothing-here/") == []


def test_local_children_refuses_a_key_that_escapes_the_store(tmp_path):
    store = LocalObjectStore(tmp_path / "state")
    with pytest.raises(ValueError):
        store.children("../")


def test_r2_children_follows_every_page_and_signs_the_query():
    fake = FakeListing(KEYS + [f"templates/{n:032x}/head.json" for n in range(5)], page=2)
    names = r2_store(fake).children("templates/")
    assert names == sorted([f"{n:032x}" for n in range(5)] + ["a" * 32, "b" * 32])
    assert len(fake.requests) == 4  # 7 folders, 2 per page
    method, url, headers = fake.requests[1]
    assert method == "GET"
    assert "continuation-token=2" in url and "list-type=2" in url
    assert "prefix=templates%2F" in url and "delimiter=%2F" in url
    assert headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=synthetic-key-id/")


def test_r2_children_retries_an_unclear_answer_then_succeeds():
    fake = FakeListing(KEYS, script=[TimeoutError("synthetic"), 503])
    assert r2_store(fake).children("templates/") == ["a" * 32, "b" * 32]
    assert len(fake.requests) == 3


@pytest.mark.parametrize("status", [403, 404])
def test_r2_children_reports_a_refusal_as_a_storage_error(status):
    with pytest.raises(StorageError, match=f"R2 answered {status}"):
        r2_store(FakeListing(KEYS, script=[status])).children("templates/")


def test_r2_children_reports_a_broken_listing():
    def broken(method, url, headers, body, timeout):
        return 200, {}, b"<ListBucketResult"

    with pytest.raises(StorageError, match="broken listing"):
        r2_store(broken).children("templates/")


def test_r2_children_stops_after_a_bounded_number_of_pages(monkeypatch):
    monkeypatch.setattr(r2, "LIST_PAGES", 3)
    fake = FakeListing([f"templates/{n:032x}/head.json" for n in range(10)], page=1)
    with pytest.raises(StorageError, match="more than 3 pages"):
        r2_store(fake).children("templates/")
    assert len(fake.requests) == 3
