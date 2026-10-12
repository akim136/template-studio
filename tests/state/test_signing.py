import ast
import json
from pathlib import Path

import pytest

import studio.state
from studio.state.errors import Conflict, Invalid
from studio.state.signing import (
    CREATED,
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
        self.remote: dict[str, Found] = {}

    def create(self, operation_id: str, payload: dict) -> str:
        self.calls.append(operation_id)
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


def test_the_simulated_adapter_creates_and_verifies_without_ids(services, approved):
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


NETWORK = {"socket", "ssl", "http", "urllib", "requests", "httpx", "aiohttp", "smtplib"}


def test_no_state_module_can_reach_a_provider_and_nothing_can_send():
    folder = Path(studio.state.__file__).parent
    for module in folder.glob("*.py"):
        tree = ast.parse(module.read_text())
        imported = {
            name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for name in (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
        }
        assert not imported & NETWORK, module.name
    assert not [name for name in dir(SimulatedSigningAdapter) if "send" in name]
