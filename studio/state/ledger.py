"""The studio's model spend ledger, kept in its own storage with conditional writes.

The rules are the CLI ledger's (`studio.services.spend`): every model call reserves its worst-case
cost before it is sent and settles the observed cost afterwards; a reservation that is never
settled (a crash, a timeout, an unknown outcome) keeps counting at its worst case; a call that
cost more than its reservation is recorded at its real cost and then stops the draft. The totals
live in one head object that is replaced only with the etag that was read, so concurrent calls on
any instance share one total and a lost race is read again, never overwritten. Each reserve and
settle is also written as an immutable row, for audit.

The cap counts market cost, including calls on Alex's own key, which the gateway bills at $0.
The opening balance is what was spent before this ledger existed, on the CLI ledger kept outside
the repository: $2.276604 on October 12, 2026.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from decimal import Decimal

from studio.services.spend import BudgetExceeded, Reservation
from studio.state.store import ObjectStore, PreconditionFailed, StorageError, canonical

HEAD = "spend/head.json"
ROWS = "spend/rows"
OPENING_USD = Decimal("2.276604")
CAP_USD = Decimal("10")  # Alex's authorization, October 11, 2026
ATTEMPTS = 8  # reads of the head before a busy ledger gives up


def _usd(value) -> Decimal:
    """Exact decimal dollars. A float goes through its shortest repr, so 0.1 stays 0.1."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _fresh() -> dict:
    return {"opening_usd": str(OPENING_USD), "settled_usd": "0", "open": {}, "runs": {}}


def totals(head: dict, run: str | None = None) -> tuple[Decimal, Decimal]:
    """(all spend, this run's spend). Unsettled reservations count at their worst case."""
    reserved = [(_usd(row["usd"]), row["run"]) for row in head["open"].values()]
    total = _usd(head["opening_usd"]) + _usd(head["settled_usd"]) + sum(u for u, _ in reserved)
    if run is None:
        return total, Decimal(0)
    settled = _usd(head["runs"].get(run, "0"))
    return total, settled + sum(u for u, r in reserved if r == run)


class StoreLedger:
    """A ledger for one run (one draft). Use `read` for the totals without a run."""

    def __init__(self, store: ObjectStore, clock: Callable, run: str, cap_usd=CAP_USD) -> None:
        self.store, self.clock, self.run, self.cap_usd = store, clock, run, _usd(cap_usd)

    def read(self) -> dict:
        stored = self.store.get(HEAD)
        return _fresh() if stored is None else json.loads(stored.data)

    def totals(self) -> tuple[float, float]:
        total, run_total = totals(self.read(), self.run)
        return float(total), float(run_total)

    def remaining(self) -> Decimal:
        return max(self.cap_usd - totals(self.read())[0], Decimal(0))

    def _update(self, change: Callable[[dict], None]) -> dict:
        """Apply `change` to the head and write it with the etag that was read. `change` may
        raise (BudgetExceeded) to leave the ledger as it was."""
        for _ in range(ATTEMPTS):
            stored = self.store.get(HEAD)
            head = _fresh() if stored is None else json.loads(stored.data)
            change(head)
            data = canonical(head)
            try:
                if stored is None:
                    self.store.put_new(HEAD, data)
                else:
                    self.store.replace(HEAD, data, stored.etag)
            except PreconditionFailed:
                continue  # another call wrote first: read again
            return head
        raise StorageError("the spend ledger is busy; nothing was sent")

    def _row(self, kind: str, reservation: Reservation, usd, detail: dict) -> None:
        at = self.clock()
        row = {
            "kind": kind,
            "id": reservation.id,
            "run": self.run,
            "label": reservation.label,
            "usd": str(usd),
            "at": at.isoformat(),
            **detail,
        }
        stamp = at.strftime("%Y%m%dt%H%M%S%fz")
        self.store.put_new(f"{ROWS}/{stamp}-{reservation.id}-{kind}.json", canonical(row))

    def reserve(self, usd, label: str) -> Reservation:
        reservation = Reservation(uuid.uuid4().hex, _usd(usd), label)

        def change(head: dict) -> None:
            total, _ = totals(head)
            if total + reservation.usd > self.cap_usd:
                raise BudgetExceeded(
                    f"this call's worst case ${reservation.usd:.4f} would take spend from "
                    f"${total:.4f} past the ${self.cap_usd:.2f} cap"
                )
            head["open"][reservation.id] = {
                "usd": str(reservation.usd),
                "run": self.run,
                "label": label,
                "at": self.clock().isoformat(),
            }

        self._update(change)
        self._row("reserve", reservation, reservation.usd, {})
        return reservation

    def settle(self, reservation: Reservation, usd, detail: dict) -> None:
        """Record the observed cost, always. Then raise BudgetExceeded if it was above the
        reservation or the total is now above the cap, so no further call is made."""
        cost = _usd(usd)

        def change(head: dict) -> None:
            if head["open"].pop(reservation.id, None) is None:
                raise StorageError(f"reservation {reservation.id} is not open")
            head["settled_usd"] = str(_usd(head["settled_usd"]) + cost)
            head["runs"][self.run] = str(_usd(head["runs"].get(self.run, "0")) + cost)

        head = self._update(change)
        self._row("settle", reservation, cost, {k: str(v) for k, v in detail.items()})
        if cost > _usd(reservation.usd):
            raise BudgetExceeded(
                f"a call cost ${cost:.4f}, above its reserved worst case ${reservation.usd:.4f}"
            )
        total, _ = totals(head)
        if total > self.cap_usd:
            raise BudgetExceeded(f"spend is now ${total:.4f}, above the ${self.cap_usd:.2f} cap")
