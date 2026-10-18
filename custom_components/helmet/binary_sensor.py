"""A binary sensor that is on when a library card needs someone to do something."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .attention import Attention, attention
from .coordinator import HelmetConfigEntry, HelmetCoordinator
from .entity import HelmetEntity
from .library_time import library_today


async def async_setup_entry(
    hass: HomeAssistant, entry: HelmetConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([AttentionSensor(entry.runtime_data)])


class AttentionSensor(HelmetEntity, BinarySensorEntity):
    """On when a loan is overdue, a loan that can't be renewed is due soon, a hold is ready, or there are fees."""

    def __init__(self, coordinator: HelmetCoordinator) -> None:
        super().__init__(coordinator, "attention")

    def _attention(self) -> Attention:
        return attention(self.coordinator.data, library_today())

    @property
    def is_on(self) -> bool:
        return self._attention().needed

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        found = self._attention()
        return {
            "reasons": [reason.value for reason in found.reasons],
            "overdue": list(found.overdue),
            "due_soon": list(found.due_soon),
            "holds_ready": list(found.holds_ready),
            "fees": found.fees_cents / 100,
        }
