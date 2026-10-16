"""A button that renews every loan of a card that can be renewed."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import HelmetConfigEntry, HelmetCoordinator
from .entity import HelmetEntity
from .renewal import async_renew, loans_to_renew


async def async_setup_entry(
    hass: HomeAssistant, entry: HelmetConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([RenewAllButton(entry.runtime_data)])


class RenewAllButton(HelmetEntity, ButtonEntity):
    """Renews all renewable loans of the card."""

    def __init__(self, coordinator: HelmetCoordinator) -> None:
        super().__init__(coordinator, "renew_all")

    async def async_press(self) -> None:
        await async_renew(
            self.hass, self.coordinator.config_entry, lambda loans: loans_to_renew(loans, dt_util.now().date())
        )
