import json
import subprocess
import sys
from pathlib import Path

import pytest

from studio.state.errors import Conflict, Invalid
from studio.state.signing import (
    CREATED,
    CREATING,
    FAILED,
    NEEDS_STATUS_CHECK,
    Found,
    Operations,
    OutcomeUnknown,
    Refused,
    SimulatedSigningAdapter,
)
from studio.state.store import StorageError, digest
from tests.state.helpers import POLICY, field


class Crash(BaseException):
    """The process dies mid-call: nothing after the adapter call runs."""


class Scripted:
    """A provider whose answers the test chooses. `remote` is what the provider holds."""

    def __init__(self, create: str = "ok", find: str = "ok") -> None:
        self.create_outcome, self.find_outcome = create, find
        self.calls: list[str] = []
        self.payloads: list[dict] = []
        self.remote: dict[str, Found] = {}
        self.during_create = None  # runs inside the create call, as a concurrent request would

    def create(self, operation_id: str, payload: dict) -> str:
        self.calls.append(operation_id)
        self.payloads.append(payload)
        if self.during_create is not None:
            during, self.during_create = self.during_create, None
            during()
        if self.create_outcome == "refuse":
            raise Refused("the provider rejected the request")
        if self.create_outcome == "timeout-before":
            raise TimeoutError
        if self.create_outcome != "lost":
            self.remote[operation_id] = Found(f"remote-{operation_id}", digest(payload))
        if self.create_outcome == "timeout-after":
            raise TimeoutError
        if self.create_outcome == "crash":
            raise Crash
        return f"remote-{operation_id}"

    def find(self, operation_id: str) -> Found | None:
        if self.find_outcome == "error":
            raise OutcomeUnknown("the provider is unreachable")
        return self.remote.get(operation_id)


@pytest.fixture
def approved(services, source):
    template = services.templates.create(source["sha256"], POLICY)
    saved, _ = services.templates.save(template["id"], 1, POLICY, [field()])
    services.templates.confirm_review(saved["id"], 2, saved["binding"])
    return services.templates.approve(saved["id"], 2, saved["binding"])


def scripted(services, clock, **outcomes):
    adapter = Scripted(**outcomes)
    return Operations(services.store, services.templates, adapter, clock), adapter


def create(operations, approved):
    return operations.create(approved["id"], approved["revision"], approved["binding"])


def test_create_needs_the_current_approved_revision(services, source):
    template = services.templates.create(source["sha256"], POLICY)
    saved, _ = services.templates.save(template["id"], 1, POLICY, [field()])
    with pytest.raises(Invalid, match="not approved"):
        services.operations.create(saved["id"], 2, saved["binding"])
    with pytest.raises(Conflict, match="stale"):
        services.operations.create(saved["id"], 1, template["binding"])
    assert services.store.get(f"templates/{saved['id']}/operation.json") is None


def test_the_payload_carries_no_field_ids(services, clock, approved):
    operations, adapter = scripted(services, clock)
    create(operations, approved)
    (payload,) = adapter.payloads
    assert payload["fields"] and all("id" not in item for item in payload["fields"])
    assert payload["fields"][0] == {k: v for k, v in field().items() if k != "id"}


def test_the_simulated_adapter_creates_and_verifies(services, approved):
    operation = create(services.operations, approved)
    assert operation["state"] == CREATED
    assert operation["external_id"] == f"simulated-{operation['id']}"
    assert services.operations.get(approved["id"]) == operation
    record = services.store.get(f"simulated-signing/{operation['id']}.json")
    assert json.loads(record.data)["payload_sha256"] == operation["payload_sha256"]


def test_a_created_template_is_not_created_again(services, approved):
    first = create(services.operations, approved)
    with pytest.raises(Conflict, match="already created") as refused:
        create(services.operations, approved)
    assert refused.value.current == first


@pytest.mark.parametrize("outcome", ["timeout-after", "crash"])
def test_an_unknown_outcome_keeps_its_id_and_offers_only_a_status_check(
    services, clock, approved, outcome
):
    operations, adapter = scripted(services, clock, create=outcome)
    if outcome == "crash":
        with pytest.raises(Crash):
            create(operations, approved)
        unknown = operations.get(approved["id"])
        assert unknown["state"] == "CREATING"
    else:
        unknown = create(operations, approved)
        assert unknown["state"] == NEEDS_STATUS_CHECK and "TimeoutError" in unknown["note"]
    with pytest.raises(Conflict, match="check its status"):
        create(operations, approved)
    assert len(adapter.calls) == 1
    checked = operations.check_status(approved["id"])
    assert checked["state"] == CREATED and checked["id"] == unknown["id"]


def test_a_refused_create_fails_and_a_new_create_is_allowed(services, clock, approved):
    operations, adapter = scripted(services, clock, create="refuse")
    failed = create(operations, approved)
    assert failed["state"] == FAILED and "rejected" in failed["note"]
    adapter.create_outcome = "ok"
    created = create(operations, approved)
    assert created["state"] == CREATED and created["id"] != failed["id"]


def test_nothing_found_means_failed_only_after_the_grace_period(services, clock, approved):
    operations, adapter = scripted(services, clock, create="timeout-before")
    unknown = create(operations, approved)
    clock.advance(seconds=30)
    early = operations.check_status(approved["id"])
    assert early["state"] == NEEDS_STATUS_CHECK and "may still be running" in early["note"]
    assert operations.get(approved["id"]) == unknown  # nothing written while a create may run
    clock.advance(minutes=3)
    failed = operations.check_status(approved["id"])
    assert failed["state"] == FAILED and "new create is allowed" in failed["note"]
    adapter.create_outcome = "ok"
    assert create(operations, approved)["state"] == CREATED


def test_a_reported_create_that_cannot_be_found_never_becomes_failed(services, clock, approved):
    operations, _ = scripted(services, clock, create="lost")
    assert create(operations, approved)["state"] == NEEDS_STATUS_CHECK
    clock.advance(hours=1)
    assert operations.check_status(approved["id"])["state"] == NEEDS_STATUS_CHECK


def test_a_status_check_that_errors_keeps_the_operation_open(services, clock, approved):
    operations, adapter = scripted(services, clock, create="timeout-after", find="error")
    create(operations, approved)
    clock.advance(hours=1)
    checked = operations.check_status(approved["id"])
    assert checked["state"] == NEEDS_STATUS_CHECK and "status unknown" in checked["note"]


def test_different_content_at_the_provider_is_not_marked_created(services, clock, approved):
    operations, adapter = scripted(services, clock, create="timeout-after")
    unknown = create(operations, approved)
    adapter.remote[unknown["id"]] = Found("remote-x", "0" * 64)
    checked = operations.check_status(approved["id"])
    assert checked["state"] == NEEDS_STATUS_CHECK and "different content" in checked["note"]


def test_an_altered_source_blocks_create(services, approved, tmp_path):
    path = tmp_path / "state" / "sources" / f"{approved['source_sha256']}.pdf"
    path.write_bytes(b"changed")
    with pytest.raises(StorageError, match="does not match"):
        create(services.operations, approved)
    assert services.store.get(f"templates/{approved['id']}/operation.json") is None


NETWORK = [
    "socket",
    "ssl",
    "http.client",
    "urllib.request",
    "requests",
    "httpx",
    "aiohttp",
    "smtplib",
    "studio.services.gateway",
]
CORE = ["errors", "policy", "store", "sources", "templates", "signing"]  # all but the HTTP layer


def test_no_state_module_can_reach_a_provider_even_indirectly_and_nothing_can_send():
    imports = "; ".join(f"import studio.state.{name}" for name in CORE)
    check = f"import sys; {imports}; print([m for m in {NETWORK!r} if m in sys.modules])"
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", check], cwd=root, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
    assert not [name for name in dir(SimulatedSigningAdapter) if "send" in name]


def test_a_create_reported_after_a_status_check_moved_it_is_still_recorded(
    services, clock, approved
):
    operations, adapter = scripted(services, clock)

    def check_that_cannot_reach_the_provider():
        adapter.find_outcome = "error"
        assert operations.check_status(approved["id"])["state"] == NEEDS_STATUS_CHECK
        adapter.find_outcome = "ok"

    adapter.during_create = check_that_cannot_reach_the_provider
    created = create(operations, approved)
    assert created["state"] == CREATED and created["reported_created"]
    assert operations.get(approved["id"]) == created


def test_a_create_reported_after_it_was_declared_failed_is_not_left_failed(
    services, clock, approved
):
    operations, adapter = scripted(services, clock, create="lost")  # reported, never found

    def late_check():
        clock.advance(minutes=5)  # past the grace period: nothing found yet, so FAILED
        assert operations.check_status(approved["id"])["state"] == FAILED

    adapter.during_create = late_check
    reported = create(operations, approved)
    assert reported["state"] == NEEDS_STATUS_CHECK and reported["reported_created"]
    assert operations.get(approved["id"]) == reported
    with pytest.raises(Conflict, match="check its status"):  # no second, duplicate create
        create(operations, approved)


def test_a_create_reported_after_its_operation_was_replaced_says_so(services, clock, approved):
    operations, adapter = scripted(services, clock)

    def replaced():
        clock.advance(minutes=5)
        operations.check_status(approved["id"])  # FAILED: the first create was not found yet
        adapter.remote.clear()
        create(operations, approved)  # a second operation takes the slot

    adapter.during_create = replaced
    first = create(operations, approved)
    assert first["state"] == NEEDS_STATUS_CHECK and "replaced" in first["note"]
    assert operations.get(approved["id"])["id"] != first["id"]


class LandsThenFails:
    """A store whose next write lands on disk and then reports a failure (a failed fsync)."""

    def __init__(self, store) -> None:
        self.store, self.armed = store, False

    def get(self, key):
        return self.store.get(key)

    def put_new(self, key, data):
        etag = self.store.put_new(key, data)
        if self.armed and key.endswith("operation.json"):
            self.armed = False
            raise StorageError(f"cannot write {key}: Input/output error")
        return etag

    def replace(self, key, data, etag):
        return self.store.replace(key, data, etag)


def test_an_operation_record_that_landed_but_reported_failure_does_not_block_a_retry(
    services, clock, approved
):
    store = LandsThenFails(services.store)
    adapter = Scripted()
    operations = Operations(store, services.templates, adapter, clock)
    store.armed = True
    with pytest.raises(StorageError):
        create(operations, approved)
    assert adapter.calls == []  # the provider was never asked
    left = operations.get(approved["id"])
    assert left["state"] == FAILED and "not attempted" in left["note"]
    assert create(operations, approved)["state"] == CREATED


def test_a_failure_to_record_the_outcome_points_to_a_status_check(
    services, clock, approved, monkeypatch
):
    operations, adapter = scripted(services, clock)
    real = services.store.replace

    def failing(key, data, etag):
        if key.endswith("operation.json") and adapter.calls:
            raise StorageError(f"cannot write {key}: No space left on device")
        return real(key, data, etag)

    with monkeypatch.context() as patch:
        patch.setattr(services.store, "replace", failing)
        with pytest.raises(StorageError, match="the create was sent.*check its status"):
            create(operations, approved)
    assert operations.get(approved["id"])["state"] == CREATING
    assert operations.check_status(approved["id"])["state"] == CREATED
