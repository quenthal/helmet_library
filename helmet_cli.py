#!/usr/bin/env python3
"""Tries the Helmet integration's login and parsing from the console, without Home Assistant.

Uses the same code as the integration (custom_components/helmet/api.py and parser.py).

    sudo apt install python3-aiohttp python3-bs4
    python3 helmet_cli.py
    python3 helmet_cli.py --dump pages      # also saves the pages it read, for debugging

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
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

# Loads the integration's modules without its __init__.py, which needs Home Assistant.
_PACKAGE = types.ModuleType("helmet_core")
_PACKAGE.__path__ = [str(Path(__file__).resolve().parent / "custom_components" / "helmet")]
sys.modules["helmet_core"] = _PACKAGE

from helmet_core.api import HelmetAuthError, HelmetClient, HelmetError  # noqa: E402
from helmet_core.const import BASE_URL  # noqa: E402
from helmet_core.parser import Account, HoldStatus  # noqa: E402


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


def print_account(account: Account) -> None:
    today = date.today()
    print(f"\nLainat ({len(account.loans)}):")
    for loan in sorted(account.loans, key=lambda loan: (loan.due_date or date.max, loan.title)):
        due = loan.due_date.strftime("%d.%m.%Y") if loan.due_date else "?"
        notes = []
        if loan.overdue(today):
            notes.append("MYÖHÄSSÄ")
        notes.append("uusittavissa" if loan.renewable else "ei uusittavissa")
        if loan.renewals_remaining is not None:
            notes.append(f"uusintoja jäljellä {loan.renewals_remaining}")
        print(f"  {due}  {loan.title}  ({', '.join(notes)})")

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


def as_json(account: Account) -> str:
    def default(value: Any) -> Any:
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    return json.dumps(asdict(account), default=default, indent=2, ensure_ascii=False)


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
        except HelmetAuthError as err:
            print(f"Kirjasto ei hyväksynyt kortin numeroa ja PIN-koodia: {err}", file=sys.stderr)
            return 2
        except HelmetError as err:
            print(f"Virhe: {err}", file=sys.stderr)
            return 1

    if args.json:
        print(as_json(account))
    else:
        print_account(account)
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
