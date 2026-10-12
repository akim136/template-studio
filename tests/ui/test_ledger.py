from decimal import Decimal

import pytest

from studio.services.spend import BudgetExceeded
from studio.state import ledger
from studio.state.ledger import OPENING_USD, StoreLedger
from studio.state.store import PreconditionFailed, StorageError


def rows(store):
    return store.children("spend/rows/")


def test_a_new_ledger_opens_with_the_spend_before_it(services, clock):
    book = StoreLedger(services.store, clock, "draft-1")
    assert book.totals() == (float(OPENING_USD), 0.0)
    assert book.remaining() == Decimal(10) - OPENING_USD


def test_a_reservation_counts_at_its_worst_case_until_settled(services, clock):
    book = StoreLedger(services.store, clock, "draft-1")
    held = book.reserve(0.05, "page 1 stage A")
    assert book.totals() == (float(OPENING_USD + Decimal("0.05")), 0.05)
    book.settle(held, Decimal("0.0123"), {"model": "openai/gpt-5.4-mini"})
    assert book.totals() == (float(OPENING_USD + Decimal("0.0123")), 0.0123)
    assert len(rows(services.store)) == 2


def test_runs_are_counted_apart_and_share_one_total(services, clock):
    first = StoreLedger(services.store, clock, "draft-1")
    second = StoreLedger(services.store, clock, "draft-2")
    first.settle(first.reserve(0.02, "a"), Decimal("0.01"), {})
    second.reserve(0.03, "b")  # never settled
    assert second.totals() == (float(OPENING_USD + Decimal("0.04")), 0.03)
    assert first.totals()[1] == 0.01


def test_a_call_that_would_pass_the_cap_is_refused_before_anything_is_written(services, clock):
    book = StoreLedger(services.store, clock, "draft-1", cap_usd=Decimal("2.30"))
    with pytest.raises(BudgetExceeded, match=r"past the \$2\.30 cap"):
        book.reserve(Decimal("0.05"), "page 1 stage A")
    assert services.store.get(ledger.HEAD) is None and rows(services.store) == []


def test_an_overrun_is_recorded_and_then_stops(services, clock):
    book = StoreLedger(services.store, clock, "draft-1")
    held = book.reserve(0.01, "page 1 stage A")
    with pytest.raises(BudgetExceeded, match="above its reserved worst case"):
        book.settle(held, Decimal("0.02"), {})
    assert book.totals()[1] == 0.02  # the real cost is kept


def test_settling_twice_is_refused(services, clock):
    book = StoreLedger(services.store, clock, "draft-1")
    held = book.reserve(0.01, "x")
    book.settle(held, 0, {})
    with pytest.raises(StorageError, match="not open"):
        book.settle(held, 0, {})


def test_a_lost_race_is_read_again_and_both_calls_count(services, clock, monkeypatch):
    book = StoreLedger(services.store, clock, "draft-1")
    book.reserve(0.01, "first")
    rival = StoreLedger(services.store, clock, "draft-2")
    replace = services.store.replace
    raced = []

    def racing(key, data, etag):
        if not raced:  # another instance writes between our read and our write
            raced.append(True)
            rival.reserve(0.02, "rival")
        return replace(key, data, etag)

    monkeypatch.setattr(services.store, "replace", racing)
    book.reserve(0.03, "second")
    assert book.totals()[0] == float(OPENING_USD + Decimal("0.06"))


def test_a_ledger_that_stays_busy_sends_nothing(services, clock, monkeypatch):
    book = StoreLedger(services.store, clock, "draft-1")
    book.reserve(0.01, "first")

    def always_lost(key, data, etag):
        raise PreconditionFailed(key)

    monkeypatch.setattr(services.store, "replace", always_lost)
    with pytest.raises(StorageError, match="busy"):
        book.reserve(0.01, "second")


def test_the_ledger_works_on_r2(hosted, clock):
    book = StoreLedger(hosted.store, clock, "draft-1")
    book.settle(book.reserve(0.05, "page 1 stage A"), Decimal("0.01"), {})
    assert book.totals() == (float(OPENING_USD + Decimal("0.01")), 0.01)
