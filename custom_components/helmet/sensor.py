"""Sensors of a library card: loans, the next due date, holds and fees."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SORT_BY_SHELF, DEFAULT_SORT_BY_SHELF
from .coordinator import HelmetConfigEntry, HelmetCoordinator
from .entity import HelmetEntity
from .library_time import due_moment, library_today
from .parser import Account, Hold, HoldStatus, Loan


@dataclass(frozen=True, kw_only=True)
class HelmetSensorDescription(SensorEntityDescription):
    """How a sensor reads its state and attributes from the account."""

    value: Callable[[Account, date], Any]
    attributes: Callable[[Account, date, HelmetConfigEntry], dict[str, Any]] | None = None


def loan_details(loan: Loan, today: date) -> dict[str, Any]:
    return {
        "title": loan.title,
        "due_date": loan.due_date.isoformat() if loan.due_date else None,
        "days_left": (loan.due_date - today).days if loan.due_date else None,
        "overdue": loan.overdue(today),
        "renewable": loan.renewable,
        "renewals_remaining": loan.renewals_remaining,
        "author": loan.author,
        "format": loan.format,
        "checkout_date": loan.checkout_date.isoformat() if loan.checkout_date else None,
        "borrowing_location": loan.borrowing_location,
        "notices": list(loan.notices),
    }


def hold_details(hold: Hold) -> dict[str, Any]:
    return {
        "title": hold.title,
        "status": hold.status.value,
        "queue_position": hold.queue_position,
        "pickup_location": hold.pickup_location,
        "pickup_by": hold.pickup_by.isoformat() if hold.pickup_by else None,
        "pickup_number": hold.pickup_number,
        "frozen": hold.frozen,
    }


def sorted_loans(account: Account) -> list[Loan]:
    return sorted(account.loans, key=lambda loan: (loan.due_date or date.max, loan.title))


def next_due_date(account: Account, today: date) -> date | None:
    """The earliest due date, overdue ones included, since those need returning first."""
    dates = [loan.due_date for loan in account.loans if loan.due_date]
    return min(dates) if dates else None


def next_due_moment(account: Account, today: date) -> datetime | None:
    due = next_due_date(account, today)
    return due_moment(due) if due else None


def next_due_attributes(account: Account, today: date) -> dict[str, Any]:
    due = next_due_date(account, today)
    loans = [loan for loan in account.loans if due is not None and loan.due_date == due]
    return {
        "date": due.isoformat() if due else None,
        "days_left": (due - today).days if due else None,
        "titles": [loan.title for loan in loans],
        "count": len(loans),
        "renewable": all(loan.renewable for loan in loans) if loans else None,
    }


def sort_by_shelf(entry: HelmetConfigEntry) -> bool:
    return bool(entry.options.get(CONF_SORT_BY_SHELF, DEFAULT_SORT_BY_SHELF))


def shelf_sort_key(hold: Hold) -> tuple[int, list[tuple[int, int | str]], date, str]:
    if hold.pickup_number is None:
        return (1, [], hold.pickup_by or date.max, hold.title)
    tokens: list[tuple[int, int | str]] = [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", hold.pickup_number.strip())
        if part
    ]
    return (0, tokens, hold.pickup_by or date.max, hold.title)


def available_holds(account: Account, *, sort_by_shelf: bool = False) -> list[Hold]:
    holds = [hold for hold in account.holds if hold.status is HoldStatus.AVAILABLE]
    if sort_by_shelf:
        return sorted(holds, key=shelf_sort_key)
    return sorted(holds, key=lambda hold: (hold.pickup_by or date.max, hold.title))


def fines_euros(account: Account, today: date) -> float | None:
    return None if account.fines_cents is None else account.fines_cents / 100


SENSORS: tuple[HelmetSensorDescription, ...] = (
    HelmetSensorDescription(
        key="loans",
        value=lambda account, today: len(account.loans),
        attributes=lambda account, today, entry: {
            "loans": [loan_details(loan, today) for loan in sorted_loans(account)]
        },
    ),
    HelmetSensorDescription(
        key="overdue_loans",
        value=lambda account, today: sum(loan.overdue(today) for loan in account.loans),
        attributes=lambda account, today, entry: {
            "titles": [loan.title for loan in sorted_loans(account) if loan.overdue(today)]
        },
    ),
    HelmetSensorDescription(
        key="next_due_date",
        # A timestamp rather than a date, so that the frontend tells it as "in 3 days" or "tomorrow".
        device_class=SensorDeviceClass.TIMESTAMP,
        value=next_due_moment,
        attributes=lambda account, today, entry: next_due_attributes(account, today),
    ),
    HelmetSensorDescription(
        key="holds",
        value=lambda account, today: len(account.holds),
        attributes=lambda account, today, entry: {"holds": [hold_details(hold) for hold in account.holds]},
    ),
    HelmetSensorDescription(
        key="holds_available",
        value=lambda account, today: len(available_holds(account)),
        attributes=lambda account, today, entry: {
            "holds": [hold_details(hold) for hold in available_holds(account, sort_by_shelf=sort_by_shelf(entry))]
        },
    ),
    HelmetSensorDescription(
        key="fines",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        value=fines_euros,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HelmetConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(HelmetSensor(entry.runtime_data, description) for description in SENSORS)


class HelmetSensor(HelmetEntity, SensorEntity):
    """A sensor of one library card."""

    entity_description: HelmetSensorDescription

    def __init__(self, coordinator: HelmetCoordinator, description: HelmetSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.coordinator.data, library_today())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes is None:
            return None
        return self.entity_description.attributes(self.coordinator.data, library_today(), self.coordinator.config_entry)
