import os
import stat
import threading
import time

import pytest

from studio.state import store as store_module
from studio.state.store import LocalObjectStore, PreconditionFailed, StorageError, Stored


def test_put_new_refuses_a_key_that_exists(tmp_path):
    store = LocalObjectStore(tmp_path)
    etag = store.put_new("a/b.json", b"1")
    with pytest.raises(PreconditionFailed):
        store.put_new("a/b.json", b"2")
    assert store.get("a/b.json") == Stored(b"1", etag)


def test_replace_needs_the_etag_that_was_read(tmp_path):
    store = LocalObjectStore(tmp_path)
    first = store.put_new("k", b"1")
    second = store.replace("k", b"2", first)
    with pytest.raises(PreconditionFailed):
        store.replace("k", b"3", first)
    with pytest.raises(PreconditionFailed):
        store.replace("missing", b"x", first)
    assert store.get("k") == Stored(b"2", second)


@pytest.mark.parametrize("key", ["../out", "/abs", "a/../b", "A", "a//b", ".lock", "a/.tmp", ""])
def test_a_key_cannot_leave_the_store_or_touch_its_lock(tmp_path, key):
    with pytest.raises(ValueError):
        LocalObjectStore(tmp_path / "s").put_new(key, b"x")
    assert not (tmp_path / "out").exists()


def test_objects_and_folders_are_owner_only(tmp_path):
    store = LocalObjectStore(tmp_path / "s")
    store.put_new("a/b", b"x")
    for path in (tmp_path / "s", tmp_path / "s" / "a", tmp_path / "s" / "a" / "b"):
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, path


def test_a_failed_write_raises_and_leaves_nothing_behind(tmp_path, monkeypatch):
    store = LocalObjectStore(tmp_path)

    def failing(descriptor):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(store_module.os, "fsync", failing)
    with pytest.raises(StorageError, match="Input/output error"):
        store.put_new("k", b"x")
    monkeypatch.undo()
    assert store.get("k") is None
    assert sorted(os.listdir(tmp_path)) == [".lock"]


def test_concurrent_replaces_of_one_version_have_exactly_one_winner(tmp_path):
    store = LocalObjectStore(tmp_path)
    etag = store.put_new("k", b"start")
    start, winners = threading.Barrier(8), []

    def attempt(index: int) -> None:
        start.wait()
        try:
            store.replace("k", str(index).encode(), etag)
            winners.append(index)
        except PreconditionFailed:
            pass

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(winners) == 1
    assert store.get("k").data == str(winners[0]).encode()


def test_a_state_directory_that_is_a_link_or_shared_is_refused(tmp_path):
    real = tmp_path / "real"
    LocalObjectStore(real)
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(StorageError, match="not a link"):
        LocalObjectStore(tmp_path / "link")
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o750)
    with pytest.raises(StorageError, match="owner-only"):
        LocalObjectStore(shared)
    (tmp_path / "file").write_bytes(b"")
    with pytest.raises(StorageError):
        LocalObjectStore(tmp_path / "file")


def test_new_folders_are_synced_into_their_parents(tmp_path, monkeypatch):
    store = LocalObjectStore(tmp_path / "s")
    synced = []
    real = store_module._sync_folder

    def record(folder):
        synced.append(folder)
        real(folder)

    monkeypatch.setattr(store_module, "_sync_folder", record)
    store.put_new("a/b/c", b"x")
    root = tmp_path / "s"
    assert synced == [root, root / "a", root / "a" / "b"]  # each new entry, then the object


def test_two_stores_on_one_folder_never_both_win(tmp_path, monkeypatch):
    real = LocalObjectStore._write

    def slow(self, key, data):  # widen the gap between the check and the write
        time.sleep(0.02)
        return real(self, key, data)

    monkeypatch.setattr(LocalObjectStore, "_write", slow)
    stores = [LocalObjectStore(tmp_path), LocalObjectStore(tmp_path)]  # as two processes would
    start, winners = threading.Barrier(6), []

    def attempt(index: int) -> None:
        start.wait()
        try:
            stores[index % 2].put_new("k", str(index).encode())
            winners.append(index)
        except PreconditionFailed:
            pass

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(winners) == 1
