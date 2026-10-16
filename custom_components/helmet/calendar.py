"""A calendar of a library card: the due dates of its loans and the last pickup days of its holds."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import HelmetConfigEntry, HelmetCoordinator
from .entity import HelmetEntity
from .parser import Account, HoldStatus

# The calendar's events are made in code, so their texts are here rather than in the translations.
TEXTS = {
    "fi": {
        "due_one": "Eräpäivä: {title}",
        "due_many": "Eräpäivä: {count} lainaa",
        "pickup": "Nouda varaus: {title}",
        "pickup_number": "Noutonumero",
    },
    "en": {
        "due_one": "Due: {title}",
        "due_many": "Due: {count} loans",
        "pickup": "Pick up hold: {title}",
        "pickup_number": "Pickup number",
    },
}


def events(account: Account, language: str) -> list[CalendarEvent]:
    """All-day events for the due dates, one per day, and for the last pickup day of each available hold."""
    texts = TEXTS["fi" if language.startswith("fi") else "en"]
    result: list[CalendarEvent] = []

    by_date: dict[date, list[str]] = defaultdict(list)
    for loan in account.loans:
        if loan.due_date:
            by_date[loan.due_date].append(loan.title)
    for day, titles in by_date.items():
        titles.sort()
        summary = (
            texts["due_one"].format(title=titles[0])
            if len(titles) == 1
            else texts["due_many"].format(count=len(titles))
        )
        result.append(
            CalendarEvent(start=day, end=day + timedelta(days=1), summary=summary, description="\n".join(titles))
        )

    for hold in account.holds:
        if hold.status is not HoldStatus.AVAILABLE or hold.pickup_by is None:
            continue
        details = [hold.pickup_location or ""]
        if hold.pickup_number:
            details.append(f"{texts['pickup_number']}: {hold.pickup_number}")
        result.append(
            CalendarEvent(
                start=hold.pickup_by,
                end=hold.pickup_by + timedelta(days=1),
                summary=texts["pickup"].format(title=hold.title),
                description="\n".join(line for line in details if line) or None,
                location=hold.pickup_location,
            )
        )

    result.sort(key=lambda event: (event.start, event.summary))
    return result


async def async_setup_entry(
    hass: HomeAssistant, entry: HelmetConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([HelmetCalendar(entry.runtime_data)])


class HelmetCalendar(HelmetEntity, CalendarEntity):
    """The due dates and pickup days of one library card."""

    def __init__(self, coordinator: HelmetCoordinator) -> None:
        super().__init__(coordinator, "calendar")

    def _events(self) -> list[CalendarEvent]:
        return events(self.coordinator.data, self.hass.config.language)

    @property
    def event(self) -> CalendarEvent | None:
        """The next event: today's or a later one."""
        today = dt_util.now().date()
        return next((event for event in self._events() if event.end > today), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [
            event
            for event in self._events()
            if event.end_datetime_local > start_date and event.start_datetime_local < end_date
        ]
