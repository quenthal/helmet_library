#!/usr/bin/env python3
"""Tries the Helmet integration's login and parsing from the console, without Home Assistant.

Uses the same code as the integration (custom_components/helmet/api.py and parser.py).

    sudo apt install python3-aiohttp python3-bs4
    python3 helmet_cli.py
    python3 helmet_cli.py --dump pages      # also saves the pages it read, for debugging
    python3 helmet_cli.py --renew-all       # renews every loan that can be renewed
    python3 helmet_cli.py --renew 1,3       # renews loans 1 and 3 of the list

The card number and PIN are asked for, or read from HELMET_CARD and HELMET_PIN.
The PIN is never taken as an argument, so that it doesn't end up in the shell history.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
import types
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp

# Loads the integration's modules without its __init__.py, which needs Home Assistant.
_PACKAGE = types.ModuleType("helmet_core")
_PACKAGE.__path__ = [str(Path(__file__).resolve().parent / "custom_components" / "helmet")]
sys.modules["helmet_core"] = _PACKAGE

from helmet_core.api import HelmetAuthError, HelmetClient, HelmetError, RenewOutcome  # noqa: E402
from helmet_core.attention import attention  # noqa: E402
from helmet_core.const import BASE_URL  # noqa: E402
from helmet_core.parser import Account, HoldStatus, Loan  # noqa: E402


class DumpingClient(HelmetClient):
    """Saves every page the library answers with. Only the answers: what is sent, the PIN with it, isn't saved."""

    def __init__(self, *args: Any, dump_dir: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._dump_dir = dump_dir
        self._count = 0

    async def _request(self, method: str, url: str, **kwargs: Any) -> str:
        page = await super()._request(method, url, **kwargs)
        self._save(method, url, page, "html")
        return page

    async def _get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        body = await super()._get_json(url, params)
        self._save("GET", f"{url}-{params.get('method', '')}", json.dumps(body, indent=2, ensure_ascii=False), "json")
        return body

    def _save(self, method: str, url: str, content: str, extension: str) -> None:
        self._count += 1
        name = urlparse(url).path.strip("/").replace("/", "_") or "root"
        path = self._dump_dir / f"{self._count:02d}_{method}_{name}.{extension}"
        path.write_text(content, encoding="utf-8")


def library_today() -> date:
    """Today in Helsinki, where the library's due dates are, whatever the computer's own time zone."""
    return datetime.now(ZoneInfo("Europe/Helsinki")).date()


def fmt(day: date | None) -> str:
    return day.strftime("%d.%m.%Y") if day else "?"


def sorted_loans(loans: list[Loan]) -> list[Loan]:
    """The loans in the order they are listed and numbered: the earliest due date first."""
    return sorted(loans, key=lambda loan: (loan.due_date or date.max, loan.title))


def print_account(account: Account) -> None:
    today = library_today()
    print(f"\nLainat ({len(account.loans)}):")
    for number, loan in enumerate(sorted_loans(account.loans), start=1):
        late = "  MYÖHÄSSÄ" if loan.overdue(today) else ""
        print(f"{number:3}. {fmt(loan.due_date)}  {loan.title}{late}")
        details = [part for part in (loan.author, loan.format, loan.year, loan.issue) if part]
        if loan.checkout_date or loan.borrowing_location:
            details.append(
                " ".join(
                    filter(
                        None,
                        ("lainattu", fmt(loan.checkout_date) if loan.checkout_date else None, loan.borrowing_location),
                    )
                )
            )
        renew = "uusittavissa" if loan.renewable else "ei uusittavissa"
        if loan.renewals_remaining is not None:
            renew += f", uusintoja jäljellä {loan.renewals_remaining}"
        details.append(renew)
        print(f"       {' · '.join(details)}")
        if loan.barcode:
            print(f"       viivakoodi {loan.barcode}")
        for notice in loan.notices:
            print(f"       ! {notice}")

    print(f"\nVaraukset ({len(account.holds)}):")
    for hold in account.holds:
        if hold.status is HoldStatus.AVAILABLE:
            by = hold.pickup_by.strftime("%d.%m.%Y") if hold.pickup_by else "?"
            status = f"NOUDETTAVISSA {by} asti"
            if hold.pickup_number:
                status += f", noutonumero {hold.pickup_number}"
        elif hold.status is HoldStatus.IN_TRANSIT:
            status = "matkalla noutopaikkaan"
        else:
            status = f"jonossa {hold.queue_position}." if hold.queue_position else "odottaa"
        if hold.frozen:
            status += ", jäädytetty"
        where = f" @ {hold.pickup_location}" if hold.pickup_location else ""
        print(f"  {hold.title}{where}  ({status})")

    fines = "ei tiedossa" if account.fines_cents is None else f"{account.fines_cents / 100:.2f} €"
    print(f"\nMaksut: {fines}")

    found = attention(account, today)
    if not found.needed:
        print("\nTarvitsee huomiota: ei, kaikki kunnossa")
        return
    print("\nTarvitsee huomiota:")
    for title in found.overdue:
        print(f"  myöhässä: {title}")
    for title in found.due_soon:
        print(f"  erääntyy pian, ei uusittavissa: {title}")
    for title in found.holds_ready:
        print(f"  noudettavissa: {title}")
    if found.fees_cents:
        print(f"  maksuja {found.fees_cents / 100:.2f} €")


def as_json(account: Account) -> str:
    def default(value: Any) -> Any:
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    return json.dumps(asdict(account), default=default, indent=2, ensure_ascii=False)


def chosen_loans(account: Account, numbers: str) -> list[Loan]:
    """The loans with the given numbers of the list, such as "1,3" or "2-4"."""
    loans = sorted_loans(account.loans)
    chosen: list[Loan] = []
    for part in numbers.replace(" ", "").split(","):
        if not part:
            continue
        first, _, last = part.partition("-")
        for number in range(int(first), int(last or first) + 1):
            if not 1 <= number <= len(loans):
                raise ValueError(f"Lainaa {number} ei ole listassa (lainoja on {len(loans)})")
            if loans[number - 1] not in chosen:
                chosen.append(loans[number - 1])
    return chosen


def print_outcomes(outcomes: list[RenewOutcome]) -> None:
    print("\nUusinta:")
    for outcome in outcomes:
        if outcome.renewed:
            print(f"  OK     {outcome.title}: uusi eräpäivä {fmt(outcome.due_date)}")
        else:
            reason = f": {outcome.message}" if outcome.message else ""
            print(f"  EI     {outcome.title}{reason} (eräpäivä {fmt(outcome.due_date)})")


def confirm(question: str) -> bool:
    return input(f"{question} [k/E] ").strip().lower() in ("k", "kyllä", "y", "yes")


async def run(args: argparse.Namespace, card: str, pin: str) -> int:
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
        if args.dump:
            dump_dir = Path(args.dump)
            dump_dir.mkdir(parents=True, exist_ok=True)
            client: HelmetClient = DumpingClient(session, card, pin, args.base_url, dump_dir=dump_dir)
        else:
            client = HelmetClient(session, card, pin, args.base_url)
        try:
            account = await client.account()
            if args.json:
                print(as_json(account))
            else:
                print_account(account)

            if args.renew_all or args.renew:
                loans = account.loans if args.renew_all else chosen_loans(account, args.renew)
                renewable = [loan for loan in loans if loan.renewable]
                for loan in loans:
                    if not loan.renewable:
                        print(f"Ei uusittavissa, ohitetaan: {loan.title}")
                if not renewable:
                    print("\nEi uusittavia lainoja.")
                elif args.yes or confirm(f"\nUusitaanko {len(renewable)} lainaa?"):
                    print_outcomes(await client.renew([loan.renew_id for loan in renewable if loan.renew_id]))
        except HelmetAuthError as err:
            print(f"Kirjasto ei hyväksynyt kortin numeroa ja PIN-koodia: {err}", file=sys.stderr)
            return 2
        except (HelmetError, ValueError) as err:
            print(f"Virhe: {err}", file=sys.stderr)
            return 1

    if args.dump:
        print(
            f"\nSivut tallennettiin kansioon {args.dump}. Niissä voi olla nimesi ja muita henkilötietoja,"
            " joten poista ne ennen kuin lähetät sivuja kenellekään.",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    arguments = argparse.ArgumentParser(description="Kokeile Helmet-integraation kirjautumista ja sivujen lukemista.")
    arguments.add_argument("--dump", metavar="KANSIO", help="tallenna kirjaston vastaukset tähän kansioon")
    arguments.add_argument("--json", action="store_true", help="tulosta JSONina")
    renew = arguments.add_mutually_exclusive_group()
    renew.add_argument("--renew-all", action="store_true", help="uusi kaikki uusittavissa olevat lainat")
    renew.add_argument("--renew", metavar="NUMEROT", help="uusi listan lainat, esim. 1,3 tai 2-4")
    arguments.add_argument("--yes", action="store_true", help="älä kysy vahvistusta ennen uusimista")
    arguments.add_argument("-v", "--verbose", action="store_true", help="näytä debug-loki")
    arguments.add_argument("--base-url", default=BASE_URL, help=argparse.SUPPRESS)
    args = arguments.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    card = os.environ.get("HELMET_CARD") or input("Kirjastokortin numero: ")
    pin = os.environ.get("HELMET_PIN") or getpass.getpass("PIN-koodi: ")
    card = "".join(card.split()).upper()
    return asyncio.run(run(args, card, pin))


if __name__ == "__main__":
    sys.exit(main())
