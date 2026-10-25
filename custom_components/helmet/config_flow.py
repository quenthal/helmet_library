"""Adds a library card, and asks for its PIN again when the library stops accepting it."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import create_client
from .api import HelmetAuthError, HelmetError
from .const import (
    CONF_CARD_NUMBER,
    CONF_NAME,
    CONF_PIN,
    CONF_RENEW_WITHIN_DAYS,
    DEFAULT_RENEW_WITHIN_DAYS,
    DOMAIN,
    MAX_RENEW_WITHIN_DAYS,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_CARD_NUMBER): str,
        vol.Required(CONF_PIN): PASSWORD,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PIN): PASSWORD})
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_RENEW_WITHIN_DAYS, default=DEFAULT_RENEW_WITHIN_DAYS): NumberSelector(
            NumberSelectorConfig(
                min=0, max=MAX_RENEW_WITHIN_DAYS, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="days"
            )
        ),
    }
)


def normalized_card_number(card_number: str) -> str:
    """The card number without the spaces people type in it."""
    return "".join(card_number.split()).upper()


class HelmetConfigFlow(ConfigFlow, domain=DOMAIN):
    """One flow per library card."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HelmetOptionsFlow:
        return HelmetOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            card_number = normalized_card_number(user_input[CONF_CARD_NUMBER])
            await self.async_set_unique_id(card_number)
            self._abort_if_unique_id_configured()
            if error := await self._check_login(card_number, user_input[CONF_PIN]):
                errors["base"] = error
            else:
                name = user_input[CONF_NAME].strip() or card_number
                return self.async_create_entry(
                    title=name,
                    data={CONF_NAME: name, CONF_CARD_NUMBER: card_number, CONF_PIN: user_input[CONF_PIN]},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := await self._check_login(entry.data[CONF_CARD_NUMBER], user_input[CONF_PIN]):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_PIN: user_input[CONF_PIN]})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def _check_login(self, card_number: str, pin: str) -> str | None:
        """Logs in once with the card. Returns the error to show, or None when the login worked."""
        client = create_client(self.hass, card_number, pin)
        try:
            await client.login()
        except HelmetAuthError:
            return "invalid_auth"
        except HelmetError as err:
            _LOGGER.debug("Login check failed: %s", err)
            return "cannot_connect"
        return None


class HelmetOptionsFlow(OptionsFlow):
    """How many days before the due date the button renews a loan."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={**user_input, CONF_RENEW_WITHIN_DAYS: int(user_input[CONF_RENEW_WITHIN_DAYS])}
            )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, self.config_entry.options),
        )
