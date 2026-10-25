"""Tests of renewing from Home Assistant: the button and the renew_loans action."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.helmet.api import HelmetError, RenewOutcome
from custom_components.helmet.const import CONF_RENEW_WITHIN_DAYS, DOMAIN, EVENT_LOANS_RENEWED
from custom_components.helmet.parser import Account
from custom_components.helmet.renewal import async_renew, loans_to_renew

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


BUTTON = "button.helmet_liisa_renew_loans_due_soon"


async def press(hass: HomeAssistant) -> None:
    await hass.services.async_call("button", "press", target={"entity_id": BUTTON}, blocking=True)


async def test_button_leaves_loans_not_due_soon(hass: HomeAssistant, card) -> None:
    # Both renewable loans are due in 6 days, and the button renews those due within 3.
    with patch("custom_components.helmet.api.HelmetClient.renew", AsyncMock()) as renew:
        await press(hass)
    renew.assert_not_awaited()


async def test_button_renews_loans_due_soon(hass: HomeAssistant, card) -> None:
    hass.config_entries.async_update_entry(card, options={CONF_RENEW_WITHIN_DAYS: 6})
    events = async_capture_events(hass, EVENT_LOANS_RENEWED)
    with patch("custom_components.helmet.api.HelmetClient.renew", AsyncMock(return_value=OUTCOMES)) as renew:
        await press(hass)

    renew.assert_awaited_once_with(["i1|b1", "i3|b3"])
    assert len(events) == 1
    assert events[0].data["name"] == "Liisa"
    assert events[0].data["renewed"] == [{"title": "Muumipeikko ja pyrstötähti", "due_date": "2026-11-12"}]
    assert events[0].data["not_renewed"] == [
        {"title": "Ronja Ryövärintytär", "due_date": "2026-10-22", "reason": "Item has holds"}
    ]


async def test_renewals_of_a_card_wait_for_each_other(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """The second of two renewals at once chooses from the loans as the first left them."""
    freezer.move_to(datetime(2026, 10, 20, 8, tzinfo=dt_util.get_default_time_zone()))
    renewed = Account(loans=[replace(loan, due_date=date(2026, 11, 17)) for loan in ACCOUNT.loans])
    done = False

    # Both wait on the library, so that the two renewals could interleave.
    async def account(self: object) -> Account:
        await asyncio.sleep(0)
        return renewed if done else ACCOUNT

    async def slow_renew(ids: list[str]) -> list[RenewOutcome]:
        nonlocal done
        for _ in range(5):
            await asyncio.sleep(0)
        done = True
        return OUTCOMES

    renew = AsyncMock(side_effect=slow_renew)
    with (
        patch("custom_components.helmet.api.HelmetClient.account", account),
        patch("custom_components.helmet.api.HelmetClient.renew", renew),
    ):
        card = entry()
        card.add_to_hass(hass)
        assert await hass.config_entries.async_setup(card.entry_id)
        await hass.async_block_till_done()

        def due_soon(loans: list) -> list:
            return loans_to_renew(loans, date(2026, 10, 20), due_within_days=3)

        await asyncio.gather(async_renew(hass, card, due_soon), async_renew(hass, card, due_soon))

    assert renew.await_count == 1


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
