"""Cloudflare R2 through its S3 API: the hosted ObjectStore, and presigned direct uploads.

Every write is conditional, as ObjectStore requires: `put_new` sends `If-None-Match: *`, `replace`
sends `If-Match` with the etag that was read, and R2 answers 412 when the condition fails. Requests
are signed with AWS Signature Version 4, using only the standard library.

A request that gets no answer (a network error, a timeout or a broken response), or a 409, 429,
500, 502, 503 or 504 answer, is tried up to ATTEMPTS times in all, with backoff. A write whose
outcome is unknown may have landed, so a retry that is refused with 412 reads the key back: if it
holds exactly the bytes this write sent, the write landed and its etag is returned. Otherwise the
412 stands. When every attempt fails the error is a StorageError, which callers report as "may or
may not have been saved".

The credentials are an R2 API token limited to Object Read & Write on one bucket. They stay on the
server: a browser gets only a presigned URL for one key and one length, valid for UPLOAD_EXPIRY_S.
R2 does not check the bytes against the hash in the key, so anyone holding a live URL can write
other bytes there; seal re-hashes them and refuses a mismatch.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

from studio.state.store import PreconditionFailed, StorageError, Stored, check_key

REGION = "auto"
SERVICE = "s3"
ATTEMPTS = 4
BACKOFF_S = (0.2, 0.5, 1.0)  # before the second, third and fourth attempts
TIMEOUT_S = 30
UPLOAD_EXPIRY_S = 600
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
RETRY_STATUSES = frozenset({409, 429, 500, 502, 503, 504})

# (method, url, headers, body, timeout) -> (status, lowercase headers, body). Raises OSError
# (urllib's URLError and TimeoutError included) when no answer arrives, or an
# http.client.HTTPException when the answer is broken.
Transport = Callable[[str, str, Mapping[str, str], bytes, float], tuple[int, dict, bytes]]


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes, timeout: float
) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=body or None, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            answer = {k.lower(): v for k, v in response.headers.items()}
            return response.status, answer, response.read()
    except urllib.error.HTTPError as error:
        answer = {k.lower(): v for k, v in error.headers.items()}
        return error.code, answer, error.read()


@dataclass(frozen=True)
class Credentials:
    account_id: str
    bucket: str
    access_key_id: str
    secret_access_key: str = field(repr=False)

    @property
    def host(self) -> str:
        return f"{self.account_id}.r2.cloudflarestorage.com"


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, day: str, region: str, service: str) -> bytes:
    key = _hmac(f"AWS4{secret}".encode(), day)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    return key


def _encode(value: str, slash: bool) -> str:
    return quote(value, safe="-_.~/" if slash else "-_.~")


def _canonical_query(query: Mapping[str, str]) -> str:
    pairs = sorted((_encode(k, False), _encode(v, False)) for k, v in query.items())
    return "&".join(f"{k}={v}" for k, v in pairs)


def signature(
    secret: str,
    method: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    payload_sha256: str,
    amz_date: str,
    region: str = REGION,
    service: str = SERVICE,
) -> tuple[str, str]:
    """Returns (signed header names, hex signature) for SigV4. `headers` are the ones signed."""
    lowered = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    names = ";".join(sorted(lowered))
    canonical_headers = "".join(f"{k}:{lowered[k]}\n" for k in sorted(lowered))
    request = "\n".join(
        [
            method,
            _encode(path, True),
            _canonical_query(query),
            canonical_headers,
            names,
            payload_sha256,
        ]
    )
    day = amz_date[:8]
    scope = f"{day}/{region}/{service}/aws4_request"
    hashed = hashlib.sha256(request.encode()).hexdigest()
    to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashed}"
    key = _signing_key(secret, day, region, service)
    return names, hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()


def _amz_date(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


class R2ObjectStore:
    def __init__(
        self,
        credentials: Credentials,
        transport: Transport = urllib_transport,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.credentials = credentials
        self.transport = transport
        self.clock = clock
        self.sleep = sleep

    def get(self, key: str) -> Stored | None:
        status, headers, body, _ = self._send("GET", key, b"", {})
        if status == 404:
            return None
        if status != 200:
            raise StorageError(f"cannot read {key}: R2 answered {status}")
        return Stored(body, _etag(headers, key))

    def put_new(self, key: str, data: bytes) -> str:
        return self._write(key, data, {"if-none-match": "*"})

    def replace(self, key: str, data: bytes, etag: str) -> str:
        return self._write(key, data, {"if-match": etag})

    def presign_put(self, key: str, length: int, expires: int = UPLOAD_EXPIRY_S) -> str:
        """A URL that lets its holder PUT exactly `length` bytes to `key` until it expires."""
        check_key(key)
        amz_date = _amz_date(self.clock())
        credentials = self.credentials
        scope = f"{amz_date[:8]}/{REGION}/{SERVICE}/aws4_request"
        query = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{credentials.access_key_id}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires),
            "X-Amz-SignedHeaders": "content-length;host",
        }
        path = f"/{credentials.bucket}/{key}"
        signed = {"content-length": str(length), "host": credentials.host}
        _, signature_hex = signature(
            credentials.secret_access_key, "PUT", path, query, signed, "UNSIGNED-PAYLOAD", amz_date
        )
        query["X-Amz-Signature"] = signature_hex
        return f"https://{credentials.host}{_encode(path, True)}?{_canonical_query(query)}"

    def _write(self, key: str, data: bytes, condition: dict[str, str]) -> str:
        status, headers, _, unknown = self._send("PUT", key, data, condition)
        if status == 200:
            return _etag(headers, key)
        if status == 412:
            if unknown:
                landed = self.get(key)
                if landed is not None and landed.data == data:
                    return landed.etag  # an earlier attempt of this write landed
            raise PreconditionFailed(key)
        raise StorageError(f"cannot write {key}: R2 answered {status}")

    def _send(
        self, method: str, key: str, body: bytes, extra: Mapping[str, str]
    ) -> tuple[int, dict, bytes, bool]:
        """Sends one signed request, retrying when no clear answer comes. The flag says whether an
        earlier attempt of this write may have landed."""
        check_key(key)
        credentials = self.credentials
        path = f"/{credentials.bucket}/{key}"
        url = f"https://{credentials.host}{_encode(path, True)}"
        unknown = False
        failure = "no attempt was made"
        for attempt in range(ATTEMPTS):
            if attempt:
                self.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S)) - 1])
            amz_date = _amz_date(self.clock())
            payload_sha256 = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
            headers = {
                "host": credentials.host,
                "x-amz-content-sha256": payload_sha256,
                "x-amz-date": amz_date,
                **extra,
            }
            names, signature_hex = signature(
                credentials.secret_access_key, method, path, {}, headers, payload_sha256, amz_date
            )
            scope = f"{amz_date[:8]}/{REGION}/{SERVICE}/aws4_request"
            headers["authorization"] = (
                f"AWS4-HMAC-SHA256 Credential={credentials.access_key_id}/{scope}, "
                f"SignedHeaders={names}, Signature={signature_hex}"
            )
            try:
                status, answer, data = self.transport(method, url, headers, body, TIMEOUT_S)
            except (OSError, http.client.HTTPException) as error:  # no answer, or a broken one
                failure = f"no answer ({type(error).__name__})"
                unknown = unknown or method == "PUT"
                continue
            if status in RETRY_STATUSES:
                failure = f"R2 answered {status}"
                unknown = unknown or (method == "PUT" and status >= 500)
                continue
            return status, answer, data, unknown
        raise StorageError(f"cannot {'write' if method == 'PUT' else 'read'} {key}: {failure}")


def _etag(headers: Mapping[str, str], key: str) -> str:
    etag = headers.get("etag")
    if not etag:
        raise StorageError(f"R2 returned no etag for {key}")
    return etag
