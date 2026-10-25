"""A button that renews the loans of a card that are due soon."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_RENEW_WITHIN_DAYS, DEFAULT_RENEW_WITHIN_DAYS
from .coordinator import HelmetConfigEntry, HelmetCoordinator
from .entity import HelmetEntity
from .library_time import library_today
from .renewal import async_renew, loans_to_renew


async def async_setup_entry(
    hass: HomeAssistant, entry: HelmetConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([RenewDueSoonButton(entry.runtime_data)])


def renew_within_days(entry: HelmetConfigEntry) -> int:
    return int(entry.options.get(CONF_RENEW_WITHIN_DAYS, DEFAULT_RENEW_WITHIN_DAYS))


class RenewDueSoonButton(HelmetEntity, ButtonEntity):
    """Renews the renewable loans of the card that are due within a few days, overdue ones included.

    Only those, since every renewal uses up one of the item's renewals. A loan just renewed is no longer
    due soon, so pressing the button again, or twice in a row, doesn't use another.
    """

    def __init__(self, coordinator: HelmetCoordinator) -> None:
        super().__init__(coordinator, "renew_due_soon")

    async def async_press(self) -> None:
        days = renew_within_days(self.coordinator.config_entry)
        await async_renew(
            self.hass,
            self.coordinator.config_entry,
            lambda loans: loans_to_renew(loans, library_today(), due_within_days=days),
        )
