"""Config flow for Veeam Backup for Microsoft 365 integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv, selector
import voluptuous as vol

from .const import (
    API_VERSIONS,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_VERSION,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _get_api_version_selector_config(
    preferred_version: str | None = None,
) -> tuple[list[str], str]:
    """Get API version options and default for selector."""
    api_version_options = list(API_VERSIONS.keys())

    if preferred_version and preferred_version in api_version_options:
        return api_version_options, preferred_version

    if DEFAULT_API_VERSION in api_version_options:
        return api_version_options, DEFAULT_API_VERSION

    if api_version_options:
        return api_version_options, api_version_options[-1]

    _LOGGER.error("No API versions available, using fallback")
    return [DEFAULT_API_VERSION], DEFAULT_API_VERSION


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    api_version = data.get(CONF_API_VERSION, DEFAULT_API_VERSION)

    try:
        from veeam_365.client import VeeamClient
    except ImportError as err:
        _LOGGER.error("Error importing veeam_365: %s", err)
        raise ConnectionError("Failed to import veeam_365 modules") from err

    base_url = f"https://{data[CONF_HOST]}:{data[CONF_PORT]}"

    try:

        async def _test_connection():
            vc = VeeamClient(
                host=base_url,
                username=data[CONF_USERNAME],
                password=data[CONF_PASSWORD],
                api_version=api_version,
                verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            )
            await vc.connect()
            return vc

        vc = await _test_connection()

        # Verify connection was successful by attempting to access the client
        if not vc:
            raise PermissionError("Authentication failed")

    except Exception as err:
        raise ConnectionError(f"Failed to connect: {err}") from err

    return {"title": f"Veeam 365 ({data[CONF_HOST]})"}


class Veeam365ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Veeam Backup for Microsoft 365."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "Veeam365OptionsFlow":
        return Veeam365OptionsFlow()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle reconfiguration of the integration."""
        errors: dict[str, str] = {}
        reconf_entry = self._get_reconfigure_entry()

        if user_input is not None:
            # Merge with existing config data
            new_data = {**reconf_entry.data, **user_input}

            try:
                await validate_input(self.hass, new_data)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except PermissionError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Update the config entry
                return self.async_update_reload_and_abort(
                    reconf_entry,
                    data=new_data,
                    reason="reconfigure_successful",
                )

        api_version_options, default_api_version = _get_api_version_selector_config(
            reconf_entry.data.get(CONF_API_VERSION)
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=reconf_entry.data[CONF_HOST]): str,
                    vol.Required(CONF_PORT, default=reconf_entry.data[CONF_PORT]): cv.port,
                    vol.Required(CONF_USERNAME, default=reconf_entry.data[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(
                        CONF_VERIFY_SSL,
                        default=reconf_entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    ): bool,
                }
            ),
            errors=errors,
            description_placeholders={"host": reconf_entry.data[CONF_HOST]},
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauth flow."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauth confirmation."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            new_data = {**reauth_entry.data, **user_input}

            try:
                await validate_input(self.hass, new_data)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except PermissionError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data=new_data,
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=reauth_entry.data[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={"host": reauth_entry.data[CONF_HOST]},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Check if already configured
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()

            try:
                info = await validate_input(self.hass, user_input)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except PermissionError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        api_version_options, default_api_version = _get_api_version_selector_config()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
                    vol.Required(
                        CONF_API_VERSION, default=default_api_version
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=api_version_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )


class Veeam365OptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Veeam Backup for Microsoft 365."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate the new API version works
            test_data = {**self.config_entry.data, CONF_API_VERSION: user_input[CONF_API_VERSION]}

            try:
                await validate_input(self.hass, test_data)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except PermissionError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title="", data=user_input)

        current_api_version = self.config_entry.options.get(
            CONF_API_VERSION, self.config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
        )

        api_version_options, default_api_version = _get_api_version_selector_config(
            current_api_version
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_API_VERSION, default=current_api_version
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=api_version_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )
