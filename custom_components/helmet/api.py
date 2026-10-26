"""Logs in to the Helmet web library with a library card, and reads the card's loans, holds and fees.

Each card has a session of its own, since Finna keeps the login in a session
cookie. When the session has ended, the client logs in again with the card
number and PIN.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, TypeVar

import aiohttp

from . import parser
from .const import (
    AJAX_PATH,
    BASE_URL,
    HOLDS_PATH,
    LANGUAGE,
    LOANS_PATH,
    LOGIN_PATH,
    LOGIN_TARGET_HINT,
    MAX_PAGES,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from .parser import Account, Hold, Loan

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")

TIMEOUT = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.8,fi;q=0.5"}


class HelmetError(Exception):
    """The library couldn't be reached, or answered in a way the integration doesn't understand."""


class HelmetAuthError(HelmetError):
    """The library didn't accept the card number and PIN."""


@dataclass(frozen=True)
class RenewOutcome:
    """What happened to one loan that was renewed."""

    title: str
    renewed: bool
    # The due date after the renewal: the new one, or the old one when the renewal failed.
    due_date: date | None
    # Why the library didn't renew it, when it said.
    message: str | None = None


class HelmetClient:
    """One library card's session in the Helmet web library."""

    def __init__(self, session: aiohttp.ClientSession, card_number: str, pin: str, base_url: str = BASE_URL) -> None:
        self._session = session
        self._card_number = card_number
        self._pin = pin
        self._base_url = base_url.rstrip("/")
        self._logged_in = False
        # Updates of one card don't overlap, so that two logins never race in the same session.
        self._lock = asyncio.Lock()

    async def login(self) -> None:
        """Logs in with the card. Raises HelmetAuthError when the card number or PIN is wrong."""
        login_url = self._url(LOGIN_PATH)
        page = await self._get(login_url)
        form = parser.login_form(page, login_url, LOGIN_TARGET_HINT)
        if form is None:
            raise HelmetError("The library's login page had no login form")

        fields = {**form.fields, "username": self._card_number, "password": self._pin}
        page = await self._post(form.action, fields)
        if parser.is_login_page(page):
            message = parser.page_error(page)
            if message is None or parser.is_invalid_login(message):
                # Finna shows the form again without a message when it refuses the login outright.
                raise HelmetAuthError(message or "The library didn't accept the card number and PIN")
            raise HelmetError(f"The library refused the login: {message}")
        self._logged_in = True

    async def account(self) -> Account:
        """Reads the card's loans, holds and fees, logging in first when needed."""
        return await self._logged_in_call(self._read_account)

    async def renew(self, renew_ids: Iterable[str] | None = None) -> list[RenewOutcome]:
        """Renews the loans with the given renewal ids (Loan.renew_id), or every renewable loan when none are given."""
        ids = None if renew_ids is None else list(dict.fromkeys(renew_ids))
        return await self._logged_in_call(lambda: self._renew(ids))

    async def _logged_in_call(self, call: Callable[[], Awaitable[T]]) -> T:
        """Makes the call in a logged in session, logging in again once if the session has ended."""
        async with self._lock:
            if not self._logged_in:
                await self.login()
            try:
                return await call()
            except _SessionEnded:
                _LOGGER.debug("The library session had ended; logging in again")
                self._logged_in = False
                await self.login()
                try:
                    return await call()
                except _SessionEnded as err:
                    raise HelmetError("The library ended the session right after logging in") from err

    async def _renew(self, ids: list[str] | None) -> list[RenewOutcome]:
        before = await self._loans()
        renewable = {loan.renew_id: loan for loan in before if loan.renew_id}
        if ids is None:
            ids = list(renewable)
        unknown = [renew_id for renew_id in ids if renew_id not in renewable]
        if unknown:
            raise HelmetError(f"Not renewable loans: {', '.join(unknown)}")
        if not ids:
            return []

        url = self._url(LOANS_PATH)
        form = parser.renew_form(await self._account_page(LOANS_PATH), url)
        if form is None:
            raise HelmetError("The loans page had no renewal form")
        data = [("csrf", form.csrf), ("renewSelected", "1")]
        data += [("renewSelectedIDS[]", renew_id) for renew_id in ids]
        answer = await self._post(form.action, data)
        if parser.is_login_page(answer):
            # Nothing was renewed, so the renewal can be sent again once logged in.
            raise _SessionEnded
        _LOGGER.debug("Renewal answered: %s", parser.page_messages(answer))

        # The answering page tells the result of each renewed loan on it. A loan on a later page
        # of a long list is checked by whether its due date moved.
        results = [loan for loan in parser.parse_loans(answer) if loan.renewed is not None]
        after: list[Loan] | None = None
        outcomes = []
        for renew_id in ids:
            loan = renewable[renew_id]
            result = next((row for row in results if _same_item(row, loan)), None)
            if result is not None:
                results.remove(result)
                outcomes.append(RenewOutcome(loan.title, bool(result.renewed), result.due_date, result.renew_message))
                continue
            if after is None:
                try:
                    after = await self._loans()
                except _SessionEnded as err:
                    # Not retried: the renewal has been sent, and sending it again would use another renewal.
                    raise HelmetError("The library ended the session before the renewal could be checked") from err
            now = next((row for row in after if _same_item(row, loan)), None)
            due = now.due_date if now else None
            renewed = due is not None and loan.due_date is not None and due > loan.due_date
            outcomes.append(RenewOutcome(loan.title, renewed, due or loan.due_date))
        return outcomes

    async def _read_account(self) -> Account:
        loans = await self._loans()
        holds = await self._holds()
        fines = await self._fines_cents()
        return Account(loans=loans, holds=holds, fines_cents=fines)

    async def _loans(self) -> list[Loan]:
        loans: list[Loan] = []
        listed = None
        for number in range(1, MAX_PAGES + 1):
            page = await self._account_page(LOANS_PATH, {"page": str(number)} if number > 1 else None)
            if number == 1:
                listed = parser.listed_loan_count(page)
            loans.extend(parser.parse_loans(page))
            if not parser.has_next_page(page, number):
                break
        else:
            _LOGGER.warning("Read only the first %s pages of loans", MAX_PAGES)
        if listed is not None and listed != len(loans):
            # The page has changed in a way the parser doesn't follow.
            _LOGGER.warning("The loans page lists %s loans, but %s of them could be read", listed, len(loans))
        return loans

    async def _holds(self) -> list[Hold]:
        return parser.parse_holds(await self._account_page(HOLDS_PATH))

    async def _fines_cents(self) -> int | None:
        """The total of unpaid fees, from the same JSON call the web library's account menu makes."""
        try:
            body = await self._get_json(self._url(AJAX_PATH), {"method": "getUserFines"})
        except HelmetError as err:
            # Fees are a side detail; the loans and holds are still worth showing without them.
            _LOGGER.debug("Couldn't read the fees: %s", err)
            return None
        data = body.get("data")
        if isinstance(data, dict) and isinstance(data.get("total"), int | float):
            return int(data["total"])
        return None

    async def _account_page(self, path: str, params: dict[str, str] | None = None) -> str:
        page = await self._get(self._url(path), params)
        if parser.is_login_page(page):
            raise _SessionEnded
        return page

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def _get(self, url: str, params: dict[str, str] | None = None) -> str:
        return await self._request("GET", url, params=self._params(params))

    async def _post(self, url: str, data: dict[str, str] | list[tuple[str, str]]) -> str:
        return await self._request("POST", url, params=self._params(None), data=data)

    async def _get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._session.get(
                url, params=self._params(params), headers={**HEADERS, "Accept": "application/json"}, timeout=TIMEOUT
            ) as response:
                if response.status in (401, 403):
                    raise _SessionEnded
                if response.status >= 400:
                    raise HelmetError(f"The library answered {response.status}")
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise HelmetError(f"Couldn't read {url}: {err}") from err
        if not isinstance(body, dict):
            raise HelmetError("The library's answer wasn't an object")
        return body

    async def _request(self, method: str, url: str, **kwargs: Any) -> str:
        try:
            async with self._session.request(method, url, headers=HEADERS, timeout=TIMEOUT, **kwargs) as response:
                if response.status >= 500:
                    raise HelmetError(f"The library answered {response.status}")
                # Finna answers some refused requests with 4xx and the login form; the caller reads the page.
                page = await response.text()
                if response.status == 403 and not parser.is_login_page(page):
                    # The firewall in front of the web library, rather than the library itself.
                    raise HelmetError("The web library refused the request (403 Forbidden)")
                return page
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HelmetError(f"Couldn't reach the library: {err}") from err

    @staticmethod
    def _params(params: dict[str, str] | None) -> dict[str, str]:
        return {**(params or {}), "lng": LANGUAGE}


def _same_item(row: Loan, loan: Loan) -> bool:
    """Whether a row of a later page is the same item as the loan.

    The title alone isn't enough: a family can have several issues of the same magazine.
    """
    if row.renew_id and loan.renew_id:
        return row.renew_id == loan.renew_id
    if row.barcode and loan.barcode:
        return row.barcode == loan.barcode
    return (row.title, row.issue) == (loan.title, loan.issue)


class _SessionEnded(Exception):
    """The library asked to log in again."""
