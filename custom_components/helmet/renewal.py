"""Renews a card's loans for the button and the renew_loans action, and tells automations how it went."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import HelmetAuthError, HelmetError, RenewOutcome
from .const import DOMAIN, EVENT_LOANS_RENEWED
from .coordinator import HelmetConfigEntry
from .parser import Loan


def loans_to_renew(
    loans: Iterable[Loan],
    today: date,
    titles: Iterable[str] | None = None,
    due_within_days: int | None = None,
) -> list[Loan]:
    """The renewable loans that match: a part of one of the titles, and due within the given number of days."""
    wanted = [title.casefold() for title in titles or [] if title.strip()]
    last_day = None if due_within_days is None else today + timedelta(days=due_within_days)
    chosen = []
    for loan in loans:
        if not loan.renewable:
            continue
        if wanted and not any(title in loan.title.casefold() for title in wanted):
            continue
        if last_day is not None and (loan.due_date is None or loan.due_date > last_day):
            continue
        chosen.append(loan)
    return chosen


def outcomes_response(outcomes: list[RenewOutcome]) -> dict[str, Any]:
    """The outcomes as the action's response and the event's data."""
    return {
        "renewed": [
            {"title": outcome.title, "due_date": _iso(outcome.due_date)} for outcome in outcomes if outcome.renewed
        ],
        "not_renewed": [
            {"title": outcome.title, "due_date": _iso(outcome.due_date), "reason": outcome.message}
            for outcome in outcomes
            if not outcome.renewed
        ],
    }


async def async_renew(
    hass: HomeAssistant, entry: HelmetConfigEntry, choose: Callable[[list[Loan]], list[Loan]]
) -> dict[str, Any]:
    """Renews the loans chosen from the card's current loans, and fires an event with the outcomes.

    The loans are read again first, since the entities' copy can be an hour old. Renewals of a card
    wait for each other, so that the second of two presses chooses from the loans the first renewed.
    """
    coordinator = entry.runtime_data
    async with coordinator.renew_lock:
        return await _async_renew(hass, entry, choose)


async def _async_renew(
    hass: HomeAssistant, entry: HelmetConfigEntry, choose: Callable[[list[Loan]], list[Loan]]
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="renew_failed",
            translation_placeholders={"name": entry.title, "error": str(coordinator.last_exception)},
        )
    ids = [loan.renew_id for loan in choose(coordinator.data.loans) if loan.renew_id]
    if ids:
        try:
            outcomes = await coordinator.client.renew(ids)
        except HelmetAuthError as err:
            entry.async_start_reauth(hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
                translation_placeholders={"name": entry.title},
            ) from err
        except HelmetError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="renew_failed",
                translation_placeholders={"name": entry.title, "error": str(err)},
            ) from err
        await coordinator.async_refresh()
    else:
        outcomes = []

    response = outcomes_response(outcomes)
    hass.bus.async_fire(EVENT_LOANS_RENEWED, {"config_entry_id": entry.entry_id, "name": entry.title, **response})
    return response


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None
