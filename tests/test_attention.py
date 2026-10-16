"""Tests of telling when a card needs attention."""

from datetime import date, datetime

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.helmet.attention import Reason, attention
from custom_components.helmet.parser import Account, Hold, HoldStatus, Loan

from .test_init import ACCOUNT, account, entry  # noqa: F401

TODAY = date(2026, 10, 16)


def test_all_good() -> None:
    found = attention(Account(loans=[Loan("A", date(2026, 10, 17), renew_id="1")], fines_cents=0), TODAY)
    # Due tomorrow, but it can be renewed.
    assert not found.needed
    assert found.reasons == []


def test_reasons() -> None:
    found = attention(ACCOUNT, TODAY)
    assert found.reasons == [Reason.OVERDUE, Reason.HOLD_READY, Reason.FEES]
    assert found.overdue == ("Tuntematon sotilas",)
    assert found.holds_ready == ("Harry Potter",)
    assert found.fees_cents == 250


def test_due_soon_only_when_it_cant_be_renewed() -> None:
    loans = [
        Loan("Today", TODAY),
        Loan("Tomorrow", date(2026, 10, 17)),
        Loan("Later", date(2026, 10, 18)),
        Loan("Renewable", TODAY, renew_id="1"),
    ]
    found = attention(Account(loans=loans), TODAY)
    assert found.due_soon == ("Today", "Tomorrow")
    assert found.reasons == [Reason.DUE_SOON]


def test_in_transit_hold_needs_nothing() -> None:
    found = attention(Account(holds=[Hold("A", HoldStatus.IN_TRANSIT)]), TODAY)
    assert not found.needed


async def test_binary_sensor(hass: HomeAssistant, account, freezer: FrozenDateTimeFactory) -> None:  # noqa: F811
    freezer.move_to(datetime(2026, 10, 16, 12, tzinfo=dt_util.get_default_time_zone()))
    card = entry()
    card.add_to_hass(hass)
    assert await hass.config_entries.async_setup(card.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.helmet_liisa_needs_attention")
    assert state is not None
    assert state.state == "on"
    assert state.attributes["reasons"] == ["overdue", "hold_ready", "fees"]
    assert state.attributes["overdue"] == ["Tuntematon sotilas"]
    assert state.attributes["fees"] == 2.5
