"""Model spend ledger with a hard cap, kept outside the repository.

Every model call reserves its worst-case cost before it is sent and settles the observed cost
afterwards. A reservation that is never settled (a crash, a timeout, an unknown outcome) keeps
counting at its worst case, so the cap holds even when a call's cost is unknown. Rows are appended
under an exclusive file lock, so concurrent calls and concurrent runs share one total.

The cap counts market cost, including calls made with Alex's own provider key (BYOK), which the
gateway bills at $0 but the provider bills directly.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

TOTAL_CAP_USD = 10.0  # Alex's authorization, October 11, 2026


class BudgetExceeded(Exception):
    pass


@dataclass(frozen=True)
class Reservation:
    id: str
    usd: float
    label: str


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _totals(rows: list[dict], run: str) -> tuple[float, float]:
    """(all spend, this run's spend). Unsettled reservations count at their worst case."""
    settled = {row["id"]: row["usd"] for row in rows if row.get("kind") == "settle"}
    total = run_total = 0.0
    for row in rows:
        kind = row.get("kind")
        if kind in ("actual", "correction"):
            usd = row["usd"]
        elif kind == "reserve":
            usd = settled.get(row["id"], row["usd"])
        else:
            continue
        total += usd
        if row.get("run") == run:
            run_total += usd
    return total, run_total


class SpendLedger:
    def __init__(self, path: Path, run: str, run_cap_usd: float, cap_usd: float = TOTAL_CAP_USD):
        if run_cap_usd > cap_usd:
            raise ValueError(f"the run cap ${run_cap_usd} exceeds the total cap ${cap_usd}")
        self.path, self.run, self.run_cap_usd, self.cap_usd = path, run, run_cap_usd, cap_usd

    def _locked(self, write: dict | None = None, check_usd: float | None = None) -> tuple:
        with open(self.path, "a+", encoding="utf-8") as handle:
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
                if write is not None:
                    handle.write(json.dumps(write) + "\n")
                    handle.flush()
                return total, run_total
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def totals(self) -> tuple[float, float]:
        return self._locked()

    def reserve(self, usd: float, label: str) -> Reservation:
        reservation = Reservation(uuid.uuid4().hex, usd, label)
        row = {"kind": "reserve", "id": reservation.id, "run": self.run, "label": label}
        self._locked({**row, "usd": usd, "at": _now()}, check_usd=usd)
        return reservation

    def settle(self, reservation: Reservation, usd: float, detail: dict) -> None:
        row = {"kind": "settle", "id": reservation.id, "run": self.run, "label": reservation.label}
        self._locked({**row, "usd": usd, **detail, "at": _now()})
