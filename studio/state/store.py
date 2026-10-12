"""Object storage where every write is conditional.

`put_new` succeeds only if the key is absent, and `replace` only if the key still has the etag the
caller read, so two writers can never both believe they saved. This is the shape R2 offers through
conditional PUTs, so an R2 backend can replace the local one without changing callers.

A write that raises `StorageError` is reported as not saved, but it may still have landed (a
failure after the rename, for example). Callers never claim a save on an error, and the next
conditional write sees the real state.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Lowercase segments that start with a letter or digit: no "." or ".." segment, no absolute path.
KEY = re.compile(r"[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*")


class StorageError(Exception):
    """The store could not be read or written. Nothing may be reported as saved."""


class PreconditionFailed(Exception):
    """A conditional write lost: the key already exists, or it changed since it was read."""


@dataclass(frozen=True)
class Stored:
    data: bytes
    etag: str


class ObjectStore(Protocol):
    def get(self, key: str) -> Stored | None: ...

    def put_new(self, key: str, data: bytes) -> str: ...

    def replace(self, key: str, data: bytes, etag: str) -> str: ...


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def check_key(key: str) -> str:
    if not KEY.fullmatch(key):
        raise ValueError(f"invalid storage key {key!r}")
    return key


class LocalObjectStore:
    """A directory on local disk, owner-only.

    Each write goes to a temporary file that is flushed, synced and renamed into place, so a reader
    sees the old object or the new one, never part of one. A lock file serializes conditional
    writes across threads and processes on this machine.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as error:
            raise StorageError(f"cannot create the state directory: {error.strerror}") from None

    def get(self, key: str) -> Stored | None:
        try:
            data = (self.root / check_key(key)).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StorageError(f"cannot read {key}: {error.strerror}") from None
        return Stored(data, hashlib.sha256(data).hexdigest())

    def put_new(self, key: str, data: bytes) -> str:
        with self._locked():
            if self.get(key) is not None:
                raise PreconditionFailed(key)
            return self._write(key, data)

    def replace(self, key: str, data: bytes, etag: str) -> str:
        with self._locked():
            current = self.get(key)
            if current is None or current.etag != etag:
                raise PreconditionFailed(key)
            return self._write(key, data)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._lock:
            try:
                handle = open(self.root / ".lock", "ab")  # keys never start with "."
            except OSError as error:
                raise StorageError(f"cannot lock the state directory: {error.strerror}") from None
            with handle:
                fcntl.flock(handle, fcntl.LOCK_EX)  # released when the handle closes
                yield

    def _write(self, key: str, data: bytes) -> str:
        path = self.root / check_key(key)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as error:
            raise StorageError(f"cannot write {key}: {error.strerror}") from None
        return hashlib.sha256(data).hexdigest()
