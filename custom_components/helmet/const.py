"""Constants for the Helmet libraries integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "helmet"

# The Helmet web library runs on Finna (the National Library's VuFind).
BASE_URL: Final = "https://helmet.finna.fi"
LOGIN_PATH: Final = "/MyResearch/UserLogin"
LOANS_PATH: Final = "/MyResearch/CheckedOut"
HOLDS_PATH: Final = "/Holds/List"
AJAX_PATH: Final = "/AJAX/JSON"

# Pages are read in English, so that the labels the parser looks for stay the same
# whatever language the account uses.
LANGUAGE: Final = "en-gb"

# When Finna offers more than one library to log in to, the one whose id has this in it.
LOGIN_TARGET_HINT: Final = "helmet"

CONF_CARD_NUMBER: Final = "card_number"
CONF_PIN: Final = "pin"
CONF_NAME: Final = "name"

UPDATE_INTERVAL: Final = timedelta(hours=1)
REQUEST_TIMEOUT: Final = 30
# A loan list longer than this many pages is cut, to keep a broken page from looping.
MAX_PAGES: Final = 10

USER_AGENT: Final = "Mozilla/5.0 (compatible; HomeAssistant-Helmet/0.1; +https://www.home-assistant.io)"

SERVICE_RENEW_LOANS: Final = "renew_loans"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_TITLES: Final = "titles"
ATTR_DUE_WITHIN_DAYS: Final = "due_within_days"
# Fired after every renewal, with the loans that were renewed and those that weren't.
EVENT_LOANS_RENEWED: Final = "helmet_loans_renewed"
