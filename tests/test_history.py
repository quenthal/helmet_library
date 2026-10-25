"""Tests of what goes to Home Assistant's history."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from .test_init import ACCOUNT, entry


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock: Recorder, enable_custom_integrations: None) -> None:
    """The recorder has to start before Home Assistant does."""
    return


async def test_attributes_stay_out_of_the_history(hass: HomeAssistant) -> None:
    """The lists of loans and holds would fill the database; the history keeps only the states."""
    card = entry()
    card.add_to_hass(hass)
    with patch("custom_components.helmet.api.HelmetClient.account", AsyncMock(return_value=ACCOUNT)):
        assert await hass.config_entries.async_setup(card.entry_id)
        await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    loans = "sensor.helmet_liisa_loans"
    assert "loans" in hass.states.get(loans).attributes
    history = await get_instance(hass).async_add_executor_job(
        get_significant_states, hass, dt_util.utcnow() - timedelta(hours=1), None, [loans]
    )
    recorded = history[loans][-1]
    assert recorded.state == "3"
    assert "loans" not in recorded.attributes
    assert recorded.attributes["friendly_name"] == "Helmet Liisa Loans"
