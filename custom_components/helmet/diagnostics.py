"""Diagnostics of a library card, for telling what the integration read from the web library.

What could tell who the card's owner is, or what they read, is left out: the card number, the PIN,
the name, and the titles, authors, places and codes of the loans and holds. What is left shows
which details the parser found, which is what a broken page needs.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_CARD_NUMBER, CONF_NAME, CONF_PIN
from .coordinator import HelmetConfigEntry

TO_REDACT = {
    CONF_CARD_NUMBER,
    CONF_PIN,
    CONF_NAME,
    "title",
    "author",
    "barcode",
    "renew_id",
    "borrowing_location",
    "pickup_location",
    "pickup_number",
    "unique_id",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: HelmetConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    account = asdict(coordinator.data) if coordinator.data else None
    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "last_update_success": coordinator.last_update_success,
            "last_exception": str(coordinator.last_exception) if coordinator.last_exception else None,
            "account": _jsonable(account),
        },
        TO_REDACT,
    )


def _jsonable(value: Any) -> Any:
    """The account with its dates as text, and tuples as lists."""
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return value
