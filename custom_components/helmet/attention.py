"""Whether a library card needs someone to do something, and why.

Kept free of Home Assistant, so that the console tool tells the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from .parser import Account, HoldStatus

# A loan that can't be renewed needs returning when it is due within this many days (0 is today).
DUE_SOON_DAYS = 1


class Reason(StrEnum):
    """Why a card needs attention."""

    OVERDUE = "overdue"
    DUE_SOON = "due_soon"
    HOLD_READY = "hold_ready"
    FEES = "fees"


@dataclass(frozen=True)
class Attention:
    """What on a card needs attention."""

    overdue: tuple[str, ...] = ()
    # Loans due today or tomorrow that can't be renewed.
    due_soon: tuple[str, ...] = ()
    holds_ready: tuple[str, ...] = ()
    fees_cents: int = 0

    @property
    def reasons(self) -> list[Reason]:
        reasons = []
        if self.overdue:
            reasons.append(Reason.OVERDUE)
        if self.due_soon:
            reasons.append(Reason.DUE_SOON)
        if self.holds_ready:
            reasons.append(Reason.HOLD_READY)
        if self.fees_cents > 0:
            reasons.append(Reason.FEES)
        return reasons

    @property
    def needed(self) -> bool:
        return bool(self.reasons)


def attention(account: Account, today: date) -> Attention:
    last_day = today + timedelta(days=DUE_SOON_DAYS)
    loans = sorted(account.loans, key=lambda loan: (loan.due_date or date.max, loan.title))
    return Attention(
        overdue=tuple(loan.title for loan in loans if loan.overdue(today)),
        due_soon=tuple(
            loan.title
            for loan in loans
            if loan.due_date is not None and today <= loan.due_date <= last_day and not loan.renewable
        ),
        holds_ready=tuple(hold.title for hold in account.holds if hold.status is HoldStatus.AVAILABLE),
        fees_cents=account.fines_cents or 0,
    )
