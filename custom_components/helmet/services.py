"""The renew_loans action: renews a card's loans, all or those matching titles or a due date."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import ATTR_CONFIG_ENTRY_ID, ATTR_DUE_WITHIN_DAYS, ATTR_TITLES, DOMAIN, SERVICE_RENEW_LOANS
from .coordinator import HelmetConfigEntry
from .parser import Loan
from .renewal import async_renew, loans_to_renew

RENEW_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_TITLES): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_DUE_WITHIN_DAYS): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Registers the integration's actions."""

    async def renew_loans(call: ServiceCall) -> ServiceResponse:
        entry: HelmetConfigEntry | None = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_card")
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="card_not_loaded",
                translation_placeholders={"name": entry.title},
            )

        def choose(loans: list[Loan]) -> list[Loan]:
            return loans_to_renew(
                loans,
                dt_util.now().date(),
                titles=call.data.get(ATTR_TITLES),
                due_within_days=call.data.get(ATTR_DUE_WITHIN_DAYS),
            )

        return await async_renew(hass, entry, choose)

    hass.services.async_register(
        DOMAIN, SERVICE_RENEW_LOANS, renew_loans, schema=RENEW_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
