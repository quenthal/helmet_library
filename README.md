# Helmet libraries for Home Assistant

<img src="custom_components/helmet/brand/icon.png" width="96" alt="The Helmet logo" align="right">

Home Assistant integration that follows the loans, holds and fees of a Helmet library card
(the libraries of Helsinki, Espoo, Vantaa and Kauniainen).

Each library card is its own entry, so a family adds the integration once for each member's card.

This is an unofficial integration. Helmet has no public API for a patron's own account, so the integration reads
the same pages of the [Helmet web library](https://helmet.finna.fi) that a browser shows.

## What you get

For each card, a device named **Helmet _name_** with:

| Entity | State | Attributes |
| --- | --- | --- |
| Loans | Number of loans | Every loan with its title, due date, days left, whether it is overdue, whether it can be renewed and the renewals left |
| Overdue loans | Number of overdue loans | Their titles |
| Next due date | The end (23:59) of the earliest due day, as a timestamp, so it shows as "in 3 days" | `date`, `days_left`, the titles due that day, and whether they can all be renewed |
| Holds | Number of holds | Every hold with its status (`available`, `in_transit`, `waiting`), queue position, pickup location, last pickup day and pickup number |
| Holds ready for pickup | Number of holds waiting at the library | The same details for those holds |
| Fees | Unpaid fees in euros | |
| Needs attention (binary sensor) | On when a loan is overdue, a loan that can't be renewed is due today or tomorrow, a hold is ready for pickup, or there are unpaid fees | `reasons`, and the titles for each reason |
| Due dates (calendar) | | An all-day event for each due date, and for the last pickup day of each hold ready for pickup |
| Renew loans due soon (button) | | Renews the loans that can be renewed and are due within 3 days |

The account is read once an hour, and again right before a renewal.

Home Assistant's history keeps only the states of these entities, not their attributes, so that a long list of
loans doesn't fill the database.

### Renewing loans

The **Renew loans due soon** button renews the loans that can be renewed and are due within 3 days, overdue ones
included. Each renewal uses up one of the item's renewals, and renewing long before the due date wastes one, so the
button leaves the other loans as they are. A loan just renewed is no longer due soon, so pressing the button again
doesn't renew it again. The number of days can be changed, from 0 (today) to 14, in the card's **Configure** on the
integration page.

For more choice, the `helmet.renew_loans` action renews a card's loans that match, or all of them:

| Field | |
| --- | --- |
| `config_entry_id` | The card. In the action editor, pick the card from the list, and the editor fills this in. |
| `titles` | Only the loans with any of these in their title (optional) |
| `due_within_days` | Only the loans due within this many days (optional) |

The action responds with the loans that were renewed, with their new due dates, and those that weren't, with the
library's reason. Every renewal, from the button or the action, also fires a `helmet_loans_renewed` event with the
same details and the card's `name`.

Each renewal uses up one of the item's renewals, so an automation should renew only loans that are about to be due.
For example, renewing every morning the loans due within two days, and telling what happened:

```yaml
automation:
  - alias: Renew Liisa's library loans
    triggers:
      - trigger: time
        at: "07:00:00"
    actions:
      - action: helmet.renew_loans
        data:
          config_entry_id: YOUR_CARD_ENTRY_ID
          due_within_days: 2
        response_variable: result
      - if: "{{ result.renewed or result.not_renewed }}"
        then:
          - action: notify.notify
            data:
              title: Kirjasto
              message: >
                {% for loan in result.renewed %}Uusittu: {{ loan.title }}, eräpäivä {{ strptime(loan.due_date, '%Y-%m-%d').strftime('%-d.%-m.%Y') }}
                {% endfor %}{% for loan in result.not_renewed %}EI uusittu: {{ loan.title }} ({{ loan.reason }})
                {% endfor %}
```

### Automation ideas

- A reminder two days before the next due date, if the loans can't all be renewed
- A notification when a hold is ready for pickup, with its pickup number
- The family's due dates on one calendar card on the dashboard

## Trying it from the console first

`helmet_cli.py` logs in and reads a card's account with the integration's own code, without Home Assistant:

```
sudo apt install python3-aiohttp python3-bs4
python3 helmet_cli.py
```

It asks for the card number and PIN (or reads `HELMET_CARD` and `HELMET_PIN`), and prints the loans, holds and fees.
`--renew-all` renews every loan that can be renewed, and `--renew 1,3` the loans with those numbers in the list. It
asks before renewing, unless given `--yes`.
`--dump pages` also saves the pages the library answered with, which helps when something isn't read right. The saved
pages can have your name and other personal details in them, so remove those before sharing them.

## Installation

### With HACS

1. Add `https://github.com/quenthal/helmet_library` to HACS custom repositories with type **Integration**
2. Search for Helmet libraries in HACS and download it
3. Restart Home Assistant
4. Add the integration in Settings › Devices & services

### Manual

Copy the `custom_components/helmet` folder to your Home Assistant installation's `config/custom_components` folder
and restart Home Assistant.

The integration's icon shows in Home Assistant 2026.3 and later. It is the Helmet libraries' logo, used to tell
which library the integration is for; the integration is still unofficial and not made by Helmet.

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

The integration reads the account, and renews loans when asked to. It doesn't place, change or cancel holds.

## Limits

- The integration reads the web library's pages, so a change in them can stop it until it is updated. If the
  Loans sensor stops matching the web library, the log has a warning about it.
- Helmet is changing its library system in 2026–2027. The web library stays on Finna, which should keep the
  integration working, but a break around the change is possible.

When something isn't read right, the card's **Download diagnostics** on the integration page tells what the
integration read: the due dates, statuses and counts, and which details it found. It leaves out the card number,
PIN and name, and the titles, authors, places and codes of the loans and holds, so it can be attached to an issue.

## Development

```
pip install pytest-homeassistant-custom-component beautifulsoup4 ruff
pytest
ruff check . && ruff format --check .
```

The parser follows the templates of Finna ([NDL-VuFind2](https://github.com/NatLibFi/NDL-VuFind2), theme `finna2`),
and the test pages in `tests/fixtures` are built after them.
