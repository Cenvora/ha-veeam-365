"""Config flow for Veeam Backup & Replication integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    # Import the veeam_br library
    try:
        from veeam_br.v1_3_rev1 import Client
        from veeam_br.v1_3_rev1.api.login import create_token
        from veeam_br.v1_3_rev1.models.e_login_grant_type import ELoginGrantType
        from veeam_br.v1_3_rev1.models.token_login_spec import TokenLoginSpec
    except ImportError as err:
        _LOGGER.error("Failed to import veeam_br library: %s", err)
        raise ConnectionError("veeam_br library not installed") from err

    # Construct base URL
    base_url = f"https://{data[CONF_HOST]}:{data[CONF_PORT]}"

    # Test connection by attempting to authenticate
    try:

        def _test_connection():
            client = Client(
                base_url=base_url, verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            )
            body = TokenLoginSpec(
                grant_type=ELoginGrantType.PASSWORD,
                username=data[CONF_USERNAME],
                password=data[CONF_PASSWORD],
            )
            with client:
                return create_token.sync(client=client, body=body, x_api_version="1.3-rev1")

        token_result = await hass.async_add_executor_job(_test_connection)

        if not token_result or not token_result.access_token:
            raise PermissionError("Authentication failed - no access token received")

    except PermissionError as err:
        _LOGGER.error("Authentication failed: %s", err)
        raise
    except ConnectionError as err:
        _LOGGER.error("Failed to connect to Veeam server: %s", err)
        raise
    except Exception as err:
        _LOGGER.error("Unexpected error during connection test: %s", err)
        raise ConnectionError(f"Failed to connect: {err}") from err

    # Return info that you want to store in the config entry.
    return {"title": f"Veeam BR ({data[CONF_HOST]})"}


class VeeamBRConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Veeam Backup & Replication."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Check if already configured
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()

            try:
                info = await validate_input(self.hass, user_input)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        # Show the form
        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)
