"""Tests of reading the web library's pages."""

from datetime import date

from custom_components.helmet import parser
from custom_components.helmet.parser import HoldStatus

from . import fixture


def test_login_form_fills_hidden_fields_and_picks_helmet() -> None:
    form = parser.login_form(fixture("login.html"), "https://helmet.finna.fi/MyResearch/UserLogin", "helmet")

    assert form is not None
    assert form.action == "https://helmet.finna.fi/MyResearch/Home"
    assert form.fields == {
        "target": "helmet",
        "username": "",
        "password": "",
        "auth_method": "MultiILS",
        "csrf": "abc123csrf",
        "processLogin": "Login",
    }


def test_login_form_missing() -> None:
    assert parser.login_form(fixture("loans.html"), "https://x", "helmet") is None


def test_login_page_and_errors() -> None:
    assert parser.is_login_page(fixture("login.html"))
    assert not parser.is_login_page(fixture("loans.html"))
    assert parser.page_error(fixture("login.html")) is None

    invalid = parser.page_error(fixture("login_invalid.html"))
    assert invalid == "Invalid Patron Login"
    assert parser.is_invalid_login(invalid)

    unavailable = parser.page_error(fixture("login_unavailable.html"))
    assert unavailable is not None
    assert not parser.is_invalid_login(unavailable)
    assert not parser.is_invalid_login("Invalid CSRF token")


def test_loans() -> None:
    loans = parser.parse_loans(fixture("loans.html"))

    assert [loan.title for loan in loans] == ["Muumipeikko ja pyrstötähti", "Tuntematon sotilas", "Ronja Ryövärintytär"]
    first, second, third = loans
    assert first.due_date == date(2026, 10, 22)
    assert first.renewals_remaining == 4
    assert first.renew_id == "i1001|b2001"
    assert first.renewable

    assert second.due_date == date(2026, 10, 9)
    assert second.renewals_remaining == 0
    assert not second.renewable
    assert second.overdue(date(2026, 10, 16))
    assert not first.overdue(date(2026, 10, 16))

    assert third.renewals_remaining is None
    assert third.renewable


def test_no_loans() -> None:
    assert parser.parse_loans(fixture("loans_empty.html")) == []


def test_holds() -> None:
    available, in_transit, waiting = parser.parse_holds(fixture("holds.html"))

    assert available.title == "Harry Potter ja viisasten kivi"
    assert available.status is HoldStatus.AVAILABLE
    assert available.pickup_location == "Oodi"
    assert available.pickup_by == date(2026, 10, 23)
    assert available.pickup_number == "42"
    assert available.expires == date(2027, 4, 1)

    assert in_transit.status is HoldStatus.IN_TRANSIT
    assert in_transit.pickup_location == "Kallio"
    assert in_transit.pickup_by is None

    assert waiting.status is HoldStatus.WAITING
    assert waiting.queue_position == 7
    assert waiting.frozen
    assert not available.frozen


def test_dates() -> None:
    assert parser.parse_date("Due date: 1.2.2027") == date(2027, 2, 1)
    assert parser.parse_date("31.2.2027") is None
    assert parser.parse_date(None) is None


def test_next_page() -> None:
    page = '<ul class="pagination"><li><a href="/MyResearch/CheckedOut?page=2&amp;lng=en-gb">2</a></li></ul>'
    assert parser.has_next_page(page, 1)
    assert not parser.has_next_page(page, 2)


def test_listed_loan_count() -> None:
    assert parser.listed_loan_count(fixture("loans.html")) == 3
    assert parser.listed_loan_count(fixture("loans_empty.html")) is None
