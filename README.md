# Helmet libraries for Home Assistant

Home Assistant integration that follows the loans, holds and fees of a Helmet library card
(the libraries of Helsinki, Espoo, Vantaa and Kauniainen).

Each library card is its own entry, so a family adds the integration once for each member's card.

This is an unofficial integration. Helmet has no public API for a patron's own account, so the integration reads
the same pages of the [Helmet web library](https://helmet.finna.fi) that a browser shows.

## What you get

For each card, a device named **Helmet _name_** with:

| Entity | State | Attributes |
| --- | --- | --- |
| Loans | Number of loans | Every loan with its title, due date, whether it is overdue, whether it can be renewed and the renewals left |
| Overdue loans | Number of overdue loans | Their titles |
| Next due date | The earliest due date (a date sensor) | The titles due that day, and whether they can all be renewed |
| Holds | Number of holds | Every hold with its status (`available`, `in_transit`, `waiting`), queue position, pickup location, last pickup day and pickup number |
| Holds ready for pickup | Number of holds waiting at the library | The same details for those holds |
| Fees | Unpaid fees in euros | |
| Due dates (calendar) | | An all-day event for each due date, and for the last pickup day of each hold ready for pickup |

The account is read once an hour.

### Automation ideas

- A reminder two days before the next due date, if the loans can't all be renewed
- A notification when a hold is ready for pickup, with its pickup number
- The family's due dates on one calendar card on the dashboard

## Installation

### With HACS

1. Add this repository to HACS custom repositories with type **Integration**
2. Search for Helmet libraries in HACS and download it
3. Restart Home Assistant
4. Add the integration in Settings › Devices & services

### Manual

Copy the `custom_components/helmet` folder to your Home Assistant installation's `config/custom_components` folder
and restart Home Assistant.

## Adding cards

Add the integration, and give:

- **Name**: whose card it is, such as Liisa. It names the card's device and entities.
- **Library card number** and **PIN**: the ones you use to log in to the Helmet web library.

For another family member, add the integration again with their card.

If the PIN changes, Home Assistant asks for the new one on the integration page.

## Security

The integration stores the card number and PIN in Home Assistant's configuration storage, like any integration's
credentials, and in every backup of it. With them, anyone can log in to the card's account in the web library and
see its loans, holds and contact details, and place or cancel holds. They don't give access to anything outside the
library.

The integration itself only reads the account. It doesn't renew loans or change holds.

## Limits

- The integration reads the web library's pages, so a change in them can stop it until it is updated. If the
  Loans sensor stops matching the web library, the log has a warning about it.
- Helmet is changing its library system in 2026–2027. The web library stays on Finna, which should keep the
  integration working, but a break around the change is possible.

## Development

```
pip install pytest-homeassistant-custom-component beautifulsoup4 ruff
pytest
ruff check . && ruff format --check .
```

The parser follows the templates of Finna ([NDL-VuFind2](https://github.com/NatLibFi/NDL-VuFind2), theme `finna2`),
and the test pages in `tests/fixtures` are built after them.
