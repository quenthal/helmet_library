"""Tests of renewing from Home Assistant: the button and the renew_loans action."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.helmet.api import HelmetError, RenewOutcome
from custom_components.helmet.const import DOMAIN, EVENT_LOANS_RENEWED
from custom_components.helmet.renewal import loans_to_renew

from .test_init import ACCOUNT, entry

TODAY = date(2026, 10, 16)
OUTCOMES = [
    RenewOutcome("Muumipeikko ja pyrstötähti", True, date(2026, 11, 12)),
    RenewOutcome("Ronja Ryövärintytär", False, date(2026, 10, 22), "Item has holds"),
]


def test_choosing_loans() -> None:
    def titles(**kwargs) -> list[str]:
        return [loan.title for loan in loans_to_renew(ACCOUNT.loans, TODAY, **kwargs)]

    # Tuntematon sotilas can't be renewed, so it is never chosen.
    assert titles() == ["Muumipeikko ja pyrstötähti", "Ronja Ryövärintytär"]
    assert titles(titles=["ronja"]) == ["Ronja Ryövärintytär"]
    assert titles(titles=["ronja", "MUUMI"]) == ["Muumipeikko ja pyrstötähti", "Ronja Ryövärintytär"]
    assert titles(due_within_days=5) == []
    assert titles(due_within_days=6) == ["Muumipeikko ja pyrstötähti", "Ronja Ryövärintytär"]
    assert titles(titles=["sotilas"]) == []


@pytest.fixture
async def card(hass: HomeAssistant, freezer: FrozenDateTimeFactory):
    freezer.move_to(datetime(2026, 10, 16, 8, tzinfo=dt_util.get_default_time_zone()))
    with patch("custom_components.helmet.api.HelmetClient.account", AsyncMock(return_value=ACCOUNT)):
        card = entry()
        card.add_to_hass(hass)
        assert await hass.config_entries.async_setup(card.entry_id)
        await hass.async_block_till_done()
        yield card


async def test_button_renews_all(hass: HomeAssistant, card) -> None:
    events = async_capture_events(hass, EVENT_LOANS_RENEWED)
    with patch("custom_components.helmet.api.HelmetClient.renew", AsyncMock(return_value=OUTCOMES)) as renew:
        await hass.services.async_call(
            "button", "press", target={"entity_id": "button.helmet_liisa_renew_all_loans"}, blocking=True
        )

    renew.assert_awaited_once_with(["i1|b1", "i3|b3"])
    assert len(events) == 1
    assert events[0].data["name"] == "Liisa"
    assert events[0].data["renewed"] == [{"title": "Muumipeikko ja pyrstötähti", "due_date": "2026-11-12"}]
    assert events[0].data["not_renewed"] == [
        {"title": "Ronja Ryövärintytär", "due_date": "2026-10-22", "reason": "Item has holds"}
    ]


async def test_action_renews_matching_loans(hass: HomeAssistant, card) -> None:
    with patch("custom_components.helmet.api.HelmetClient.renew", AsyncMock(return_value=OUTCOMES[1:])) as renew:
        response = await hass.services.async_call(
            DOMAIN,
            "renew_loans",
            {"config_entry_id": card.entry_id, "titles": ["Ronja"], "due_within_days": 7},
            blocking=True,
            return_response=True,
        )

    renew.assert_awaited_once_with(["i3|b3"])
    assert response == {
        "renewed": [],
        "not_renewed": [{"title": "Ronja Ryövärintytär", "due_date": "2026-10-22", "reason": "Item has holds"}],
    }


async def test_action_with_nothing_to_renew(hass: HomeAssistant, card) -> None:
    with patch("custom_components.helmet.api.HelmetClient.renew", AsyncMock()) as renew:
        response = await hass.services.async_call(
            DOMAIN,
            "renew_loans",
            {"config_entry_id": card.entry_id, "due_within_days": 1},
            blocking=True,
            return_response=True,
        )
    renew.assert_not_awaited()
    assert response == {"renewed": [], "not_renewed": []}


async def test_action_errors(hass: HomeAssistant, card) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "renew_loans", {"config_entry_id": "nope"}, blocking=True)

    with (
        patch("custom_components.helmet.api.HelmetClient.renew", AsyncMock(side_effect=HelmetError("down"))),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(DOMAIN, "renew_loans", {"config_entry_id": card.entry_id}, blocking=True)
