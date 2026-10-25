"""Keeps one library card's account up to date."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HelmetAuthError, HelmetClient, HelmetError
from .const import DOMAIN, UPDATE_INTERVAL
from .parser import Account

_LOGGER = logging.getLogger(__name__)

type HelmetConfigEntry = ConfigEntry[HelmetCoordinator]


class HelmetCoordinator(DataUpdateCoordinator[Account]):
    """Reads a card's loans, holds and fees once an hour."""

    config_entry: HelmetConfigEntry

    def __init__(self, hass: HomeAssistant, entry: HelmetConfigEntry, client: HelmetClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        # Held for the whole of a renewal, from choosing the loans to reading them again.
        self.renew_lock = asyncio.Lock()

    async def _async_update_data(self) -> Account:
        try:
            return await self.client.account()
        except HelmetAuthError as err:
            raise ConfigEntryAuthFailed("The library didn't accept the card number and PIN") from err
        except HelmetError as err:
            raise UpdateFailed(str(err)) from err
