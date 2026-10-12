"""Model spend ledger with a hard cap, kept outside the repository.

Every model call reserves its worst-case cost before it is sent and settles the observed cost
afterwards. A reservation that is never settled (a crash, a timeout, an unknown outcome) keeps
counting at its worst case. A call that turns out to cost more than its reservation is still
recorded at its real cost, and then stops the run (BudgetExceeded): the caps bound what is
reserved, and an overrun can exceed them by at most that one call's difference. Rows are appended
under an exclusive file lock, so concurrent calls and concurrent runs share one total. Sums and
cap checks use exact decimals; the file is created owner-only.

The cap counts market cost, including calls made with Alex's own provider key (BYOK), which the
gateway bills at $0 but the provider bills directly.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

TOTAL_CAP_USD = 10.0  # Alex's authorization, October 11, 2026


class BudgetExceeded(Exception):
    fell_back = False  # set by gateway.call on a call that overran after falling back


@dataclass(frozen=True)
class Reservation:
    id: str
    usd: Decimal | float
    label: str


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _usd(value) -> Decimal:
    """Exact decimal dollars. A float goes through its shortest repr, so 0.1 stays 0.1."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _totals(rows: list[dict], run: str) -> tuple[Decimal, Decimal]:
    """(all spend, this run's spend). Unsettled reservations count at their worst case."""
    settled = {row["id"]: row["usd"] for row in rows if row.get("kind") == "settle"}
    total = run_total = Decimal(0)
    for row in rows:
        kind = row.get("kind")
        if kind in ("actual", "correction"):
            usd = _usd(row["usd"])
        elif kind == "reserve":
            usd = _usd(settled.get(row["id"], row["usd"]))
        else:
            continue
        total += usd
        if row.get("run") == run:
            run_total += usd
    return total, run_total


class SpendLedger:
    def __init__(self, path: Path, run: str, run_cap_usd, cap_usd=TOTAL_CAP_USD):
        run_cap, cap = _usd(run_cap_usd), _usd(cap_usd)
        if run_cap > cap:
            raise ValueError(f"the run cap ${run_cap} exceeds the total cap ${cap}")
        self.path, self.run, self.run_cap_usd, self.cap_usd = path, run, run_cap, cap

    def _locked(self, write: dict | None = None, check_usd: Decimal | None = None) -> tuple:
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        with open(descriptor, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                handle.seek(0)
                rows = [json.loads(line) for line in handle if line.strip()]
                total, run_total = _totals(rows, self.run)
                if check_usd is not None:
                    if total + check_usd > self.cap_usd:
                        raise BudgetExceeded(
                            f"total spend ${total:.4f} + this call's worst case "
                            f"${check_usd:.4f} would exceed the ${self.cap_usd:.2f} cap"
                        )
                    if run_total + check_usd > self.run_cap_usd:
                        raise BudgetExceeded(
                            f"run spend ${run_total:.4f} + this call's worst case "
                            f"${check_usd:.4f} would exceed the run cap ${self.run_cap_usd:.2f}"
                        )
                if write is None:
                    return total, run_total
                handle.write(json.dumps(write, default=str) + "\n")  # Decimal as text
                handle.flush()
                return _totals([*rows, write], self.run)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def totals(self) -> tuple[float, float]:
        total, run_total = self._locked()
        return float(total), float(run_total)

    def reserve(self, usd, label: str) -> Reservation:
        reservation = Reservation(uuid.uuid4().hex, usd, label)
        row = {"kind": "reserve", "id": reservation.id, "run": self.run, "label": label}
        self._locked({**row, "usd": usd, "at": _now()}, check_usd=_usd(usd))
        return reservation

    def settle(self, reservation: Reservation, usd, detail: dict) -> None:
        """Record the observed cost, always. Then raise BudgetExceeded if it was above the
        reservation or the totals are now above a cap, so no further call is made."""
        row = {"kind": "settle", "id": reservation.id, "run": self.run, "label": reservation.label}
        total, run_total = self._locked({**row, "usd": usd, **detail, "at": _now()})
        if _usd(usd) > _usd(reservation.usd):
            raise BudgetExceeded(
                f"a call cost ${usd:.4f}, above its reserved worst case ${reservation.usd:.4f}"
            )
        if total > self.cap_usd or run_total > self.run_cap_usd:
            raise BudgetExceeded(
                f"spend is now ${total:.4f} total and ${run_total:.4f} this run, above a cap"
            )
