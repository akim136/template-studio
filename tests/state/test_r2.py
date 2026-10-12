import hashlib
import http.client
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from studio.state import r2
from studio.state.store import PreconditionFailed, StorageError

# AWS's published Signature Version 4 examples for S3 (Amazon S3 API reference, "Signature
# calculations for the Authorization header" and "Query string request authentication").
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
AWS_DATE = "20130524T000000Z"
AWS_HOST = "examplebucket.s3.amazonaws.com"
CREDENTIALS = r2.Credentials("0" * 32, "template-studio", "synthetic-key-id", "synthetic-secret")
NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)


def test_the_signer_reproduces_aws_get_object_example():
    headers = {
        "host": AWS_HOST,
        "range": "bytes=0-9",
        "x-amz-content-sha256": r2.EMPTY_SHA256,
        "x-amz-date": AWS_DATE,
    }
    names, signature = r2.signature(
        AWS_SECRET, "GET", "/test.txt", {}, headers, r2.EMPTY_SHA256, AWS_DATE, "us-east-1"
    )
    assert names == "host;range;x-amz-content-sha256;x-amz-date"
    assert signature == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"


def test_the_signer_reproduces_aws_put_object_example():
    body = b"Welcome to Amazon S3."
    payload = hashlib.sha256(body).hexdigest()
    headers = {
        "date": "Fri, 24 May 2013 00:00:00 GMT",
        "host": AWS_HOST,
        "x-amz-content-sha256": payload,
        "x-amz-date": AWS_DATE,
        "x-amz-storage-class": "REDUCED_REDUNDANCY",
    }
    _, signature = r2.signature(
        AWS_SECRET, "PUT", "/test$file.text", {}, headers, payload, AWS_DATE, "us-east-1"
    )
    assert signature == "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"


def test_the_signer_reproduces_aws_presigned_url_example():
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": "AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request",
        "X-Amz-Date": AWS_DATE,
        "X-Amz-Expires": "86400",
        "X-Amz-SignedHeaders": "host",
    }
    _, signature = r2.signature(
        AWS_SECRET,
        "GET",
        "/test.txt",
        query,
        {"host": AWS_HOST},
        "UNSIGNED-PAYLOAD",
        AWS_DATE,
        "us-east-1",
    )
    assert signature == "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"


class FakeR2:
    """Answers like R2's conditional PUT and GET, from a dict. `script` lets a test inject
    failures: each entry is consumed by one request and is an exception or a status."""

    def __init__(self, script=()):
        self.objects: dict[str, bytes] = {}
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers)))
        key = urlsplit(url).path.split("/", 2)[2]
        if self.script:
            step = self.script.pop(0)
            if step == "land-then-drop":  # the write lands, then the connection drops
                self.objects[key] = body
                raise TimeoutError("synthetic: the answer never arrived")
            if isinstance(step, BaseException):
                raise step
            if isinstance(step, int):
                return step, {}, b""
        etag = lambda data: f'"{hashlib.md5(data).hexdigest()}"'  # noqa: E731, S324
        if method == "GET":
            if key not in self.objects:
                return 404, {}, b""
            return 200, {"etag": etag(self.objects[key])}, self.objects[key]
        current = self.objects.get(key)
        if headers.get("if-none-match") == "*" and current is not None:
            return 412, {}, b""
        if "if-match" in headers and (current is None or etag(current) != headers["if-match"]):
            return 412, {}, b""
        self.objects[key] = body
        return 200, {"etag": etag(body)}, b""


def store(fake):
    return r2.R2ObjectStore(CREDENTIALS, fake, clock=lambda: NOW, sleep=lambda s: None)


def test_writes_are_conditional_and_reads_return_the_etag():
    fake = FakeR2()
    objects = store(fake)
    etag = objects.put_new("templates/a/head.json", b"one")
    with pytest.raises(PreconditionFailed):
        objects.put_new("templates/a/head.json", b"two")
    assert objects.get("templates/a/head.json").etag == etag
    newer = objects.replace("templates/a/head.json", b"three", etag)
    with pytest.raises(PreconditionFailed):
        objects.replace("templates/a/head.json", b"four", etag)  # a stale etag loses
    assert objects.get("templates/a/head.json").data == b"three"
    assert objects.get("templates/a/head.json").etag == newer
    assert objects.get("templates/a/missing.json") is None


def test_every_request_is_signed_for_the_bucket_and_never_carries_the_secret():
    fake = FakeR2()
    store(fake).put_new("sources/x.json", b"{}")
    method, url, headers = fake.requests[0]
    assert url == f"https://{CREDENTIALS.host}/template-studio/sources/x.json"
    assert headers["authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=synthetic-key-id/20261012/auto/s3/aws4_request, "
        "SignedHeaders=host;if-none-match;x-amz-content-sha256;x-amz-date, Signature="
    )
    assert headers["x-amz-content-sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert "synthetic-secret" not in repr(fake.requests) + repr(CREDENTIALS)


@pytest.mark.parametrize("failure", [TimeoutError("synthetic"), 503, 429, 409])
def test_a_request_without_a_clear_answer_is_retried(failure):
    fake = FakeR2([failure])
    assert store(fake).put_new("sources/x.json", b"{}")
    assert len(fake.requests) == 2


def test_a_write_that_landed_before_its_answer_was_lost_is_reported_as_saved():
    fake = FakeR2(["land-then-drop"])
    objects = store(fake)
    etag = objects.put_new("templates/a/head.json", b"mine")  # the retry meets 412, reads back
    assert objects.get("templates/a/head.json").etag == etag
    assert [m for m, _, _ in fake.requests] == ["PUT", "PUT", "GET", "GET"]


def test_a_replace_that_landed_before_its_answer_was_lost_is_reported_as_saved():
    fake = FakeR2()
    objects = store(fake)
    etag = objects.put_new("templates/a/head.json", b"one")
    fake.script = ["land-then-drop"]
    newer = objects.replace("templates/a/head.json", b"two", etag)  # the retry's If-Match fails
    assert objects.get("templates/a/head.json").etag == newer
    assert [m for m, _, _ in fake.requests][1:] == ["PUT", "PUT", "GET", "GET"]


def test_a_broken_answer_counts_as_no_answer():
    fake = FakeR2([http.client.IncompleteRead(b"")])
    assert store(fake).put_new("sources/x.json", b"{}")
    assert len(fake.requests) == 2
    fake = FakeR2([http.client.IncompleteRead(b"")] * r2.ATTEMPTS)
    with pytest.raises(StorageError, match="no answer .IncompleteRead"):
        store(fake).put_new("sources/x.json", b"{}")


@pytest.mark.parametrize("answer", [429, 409])
def test_a_412_after_a_clear_retryable_answer_is_never_read_back(answer):
    fake = FakeR2([answer])
    fake.objects["templates/a/head.json"] = b"mine"  # identical bytes, written by someone else
    with pytest.raises(PreconditionFailed):
        store(fake).put_new("templates/a/head.json", b"mine")
    assert [m for m, _, _ in fake.requests] == ["PUT", "PUT"]


def test_a_412_after_a_lost_answer_stands_when_the_bytes_are_someone_elses():
    fake = FakeR2([TimeoutError("synthetic")])
    fake.objects["templates/a/head.json"] = b"theirs"
    with pytest.raises(PreconditionFailed):
        store(fake).put_new("templates/a/head.json", b"mine")


def test_a_clean_412_is_never_read_back():
    fake = FakeR2()
    fake.objects["templates/a/head.json"] = b"mine"  # identical bytes, written by someone else
    with pytest.raises(PreconditionFailed):
        store(fake).put_new("templates/a/head.json", b"mine")
    assert [m for m, _, _ in fake.requests] == ["PUT"]


def test_storage_fails_after_the_last_attempt_and_names_the_key():
    fake = FakeR2([TimeoutError("synthetic")] * r2.ATTEMPTS)
    with pytest.raises(StorageError, match="cannot write sources/x.json: no answer"):
        store(fake).put_new("sources/x.json", b"{}")
    assert len(fake.requests) == r2.ATTEMPTS


def test_an_unexpected_answer_is_a_storage_error_without_retry():
    fake = FakeR2([403])
    with pytest.raises(StorageError, match="R2 answered 403"):
        store(fake).get("sources/x.json")
    assert len(fake.requests) == 1


def test_keys_are_checked_before_anything_is_sent():
    fake = FakeR2()
    with pytest.raises(ValueError, match="invalid storage key"):
        store(fake).get("../other-bucket/x")
    assert fake.requests == []


def test_a_presigned_upload_is_bound_to_one_key_one_length_and_ten_minutes():
    url = store(FakeR2()).presign_put("uploads/" + "a" * 64 + ".pdf", 1234)
    parts = urlsplit(url)
    assert parts.netloc == CREDENTIALS.host
    assert parts.path == "/template-studio/uploads/" + "a" * 64 + ".pdf"
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert query["X-Amz-SignedHeaders"] == "content-length;host"
    assert query["X-Amz-Expires"] == "600"
    assert query["X-Amz-Credential"] == "synthetic-key-id/20261012/auto/s3/aws4_request"
    assert len(query["X-Amz-Signature"]) == 64 and "synthetic-secret" not in url
    other = store(FakeR2()).presign_put("uploads/" + "a" * 64 + ".pdf", 1235)
    assert parse_qs(urlsplit(other).query)["X-Amz-Signature"][0] != query["X-Amz-Signature"]
