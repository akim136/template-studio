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
import stat
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

    def children(self, prefix: str) -> list[str]:
        """The sorted names directly under `prefix` (which ends with "/"): objects and folders."""
        ...


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

    The directory must be a real directory (not a link), owned by this user and closed to
    everyone else, or the store refuses to open. Each write goes to a temporary file that is
    flushed, synced and renamed into place, so a reader sees the old object or the new one, never
    part of one. Every folder the store creates, the directory and any missing parents included, is
    owner-only and synced into its parent. A lock file serializes conditional writes across threads
    and processes on this machine.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()
        try:
            _make_folders(self.root)
            info = self.root.lstat()
        except OSError as error:
            raise StorageError(f"cannot create the state directory: {error.strerror}") from None
        if not stat.S_ISDIR(info.st_mode):
            raise StorageError("the state directory must be a real directory, not a link")
        if info.st_uid != os.getuid():
            raise StorageError("the state directory must belong to this user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise StorageError("the state directory must be owner-only (mode 700)")

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

    def children(self, prefix: str) -> list[str]:
        folder = self.root / check_key(prefix.rstrip("/"))
        try:
            names = os.listdir(folder)
        except (FileNotFoundError, NotADirectoryError):
            return []
        except OSError as error:
            raise StorageError(f"cannot list {prefix}: {error.strerror}") from None
        return sorted(name for name in names if not name.startswith("."))  # skips temporaries

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
            _make_folders(path.parent, self.root)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            _sync_folder(path.parent)
        except OSError as error:
            raise StorageError(f"cannot write {key}: {error.strerror}") from None
        return hashlib.sha256(data).hexdigest()


def _make_folders(folder: Path, top: Path | None = None) -> None:
    """Makes `folder` and its missing parents below `top`, each owner-only and synced."""
    missing = []
    while folder != top and not os.path.lexists(folder):
        missing.append(folder)
        folder = folder.parent
    for created in reversed(missing):
        created.mkdir(mode=0o700, exist_ok=True)
        _sync_folder(created.parent)


def _sync_folder(folder: Path) -> None:
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
