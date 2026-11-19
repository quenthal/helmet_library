"""Tests of adding cards, and of the entities a card gets."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.helmet.api import HelmetAuthError, HelmetError
from custom_components.helmet.const import (
    CONF_CARD_NUMBER,
    CONF_NAME,
    CONF_PIN,
    CONF_RENEW_WITHIN_DAYS,
    CONF_SORT_BY_SHELF,
    DOMAIN,
)
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
    # Home Assistant somewhere else than the library: the due dates still are days in Helsinki.
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to(datetime(2026, 10, 16, 12, tzinfo=ZoneInfo("Europe/Helsinki")))
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
    # 9 October 23:59 in Helsinki, which is 20:59 UTC in summer time.
    assert next_due.state == "2026-10-09T20:59:00+00:00"
    assert next_due.attributes["device_class"] == "timestamp"
    assert next_due.attributes["date"] == "2026-10-09"
    assert next_due.attributes["days_left"] == -7
    assert next_due.attributes["titles"] == ["Tuntematon sotilas"]
    assert loans.attributes["loans"][1]["days_left"] == 6
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


async def test_today_is_helsinki_today(hass: HomeAssistant, account: AsyncMock, freezer: FrozenDateTimeFactory) -> None:
    """At 1:30 on 10 October in Helsinki it is still 9 October in New York, but the loan due 9 October is overdue."""
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to(datetime(2026, 10, 10, 1, 30, tzinfo=ZoneInfo("Europe/Helsinki")))
    card = entry()
    card.add_to_hass(hass)
    assert await hass.config_entries.async_setup(card.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.helmet_liisa_overdue_loans").state == "1"
    assert hass.states.get("sensor.helmet_liisa_next_due_date").attributes["days_left"] == -1


async def test_options(hass: HomeAssistant, account: AsyncMock) -> None:
    card = entry()
    card.add_to_hass(hass)
    assert await hass.config_entries.async_setup(card.entry_id)

    result = await hass.config_entries.options.async_init(card.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_RENEW_WITHIN_DAYS: 5.0, CONF_SORT_BY_SHELF: True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert card.options == {CONF_RENEW_WITHIN_DAYS: 5, CONF_SORT_BY_SHELF: True}


async def test_holds_sorted_by_shelf_option(hass: HomeAssistant) -> None:
    account_data = Account(
        holds=[
            Hold("Title B", HoldStatus.AVAILABLE, pickup_by=date(2026, 10, 30), pickup_number="100"),
            Hold("Title A", HoldStatus.AVAILABLE, pickup_by=date(2026, 10, 20), pickup_number="12"),
            Hold("Title C", HoldStatus.AVAILABLE, pickup_by=date(2026, 10, 25), pickup_number="2"),
            Hold("Title D", HoldStatus.AVAILABLE, pickup_by=date(2026, 10, 15), pickup_number=None),
        ]
    )
    card = entry()
    card.add_to_hass(hass)
    with patch("custom_components.helmet.api.HelmetClient.account", AsyncMock(return_value=account_data)):
        assert await hass.config_entries.async_setup(card.entry_id)
        await hass.async_block_till_done()

        # By default (sort_by_shelf=False), sorted by pickup_by date
        ready = hass.states.get("sensor.helmet_liisa_holds_ready_for_pickup")
        assert ready is not None
        assert [h["title"] for h in ready.attributes["holds"]] == ["Title D", "Title A", "Title C", "Title B"]

        # Enable sort_by_shelf
        result = await hass.config_entries.options.async_init(card.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_RENEW_WITHIN_DAYS: 3, CONF_SORT_BY_SHELF: True}
        )
        await hass.async_block_till_done()

        ready = hass.states.get("sensor.helmet_liisa_holds_ready_for_pickup")
        assert ready is not None
        assert [h["title"] for h in ready.attributes["holds"]] == ["Title C", "Title A", "Title B", "Title D"]


def test_available_holds_sorting() -> None:
    from custom_components.helmet.sensor import available_holds

    holds = [
        Hold("None 1", HoldStatus.AVAILABLE, pickup_by=date(2026, 10, 10), pickup_number=None),
        Hold("Alpha B", HoldStatus.AVAILABLE, pickup_by=date(2026, 10, 20), pickup_number="B"),
        Hold("Shelf 10B", HoldStatus.AVAILABLE, pickup_by=date(2026, 10, 20), pickup_number="10B"),
        Hold("Shelf 10A", HoldStatus.AVAILABLE, pickup_by=date(2026, 10, 20), pickup_number="10A"),
        Hold("Shelf 2", HoldStatus.AVAILABLE, pickup_by=date(2026, 10, 20), pickup_number="2"),
        Hold("Shelf 100", HoldStatus.AVAILABLE, pickup_by=date(2026, 10, 20), pickup_number="100"),
        Hold("Alpha A", HoldStatus.AVAILABLE, pickup_by=date(2026, 10, 20), pickup_number="A"),
        Hold("Waiting", HoldStatus.WAITING, pickup_number="1"),
    ]
    account_obj = Account(holds=holds)

    # By default, sorted by pickup_by then title
    default_sorted = available_holds(account_obj, sort_by_shelf=False)
    assert [h.title for h in default_sorted] == [
        "None 1",
        "Alpha A",
        "Alpha B",
        "Shelf 100",
        "Shelf 10A",
        "Shelf 10B",
        "Shelf 2",
    ]

    # By shelf, natural numeric sort, alphanumeric, and holds without shelf at the end
    shelf_sorted = available_holds(account_obj, sort_by_shelf=True)
    assert [h.title for h in shelf_sorted] == [
        "Shelf 2",
        "Shelf 10A",
        "Shelf 10B",
        "Shelf 100",
        "Alpha A",
        "Alpha B",
        "None 1",
    ]


async def test_renamed_button_keeps_its_entity(hass: HomeAssistant, account: AsyncMock) -> None:
    """The "Renew all loans" button of 0.4 keeps its entity id, so that the automations using it keep working."""
    card = entry()
    card.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "button", DOMAIN, "12345678_renew_all", config_entry=card, suggested_object_id="helmet_liisa_renew_all_loans"
    )

    assert await hass.config_entries.async_setup(card.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(old.entity_id).unique_id == "12345678_renew_due_soon"
    assert hass.states.get("button.helmet_liisa_renew_all_loans") is not None
    assert hass.states.get("button.helmet_liisa_renew_loans_due_soon") is None
