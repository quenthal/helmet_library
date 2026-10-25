"""Tests of the diagnostics, which must not tell who the card's owner is or what they read."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .test_init import ACCOUNT, entry


async def test_diagnostics_leave_out_personal_details(hass: HomeAssistant, hass_client: ClientSessionGenerator) -> None:
    card = entry()
    card.add_to_hass(hass)
    with patch("custom_components.helmet.api.HelmetClient.account", AsyncMock(return_value=ACCOUNT)):
        assert await hass.config_entries.async_setup(card.entry_id)
        await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, card)

    text = json.dumps(diagnostics, ensure_ascii=False)
    for secret in ("12345678", "1234", "Liisa", "Muumipeikko", "Oodi", "i1|b1"):
        assert secret not in text
    assert diagnostics["last_update_success"] is True
    loans = diagnostics["account"]["loans"]
    assert len(loans) == 3
    assert loans[0]["due_date"] == "2026-10-22"
    assert loans[0]["renewals_remaining"] == 4
    assert diagnostics["account"]["holds"][0]["status"] == "available"
    assert diagnostics["account"]["fines_cents"] == 250
