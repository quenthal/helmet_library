"""Tests of adding cards, and of the entities a card gets."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.helmet.api import HelmetAuthError, HelmetError
from custom_components.helmet.const import CONF_CARD_NUMBER, CONF_NAME, CONF_PIN, DOMAIN
from custom_components.helmet.parser import Account, Hold, HoldStatus, Loan

ACCOUNT = Account(
    loans=[
        Loan("Muumipeikko ja pyrstötähti", date(2026, 10, 22), 4, "i1|b1"),
        Loan("Tuntematon sotilas", date(2026, 10, 9), 0, None),
        Loan("Ronja Ryövärintytär", date(2026, 10, 22), None, "i3|b3"),
    ],
    holds=[
        Hold(
            "Harry Potter",
            HoldStatus.AVAILABLE,
            pickup_location="Oodi",
            pickup_by=date(2026, 10, 23),
            pickup_number="42",
        ),
        Hold("Koiramäen talvi", HoldStatus.IN_TRANSIT, pickup_location="Kallio"),
        Hold("Kesäkirja", HoldStatus.WAITING, queue_position=7),
    ],
    fines_cents=250,
)


def entry(card: str = "12345678", name: str = "Liisa") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=name,
        unique_id=card,
        data={CONF_NAME: name, CONF_CARD_NUMBER: card, CONF_PIN: "1234"},
    )


@pytest.fixture
def login() -> AsyncMock:
    with patch("custom_components.helmet.api.HelmetClient.login", AsyncMock()) as mock:
        yield mock


@pytest.fixture
def account() -> AsyncMock:
    with patch("custom_components.helmet.api.HelmetClient.account", AsyncMock(return_value=ACCOUNT)) as mock:
        yield mock


async def test_add_card(hass: HomeAssistant, login: AsyncMock, account: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Liisa ", CONF_CARD_NUMBER: "1234 5678", CONF_PIN: "1234"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Liisa"
    assert result["data"] == {CONF_NAME: "Liisa", CONF_CARD_NUMBER: "12345678", CONF_PIN: "1234"}
    assert result["result"].unique_id == "12345678"


@pytest.mark.parametrize(("error", "shown"), [(HelmetAuthError, "invalid_auth"), (HelmetError, "cannot_connect")])
async def test_add_card_errors(hass: HomeAssistant, error: type[Exception], shown: str) -> None:
    with patch("custom_components.helmet.api.HelmetClient.login", AsyncMock(side_effect=error("no"))):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NAME: "Liisa", CONF_CARD_NUMBER: "12345678", CONF_PIN: "0000"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": shown}


async def test_same_card_twice(hass: HomeAssistant, login: AsyncMock) -> None:
    entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Toinen", CONF_CARD_NUMBER: "12345678", CONF_PIN: "1234"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_family_cards_and_their_entities(
    hass: HomeAssistant, account: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(datetime(2026, 10, 16, 12, tzinfo=dt_util.get_default_time_zone()))
    liisa, matti = entry("111", "Liisa"), entry("222", "Matti")
    for card in (liisa, matti):
        card.add_to_hass(hass)
        assert await hass.config_entries.async_setup(card.entry_id)
    await hass.async_block_till_done()

    assert liisa.state is ConfigEntryState.LOADED
    assert matti.state is ConfigEntryState.LOADED

    loans = hass.states.get("sensor.helmet_liisa_loans")
    assert loans is not None
    assert loans.state == "3"
    assert loans.attributes["loans"][0]["title"] == "Tuntematon sotilas"
    assert hass.states.get("sensor.helmet_matti_loans").state == "3"

    assert hass.states.get("sensor.helmet_liisa_overdue_loans").state == "1"
    next_due = hass.states.get("sensor.helmet_liisa_next_due_date")
    assert next_due.state == "2026-10-09"
    assert next_due.attributes["titles"] == ["Tuntematon sotilas"]
    assert hass.states.get("sensor.helmet_liisa_holds").state == "3"
    ready = hass.states.get("sensor.helmet_liisa_holds_ready_for_pickup")
    assert ready.state == "1"
    assert ready.attributes["holds"][0]["pickup_number"] == "42"
    assert float(hass.states.get("sensor.helmet_liisa_fees").state) == 2.5

    calendar = hass.states.get("calendar.helmet_liisa_due_dates")
    assert calendar is not None
    # The next event that hasn't ended: the two loans due on 22 October.
    assert calendar.attributes["message"] == "Due: 2 loans"
    assert calendar.attributes["start_time"] == "2026-10-22 00:00:00"


async def test_calendar_events(hass: HomeAssistant, account: AsyncMock) -> None:
    card = entry()
    card.add_to_hass(hass)
    assert await hass.config_entries.async_setup(card.entry_id)
    await hass.async_block_till_done()

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {"start_date_time": "2026-10-20 00:00:00", "end_date_time": "2026-10-24 00:00:00"},
        target={"entity_id": "calendar.helmet_liisa_due_dates"},
        blocking=True,
        return_response=True,
    )
    events = response["calendar.helmet_liisa_due_dates"]["events"]
    assert [event["summary"] for event in events] == ["Due: 2 loans", "Pick up hold: Harry Potter"]
    assert events[1]["location"] == "Oodi"


async def test_wrong_pin_starts_reauth(hass: HomeAssistant) -> None:
    card = entry()
    card.add_to_hass(hass)
    with patch("custom_components.helmet.api.HelmetClient.account", AsyncMock(side_effect=HelmetAuthError("no"))):
        await hass.config_entries.async_setup(card.entry_id)
        await hass.async_block_till_done()

    assert card.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_library_down_retries(hass: HomeAssistant) -> None:
    card = entry()
    card.add_to_hass(hass)
    with patch("custom_components.helmet.api.HelmetClient.account", AsyncMock(side_effect=HelmetError("down"))):
        await hass.config_entries.async_setup(card.entry_id)
    assert card.state is ConfigEntryState.SETUP_RETRY


async def test_reauth_updates_pin(hass: HomeAssistant, login: AsyncMock, account: AsyncMock) -> None:
    card = entry()
    card.add_to_hass(hass)
    result = await card.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "9999"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert card.data[CONF_PIN] == "9999"
