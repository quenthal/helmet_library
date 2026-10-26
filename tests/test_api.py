"""Tests of logging in and reading a card's account, against a pretend web library."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from urllib.parse import urlparse

import aiohttp
import pytest

from custom_components.helmet.api import HelmetAuthError, HelmetClient, HelmetError, RenewOutcome
from custom_components.helmet.const import USER_AGENT

from . import fixture

CARD = "12345678"
PIN = "1234"


class FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def json(self, content_type: str | None = None) -> Any:
        return json.loads(self._body)


class FakeLibrary:
    """Answers like helmet.finna.fi: the account pages show the login form until the session has logged in."""

    def __init__(self) -> None:
        self.logged_in = False
        self.logins: list[dict[str, str]] = []
        self.login_page = "login.html"
        self.loan_pages = {"1": fixture("loans.html")}
        self.fail_with: int | None = None
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.renewals: list[Any] = []
        self.renewed = False

    @asynccontextmanager
    async def request(self, method: str, url: str, **kwargs: Any):
        yield self._answer(method, url, kwargs)

    def get(self, url: str, **kwargs: Any):
        return self.request("GET", url, **kwargs)

    async def close(self) -> None:
        pass

    def _answer(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        path = urlparse(url).path
        params = kwargs.get("params") or {}
        self.requests.append((method, path, params))
        assert params.get("lng") == "en-gb"
        if self.fail_with:
            return FakeResponse(self.fail_with, "error")
        if method == "GET" and path == "/MyResearch/UserLogin":
            return FakeResponse(200, fixture(self.login_page))
        if method == "POST" and path == "/MyResearch/Home":
            data = kwargs["data"]
            self.logins.append(data)
            if data.get("csrf") == "abc123csrf" and data["username"] == CARD and data["password"] == PIN:
                self.logged_in = True
                return FakeResponse(200, fixture("loans_empty.html"))
            return FakeResponse(200, fixture("login_invalid.html"))
        if not self.logged_in:
            return FakeResponse(200, fixture("login.html"))
        if path == "/MyResearch/CheckedOut" and method == "POST":
            self.renewals.append(kwargs["data"])
            self.renewed = True
            return FakeResponse(200, fixture("loans_renewed.html"))
        if path == "/MyResearch/CheckedOut":
            if self.renewed:
                return FakeResponse(200, fixture("loans_renewed.html").replace('<div class="messages">', "<div>"))
            return FakeResponse(200, self.loan_pages[params.get("page", "1")])
        if path == "/Holds/List":
            return FakeResponse(200, fixture("holds.html"))
        if path == "/AJAX/JSON" and params.get("method") == "getUserFines":
            return FakeResponse(200, json.dumps({"data": {"total": 250, "display": "2,50 €"}}))
        return FakeResponse(404, "not found")


def client(library: FakeLibrary, pin: str = PIN) -> HelmetClient:
    return HelmetClient(library, CARD, pin)  # type: ignore[arg-type]


async def test_reads_account_after_logging_in() -> None:
    library = FakeLibrary()
    account = await client(library).account()

    assert len(library.logins) == 1
    assert library.logins[0]["target"] == "helmet"
    assert len(account.loans) == 3
    assert len(account.holds) == 3
    assert account.fines_cents == 250


async def test_logs_in_again_when_session_ended() -> None:
    library = FakeLibrary()
    helmet = client(library)
    await helmet.account()

    library.logged_in = False
    account = await helmet.account()

    assert len(library.logins) == 2
    assert len(account.loans) == 3


async def test_wrong_pin() -> None:
    with pytest.raises(HelmetAuthError):
        await client(FakeLibrary(), pin="0000").login()


async def test_library_unavailable_is_not_a_wrong_pin() -> None:
    class Unavailable(FakeLibrary):
        def _answer(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
            if method == "POST":
                return FakeResponse(200, fixture("login_unavailable.html"))
            return super()._answer(method, url, kwargs)

    with pytest.raises(HelmetError) as raised:
        await client(Unavailable()).login()
    assert not isinstance(raised.value, HelmetAuthError)


async def test_server_error() -> None:
    library = FakeLibrary()
    library.fail_with = 503
    with pytest.raises(HelmetError):
        await client(library).account()


async def test_refused_by_the_firewall() -> None:
    """A 403 without a login form tells that the request was refused, not that the page had no form."""
    library = FakeLibrary()
    library.fail_with = 403
    with pytest.raises(HelmetError, match="403"):
        await client(library).login()


def test_user_agent_has_no_github_address() -> None:
    # The web library's firewall refuses the login page to it, and the integration can't log in at all.
    assert "github" not in USER_AGENT.lower()


async def test_reads_every_page_of_loans() -> None:
    library = FakeLibrary()
    first = fixture("loans.html").replace(
        "</table>", '</table><ul class="pagination"><li><a href="?page=2">2</a></li></ul>'
    )
    library.loan_pages = {"1": first, "2": fixture("loans.html")}

    account = await client(library).account()

    assert len(account.loans) == 6


async def test_fines_unknown_when_the_call_fails() -> None:
    class NoFines(FakeLibrary):
        def _answer(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
            if urlparse(url).path == "/AJAX/JSON":
                raise aiohttp.ClientError("boom")
            return super()._answer(method, url, kwargs)

    account = await client(NoFines()).account()
    assert account.fines_cents is None
    assert len(account.loans) == 3


async def test_renew_all() -> None:
    library = FakeLibrary()
    outcomes = await client(library).renew()

    assert library.renewals == [
        [
            ("csrf", "abc"),
            ("renewSelected", "1"),
            ("renewSelectedIDS[]", "i1001|b2001"),
            ("renewSelectedIDS[]", "i1003|b2003"),
        ]
    ]
    assert outcomes == [
        RenewOutcome("Muumipeikko ja pyrstötähti", True, date(2026, 11, 12)),
        RenewOutcome("Ronja Ryövärintytär", False, date(2026, 10, 22), "Item has holds"),
    ]


async def test_renew_selected() -> None:
    library = FakeLibrary()
    outcomes = await client(library).renew(["i1001|b2001"])

    assert [field for field in library.renewals[0] if field[0] == "renewSelectedIDS[]"] == [
        ("renewSelectedIDS[]", "i1001|b2001")
    ]
    assert outcomes == [RenewOutcome("Muumipeikko ja pyrstötähti", True, date(2026, 11, 12))]


async def test_renew_checks_loans_missing_from_the_answer() -> None:
    class QuietLibrary(FakeLibrary):
        """Answers the renewal without per-loan results, as for loans on a later page."""

        def _answer(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
            answer = super()._answer(method, url, kwargs)
            if method == "POST" and urlparse(url).path == "/MyResearch/CheckedOut":
                return FakeResponse(200, fixture("loans_empty.html"))
            return answer

    outcomes = await client(QuietLibrary()).renew()

    # The due dates read after the renewal tell: the first moved, the third didn't.
    assert [(outcome.title, outcome.renewed) for outcome in outcomes] == [
        ("Muumipeikko ja pyrstötähti", True),
        ("Ronja Ryövärintytär", False),
    ]


class SameTitles(FakeLibrary):
    """Two issues of the same magazine: the first and the third loan have the same title."""

    def _answer(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        answer = super()._answer(method, url, kwargs)
        answer._body = answer._body.replace("Ronja Ryövärintytär", "Muumipeikko ja pyrstötähti")
        return answer


async def test_renew_tells_apart_loans_with_the_same_title() -> None:
    # Only the third is renewed, and it fails; the first, renewed earlier, shows its success on the same page.
    outcomes = await client(SameTitles()).renew(["i1003|b2003"])

    assert outcomes == [RenewOutcome("Muumipeikko ja pyrstötähti", False, date(2026, 10, 22), "Item has holds")]


async def test_renew_checks_the_right_one_of_loans_with_the_same_title() -> None:
    class Quiet(SameTitles):
        def _answer(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
            answer = super()._answer(method, url, kwargs)
            if method == "POST" and urlparse(url).path == "/MyResearch/CheckedOut":
                return FakeResponse(200, fixture("loans_empty.html"))
            return answer

    # Afterwards the first one's due date has moved, but the third one's hasn't.
    outcomes = await client(Quiet()).renew(["i1003|b2003"])

    assert [(outcome.renewed, outcome.due_date) for outcome in outcomes] == [(False, date(2026, 10, 22))]


async def test_renew_rejects_loans_that_cant_be_renewed() -> None:
    library = FakeLibrary()
    with pytest.raises(HelmetError):
        await client(library).renew(["nope"])
    assert library.renewals == []


async def test_renew_nothing_renewable() -> None:
    library = FakeLibrary()
    library.loan_pages = {"1": fixture("loans_empty.html")}
    assert await client(library).renew() == []
    assert library.renewals == []
