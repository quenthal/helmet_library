"""The library's own time: due dates and pickup days are days in Helsinki.

Kept apart from Home Assistant's time zone, so that a due date means the same
moment wherever Home Assistant, or the person looking at it, happens to be.
"""

from __future__ import annotations

from datetime import date, datetime, time, tzinfo

from homeassistant.util import dt as dt_util

LIBRARY_TIME_ZONE = "Europe/Helsinki"
# Loans are due by the end of the day; the web library shows 23:59.
DUE_TIME = time(23, 59)


async def async_load_time_zone() -> None:
    """Loads the library's time zone once, so that reading it later doesn't touch the disk."""
    await dt_util.async_get_time_zone(LIBRARY_TIME_ZONE)


def library_time_zone() -> tzinfo:
    zone = dt_util.get_time_zone(LIBRARY_TIME_ZONE)
    if zone is None:
        raise RuntimeError(f"Unknown time zone {LIBRARY_TIME_ZONE}")
    return zone


def library_today() -> date:
    """Today in Helsinki."""
    return dt_util.now(library_time_zone()).date()


def due_moment(day: date) -> datetime:
    """The last minute of a due day in Helsinki: a loan is overdue only after it."""
    return datetime.combine(day, DUE_TIME, tzinfo=library_time_zone())
