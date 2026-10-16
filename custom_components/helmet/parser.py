"""Reads the Helmet web library's pages.

Finna has no public API for a patron's own loans, so these read the same pages
a browser shows. The pages are asked for in English (see const.LANGUAGE), and
the parser looks for Finna's English labels together with the page structure
of Finna's templates (NDL-VuFind2, theme finna2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
RENEWALS_REMAINING = re.compile(r"Renewals remaining:\s*(\d+)", re.IGNORECASE)
RENEWED_OF = re.compile(r"Renewed\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)
QUEUE_POSITION = re.compile(r"Queue Position:\s*(\d+)", re.IGNORECASE)
PICKUP_NUMBER = re.compile(r"Pickup number:\s*(\S+)", re.IGNORECASE)
EXPIRES = re.compile(r"Expires:\s*(\d{1,2}\.\d{1,2}\.\d{4})", re.IGNORECASE)
CHECKED_OUT = re.compile(r"Checked Out:\s*(\d{1,2}\.\d{1,2}\.\d{4})", re.IGNORECASE)
RENEW_SUCCESS = "renewal successful"
RENEW_FAIL = "could not be renewed"
LISTED_COUNT = re.compile(r"Checked Out Items\s*\((\d+)\)", re.IGNORECASE)
INVALID_LOGIN = re.compile(r"invalid|incorrect|wrong|virheellinen|väärä", re.IGNORECASE)


class HoldStatus(StrEnum):
    """Where a hold is."""

    AVAILABLE = "available"
    IN_TRANSIT = "in_transit"
    WAITING = "waiting"


@dataclass(frozen=True)
class Loan:
    """One checked out item."""

    title: str
    due_date: date | None
    renewals_remaining: int | None = None
    # The id Finna's renewal form takes for the item, when it can be renewed.
    renew_id: str | None = None
    author: str | None = None
    format: str | None = None
    year: str | None = None
    issue: str | None = None
    barcode: str | None = None
    checkout_date: date | None = None
    borrowing_location: str | None = None
    # What the library says about the item, such as that someone has reserved it.
    notices: tuple[str, ...] = ()
    # On the page that answers a renewal: whether this item was renewed, and why not.
    renewed: bool | None = None
    renew_message: str | None = None

    @property
    def renewable(self) -> bool:
        return self.renew_id is not None

    def overdue(self, today: date) -> bool:
        return self.due_date is not None and self.due_date < today


@dataclass(frozen=True)
class Hold:
    """One hold (varaus)."""

    title: str
    status: HoldStatus
    queue_position: int | None = None
    pickup_location: str | None = None
    # The last day an available hold can be picked up.
    pickup_by: date | None = None
    pickup_number: str | None = None
    expires: date | None = None
    frozen: bool = False


@dataclass(frozen=True)
class Account:
    """Everything the integration reads from one library card."""

    loans: list[Loan] = field(default_factory=list)
    holds: list[Hold] = field(default_factory=list)
    # Unpaid fees in cents, or None when the library didn't tell.
    fines_cents: int | None = None


@dataclass(frozen=True)
class LoginForm:
    """The login form of a page, ready to be filled in."""

    action: str
    fields: dict[str, str]


def parse_date(text: str | None) -> date | None:
    """Reads the first d.m.yyyy date in the text."""
    if not text:
        return None
    match = DATE.search(text)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def login_form(page: str, page_url: str, target_hint: str) -> LoginForm | None:
    """Finds the library card login form, with its hidden fields filled in as the page has them."""
    soup = BeautifulSoup(page, "html.parser")
    for form in soup.find_all("form"):
        if not form.find("input", attrs={"name": "password"}):
            continue
        fields: dict[str, str] = {}
        for element in form.find_all("input"):
            name = element.get("name")
            kind = (element.get("type") or "text").lower()
            if not name or kind in ("checkbox", "radio", "submit", "button", "image"):
                continue
            fields[name] = element.get("value") or ""
        target = form.find("select", attrs={"name": "target"})
        if isinstance(target, Tag):
            fields["target"] = _login_target(target, target_hint)
        # The form is sent as if its login button was pressed.
        fields["processLogin"] = "Login"
        action = urljoin(page_url, form.get("action") or page_url)
        return LoginForm(action, fields)
    return None


def _login_target(select: Tag, hint: str) -> str:
    options = [option for option in select.find_all("option") if option.get("value")]
    for option in options:
        if hint in str(option["value"]).lower():
            return str(option["value"])
    for option in options:
        if option.has_attr("selected"):
            return str(option["value"])
    return str(options[0]["value"]) if options else ""


def is_login_page(page: str) -> bool:
    """Tells whether the page asks to log in, as Finna's account pages do once the session has ended."""
    soup = BeautifulSoup(page, "html.parser")
    return soup.find("input", attrs={"type": "password"}) is not None


def page_error(page: str) -> str | None:
    """The error message a page shows, if any."""
    soup = BeautifulSoup(page, "html.parser")
    for alert in soup.select(".alert-danger, .flash-message.alert-danger, .alert.alert-error"):
        text = alert.get_text(" ", strip=True)
        if text:
            return text
    return None


def is_invalid_login(message: str | None) -> bool:
    """Tells whether a login error means a wrong card number or PIN, rather than a fault in the service."""
    if not message or "csrf" in message.lower():
        # A form that had gone stale says "invalid" too, but the card is fine.
        return False
    return INVALID_LOGIN.search(message) is not None


def parse_loans(page: str) -> list[Loan]:
    """Reads the checked out items of a loans page."""
    soup = BeautifulSoup(page, "html.parser")
    loans: list[Loan] = []
    for row in soup.select("tr.myresearch-row"):
        title = _title(row)
        if title is None:
            # The table's hidden header row.
            continue
        text = row.get_text(" ", strip=True)
        due_date = None
        for strong in row.find_all("strong"):
            label = strong.get_text(" ", strip=True)
            if label.lower().startswith("due date"):
                due_date = parse_date(label)
                break
        renew_input = row.find("input", attrs={"name": "renewSelectedIDS[]"})
        renew_id = renew_input.get("value") if isinstance(renew_input, Tag) else None
        checkout = CHECKED_OUT.search(text)
        renewed, renew_message, notices = _loan_alerts(row)
        loans.append(
            Loan(
                title=title,
                due_date=due_date,
                renewals_remaining=_renewals_remaining(text),
                renew_id=renew_id or None,
                author=_author(row),
                format=_format(row),
                year=_labelled(row, "Year of Publication"),
                issue=_labelled(row, "Issue"),
                barcode=_labelled(row, "Barcode"),
                checkout_date=parse_date(checkout.group(1)) if checkout else None,
                borrowing_location=_labelled(row, "Borrowing Location"),
                notices=notices,
                renewed=renewed,
                renew_message=renew_message,
            )
        )
    return loans


def _loan_alerts(row: Tag) -> tuple[bool | None, str | None, tuple[str, ...]]:
    """Splits a loan's messages into the result of a renewal and the other notices."""
    renewed: bool | None = None
    message: str | None = None
    notices: list[str] = []
    for alert in row.select(".alert"):
        text = alert.get_text(" ", strip=True)
        lowered = text.lower()
        if RENEW_SUCCESS in lowered:
            renewed = True
        elif RENEW_FAIL in lowered:
            renewed = False
            # "This item could not be renewed: <reason>"
            _, _, reason = text.partition(":")
            message = reason.strip() or None
        elif text:
            notices.append(text)
    return renewed, message, tuple(notices)


def _labelled(row: Tag, label: str) -> str | None:
    """The text after a bold label such as "Borrowing Location:", up to the next line break."""
    for strong in row.find_all("strong"):
        if strong.get_text(" ", strip=True).rstrip(":").strip().lower() != label.lower():
            continue
        parts: list[str] = []
        for sibling in strong.next_siblings:
            if isinstance(sibling, Tag) and sibling.name in ("br", "strong", "div"):
                break
            parts.append(sibling.get_text(" ", strip=True) if isinstance(sibling, Tag) else str(sibling))
        value = " ".join(part.strip() for part in parts if part.strip())
        return value or None
    return None


def _author(row: Tag) -> str | None:
    metadata = row.select_one(".record-core-metadata")
    if metadata is None:
        return None
    for link in metadata.find_all("a", href=True):
        if "author" in str(link["href"]).lower():
            return link.get_text(" ", strip=True) or None
    return None


def _format(row: Tag) -> str | None:
    metadata = row.select_one(".record-core-metadata")
    if metadata is None:
        return None
    element = metadata.select_one(".format, [class*='format-']")
    if element is None:
        return None
    return element.get_text(" ", strip=True) or None


@dataclass(frozen=True)
class RenewForm:
    """The renewal form of a loans page."""

    action: str
    csrf: str


def renew_form(page: str, page_url: str) -> RenewForm | None:
    """The loans page's renewal form, or None when nothing on the page can be renewed."""
    soup = BeautifulSoup(page, "html.parser")
    form = soup.find("form", attrs={"id": "renewals"}) or soup.find("form", attrs={"name": "renewals"})
    if not isinstance(form, Tag):
        return None
    csrf = form.find("input", attrs={"name": "csrf"})
    action = urljoin(page_url, str(form.get("action") or page_url))
    return RenewForm(action, str(csrf.get("value") or "") if isinstance(csrf, Tag) else "")


def page_messages(page: str) -> list[str]:
    """The messages at the top of a page, such as "2 item(s) renewed of total 5 checked out items"."""
    soup = BeautifulSoup(page, "html.parser")
    messages = []
    for alert in soup.select(".flash-message, .messages .alert"):
        text = alert.get_text(" ", strip=True)
        if text and text not in messages:
            messages.append(text)
    return messages


def _renewals_remaining(text: str) -> int | None:
    if match := RENEWALS_REMAINING.search(text):
        return int(match.group(1))
    if match := RENEWED_OF.search(text):
        return max(int(match.group(2)) - int(match.group(1)), 0)
    return None


def parse_holds(page: str) -> list[Hold]:
    """Reads the holds of a holds page."""
    soup = BeautifulSoup(page, "html.parser")
    holds: list[Hold] = []
    for row in soup.select("tr.myresearch-row"):
        title = _title(row)
        if title is None:
            continue
        info = row.select_one(".holds-status-information") or row
        text = info.get_text(" ", strip=True)

        status = HoldStatus.WAITING
        pickup_by = None
        pickup_number = None
        available = next(
            (alert for alert in info.select(".alert-success") if "available" in alert.get_text().lower()),
            None,
        )
        if available is not None:
            status = HoldStatus.AVAILABLE
            available_text = available.get_text(" ", strip=True)
            pickup_by = parse_date(available_text)
            if match := PICKUP_NUMBER.search(available_text):
                pickup_number = match.group(1)
        elif "in transit" in text.lower():
            status = HoldStatus.IN_TRANSIT

        queue = QUEUE_POSITION.search(text)
        expires = EXPIRES.search(text)
        holds.append(
            Hold(
                title=title,
                status=status,
                queue_position=int(queue.group(1)) if queue else None,
                pickup_location=_pickup_location(info),
                pickup_by=pickup_by,
                pickup_number=pickup_number,
                expires=parse_date(expires.group(1)) if expires else None,
                frozen="frozen" in text.lower(),
            )
        )
    return holds


def _pickup_location(info: Tag) -> str | None:
    container = info.select_one(".pickup-location-container")
    if container is None:
        return None
    label = container.find("strong")
    text = container.get_text(" ", strip=True)
    if label is not None:
        text = text.removeprefix(label.get_text(" ", strip=True)).strip()
    return text or None


def _title(row: Tag) -> str | None:
    heading = row.select_one(".record-title")
    if heading is None:
        return None
    title = heading.get_text(" ", strip=True).strip(" /:")
    return title or None


def listed_loan_count(page: str) -> int | None:
    """The number of loans the loans page says it has in its heading, to check the parser against."""
    soup = BeautifulSoup(page, "html.parser")
    heading = soup.select_one("#useraccount-content-heading") or soup
    match = LISTED_COUNT.search(heading.get_text(" ", strip=True))
    return int(match.group(1)) if match else None


def has_next_page(page: str, current: int) -> bool:
    """Tells whether a paged list goes on after the current page."""
    soup = BeautifulSoup(page, "html.parser")
    for link in soup.select(".pagination a[href]"):
        query = parse_qs(urlparse(str(link["href"])).query)
        if str(current + 1) in query.get("page", []):
            return True
    return False
