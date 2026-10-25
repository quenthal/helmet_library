"""The Helmet libraries integration: the loans, holds and fees of a library card.

Each library card is a config entry of its own, so a family adds one entry per
member, each with its own card number and PIN.
"""

from __future__ import annotations

import aiohttp
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import HelmetClient
from .const import CONF_CARD_NUMBER, CONF_PIN, DOMAIN
from .coordinator import HelmetConfigEntry, HelmetCoordinator
from .library_time import async_load_time_zone
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Loads the library's time zone, and registers the actions, which work on any of the cards."""
    await async_load_time_zone()
    async_setup_services(hass)
    return True


def create_client(hass: HomeAssistant, card_number: str, pin: str) -> HelmetClient:
    """A client with a cookie jar of its own, since the login lives in the session cookie.

    The session shares Home Assistant's connections, and Home Assistant closes it when it stops.
    """
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    return HelmetClient(session, card_number, pin)


async def async_setup_entry(hass: HomeAssistant, entry: HelmetConfigEntry) -> bool:
    """Sets up one library card."""
    await _async_migrate_unique_ids(hass, entry)
    client = create_client(hass, entry.data[CONF_CARD_NUMBER], entry.data[CONF_PIN])
    coordinator = HelmetCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_migrate_unique_ids(hass: HomeAssistant, entry: HelmetConfigEntry) -> None:
    """Keeps the entities of renamed keys, with their entity ids and the automations that use them.

    The "Renew all loans" button became "Renew loans due soon" in 0.5.
    """
    renamed = {f"{entry.unique_id}_renew_all": f"{entry.unique_id}_renew_due_soon"}

    @callback
    def migrate(entity: er.RegistryEntry) -> dict[str, str] | None:
        if entity.unique_id in renamed:
            return {"new_unique_id": renamed[entity.unique_id]}
        return None

    await er.async_migrate_entries(hass, entry.entry_id, migrate)


async def async_unload_entry(hass: HomeAssistant, entry: HelmetConfigEntry) -> bool:
    """Unloads one library card."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
