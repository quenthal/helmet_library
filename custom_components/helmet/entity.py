"""The base of the Helmet entities: one device per library card."""

from __future__ import annotations

from homeassistant.const import MATCH_ALL
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BASE_URL, DOMAIN
from .coordinator import HelmetCoordinator


class HelmetEntity(CoordinatorEntity[HelmetCoordinator]):
    """An entity of one library card."""

    _attr_has_entity_name = True
    # The attributes list every loan and hold, and change daily as the days left count down. Only the
    # states go to the history, so that a long loan list doesn't fill the database.
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, coordinator: HelmetCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=f"Helmet {entry.title}",
            manufacturer="Helmet-kirjastot",
            model="Library card",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"{BASE_URL}/MyResearch/Home",
        )
