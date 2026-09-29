"""Config flow for Veeam Backup for Microsoft 365 integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.httpx_client import get_async_client

from veeam_365.discovery import DEFAULT_PORTS, detect_rest_api
from veeam_365.exceptions import VeeamAuthenticationError, VeeamError

from .api_version import async_resolve_api_version
from .const import (
    API_VERSIONS,
    AUTO_API_VERSION,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    CONNECT_TIMEOUT,
    DEFAULT_API_MODULE,
    DEFAULT_API_VERSION,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .coordinator import describe_error
from .sdk import create_client, load_sdk

_LOGGER = logging.getLogger(__name__)

# How long to let the server process the validation session's logout before setup logs in
LOGOUT_SETTLE_SECONDS = 1.0


class CannotConnect(HomeAssistantError):
    """The server could not be reached, or did not answer usefully."""


class InvalidAuth(HomeAssistantError):
    """The server refused the credentials."""


class WrongPortError(CannotConnect):
    """The configured port did not answer, but another REST API port did.

    Carries the port that answered so the form can name it.
    """

    def __init__(self, port: int) -> None:
        super().__init__(f"The REST API answered on port {port}, not the configured port")
        self.port = port


def _get_api_version_selector_config(
    preferred_version: str | None = None,
) -> tuple[list[str], str]:
    """Get API version options and default for selector.

    AUTO_API_VERSION leads the list and is the default, so the common case is not asking
    the user to know which version their server speaks.
    """
    api_version_options = [AUTO_API_VERSION, *API_VERSIONS.keys()]

    if preferred_version and preferred_version in api_version_options:
        return api_version_options, preferred_version

    return api_version_options, AUTO_API_VERSION


async def async_find_working_port(
    hass: HomeAssistant, data: dict[str, Any], configured_port: int
) -> int | None:
    """Return another port the REST API answers on, or None.

    The REST API service listens on 4443 by default but the port is configurable, so "cannot
    connect" is quite often the wrong port rather than a wrong host or a firewall. Worth one
    extra probe to be able to say which.
    """
    others = [port for port in DEFAULT_PORTS if port != configured_port]
    if not others:
        return None

    verify_ssl = data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    try:
        endpoint = await detect_rest_api(
            data[CONF_HOST],
            ports=others,
            versions=list(API_VERSIONS.values()),
            verify_ssl=verify_ssl,
            client=get_async_client(hass, verify_ssl=verify_ssl),
        )
    except Exception as err:  # noqa: BLE001 - a failed probe just means no advice to give
        _LOGGER.debug("Port probe failed: %r", err)
        return None

    return endpoint.port if endpoint else None


async def _raise_wrong_port_if_answering(
    hass: HomeAssistant, data: dict[str, Any], err: Exception
) -> None:
    """Turn a connection failure into WrongPortError when another port answers."""
    working_port = await async_find_working_port(hass, data, data[CONF_PORT])
    if working_port is None:
        return

    _LOGGER.warning(
        "Could not reach the Veeam REST API on %s:%s, but it answered on port %s",
        data[CONF_HOST],
        data[CONF_PORT],
        working_port,
    )
    raise WrongPortError(working_port) from err


async def _logout(client: Any, sdk: Any) -> None:
    """Revoke the validation session, so setup does not race it with a second login."""
    if not sdk.has_operation("auth.logout"):
        # v6 has no logout; the session simply expires
        return
    try:
        await client.call(sdk.operation("auth.logout"))
        await asyncio.sleep(LOGOUT_SETTLE_SECONDS)
    except Exception as err:  # noqa: BLE001 - logging out is a courtesy
        _LOGGER.debug("Could not log out the validation session: %s", describe_error(err))


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Raises InvalidAuth for refused credentials and CannotConnect (or WrongPortError) for
    anything that stops the server answering.

    A stored "auto" is resolved here only to test the connection — it is deliberately not
    written back. Keeping the sentinel means every setup re-resolves it, so a server upgrade
    or a newer veeam-365 moves the entry onto the newer version on its own.
    """
    api_version_display = await async_resolve_api_version(hass, data)
    # Convert display version (e.g., "8") to module version (e.g., "v8") for VeeamClient
    api_module = API_VERSIONS.get(api_version_display, DEFAULT_API_MODULE)

    try:
        sdk = await hass.async_add_executor_job(load_sdk, api_module)
    except ImportError as err:
        _LOGGER.error("The installed veeam-365 cannot load API %s: %r", api_module, err)
        raise CannotConnect(f"veeam-365 cannot load API {api_module}") from err

    _LOGGER.debug(
        "Validating connection to %s:%s (api_version=%s)",
        data[CONF_HOST],
        data[CONF_PORT],
        api_version_display,
    )

    client = create_client(sdk, data)
    connected = False
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            await client.connect()
        connected = True
    except VeeamAuthenticationError as err:
        # Refused credentials prove the port is right, so there is nothing to probe
        _LOGGER.debug("Authentication failed for %s: %s", data[CONF_USERNAME], err)
        raise InvalidAuth from err
    except (TimeoutError, VeeamError, httpx.HTTPError, OSError) as err:
        _LOGGER.debug(
            "Could not connect to %s:%s: %s", data[CONF_HOST], data[CONF_PORT], describe_error(err)
        )
        await _raise_wrong_port_if_answering(hass, data, err)
        raise CannotConnect(describe_error(err)) from err
    finally:
        if connected:
            await _logout(client, sdk)
        await client.close()

    return {"title": f"Veeam 365 ({data[CONF_HOST]})"}


async def _async_validate(
    hass: HomeAssistant, data: dict[str, Any], errors: dict[str, str]
) -> tuple[dict[str, Any] | None, int | None]:
    """Run validate_input, recording the form error. Returns (info, wrong port)."""
    try:
        return await validate_input(hass, data), None
    except InvalidAuth:
        errors["base"] = "invalid_auth"
    except WrongPortError as err:
        # Subclasses CannotConnect, so it has to be caught before it
        errors["base"] = "wrong_port"
        return None, err.port
    except CannotConnect:
        errors["base"] = "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected exception validating the connection")
        errors["base"] = "unknown"
    return None, None


class Veeam365ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Veeam Backup for Microsoft 365."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> Veeam365OptionsFlow:
        return Veeam365OptionsFlow()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        errors: dict[str, str] = {}
        wrong_port: int | None = None
        reconf_entry = self._get_reconfigure_entry()

        if user_input is not None:
            # Merge with existing config data
            data = {
                **reconf_entry.data,
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            }

            # The unique ID is host:port, so moving the entry to another server changes it —
            # but never onto a server another entry already covers
            unique_id = f"{data[CONF_HOST]}:{data[CONF_PORT]}"
            await self.async_set_unique_id(unique_id)
            if unique_id != reconf_entry.unique_id:
                self._abort_if_unique_id_configured()

            info, wrong_port = await _async_validate(self.hass, data, errors)
            if info is not None:
                # Reloads once; there is no update listener to reload a second time
                return self.async_update_reload_and_abort(
                    reconf_entry,
                    unique_id=unique_id,
                    data=data,
                    reason="reconfigure_successful",
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=reconf_entry.data.get(CONF_HOST)): cv.string,
                    vol.Required(
                        CONF_PORT, default=reconf_entry.data.get(CONF_PORT, DEFAULT_PORT)
                    ): cv.port,
                    vol.Required(
                        CONF_USERNAME, default=reconf_entry.data.get(CONF_USERNAME)
                    ): cv.string,
                    vol.Required(CONF_PASSWORD): cv.string,
                    vol.Optional(
                        CONF_VERIFY_SSL,
                        default=reconf_entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    ): cv.boolean,
                }
            ),
            errors=errors,
            description_placeholders={
                "host": reconf_entry.data.get(CONF_HOST),
                "wrong_port": str(wrong_port or ""),
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauth upon API authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth dialog."""
        errors: dict[str, str] = {}
        wrong_port: int | None = None
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            # Merge with existing config data
            data = {
                **reauth_entry.data,
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }

            info, wrong_port = await _async_validate(self.hass, data, errors)
            if info is not None:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data=data,
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=reauth_entry.data.get(CONF_USERNAME)
                    ): cv.string,
                    vol.Required(CONF_PASSWORD): cv.string,
                }
            ),
            errors=errors,
            description_placeholders={
                "host": reauth_entry.data[CONF_HOST],
                "wrong_port": str(wrong_port or ""),
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        wrong_port: int | None = None

        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()

            info, wrong_port = await _async_validate(self.hass, user_input, errors)
            if info is not None:
                return self.async_create_entry(title=info["title"], data=user_input)

        api_version_options, api_version_default = _get_api_version_selector_config(
            user_input.get(CONF_API_VERSION) if user_input else None
        )

        # Preserve user input on validation failure (except password for security)
        host_default = user_input[CONF_HOST] if user_input else vol.UNDEFINED
        port_default = user_input.get(CONF_PORT, DEFAULT_PORT) if user_input else DEFAULT_PORT
        username_default = user_input[CONF_USERNAME] if user_input else vol.UNDEFINED
        verify_ssl_default = (
            user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            if user_input
            else DEFAULT_VERIFY_SSL
        )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=host_default): cv.string,
                vol.Required(CONF_PORT, default=port_default): cv.port,
                vol.Required(CONF_USERNAME, default=username_default): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Optional(CONF_VERIFY_SSL, default=verify_ssl_default): cv.boolean,
                vol.Optional(
                    CONF_API_VERSION, default=api_version_default
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=api_version_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_API_VERSION,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={"wrong_port": str(wrong_port or "")},
        )


class Veeam365OptionsFlow(config_entries.OptionsFlowWithReload):
    """Handle options flow for Veeam Backup for Microsoft 365 integration.

    OptionsFlowWithReload reloads the entry when the options change, which is why there is
    no update listener: one would reload a second time after every reauth and reconfigure.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        wrong_port: int | None = None

        if user_input is not None:
            test_data = {**self.config_entry.data, CONF_API_VERSION: user_input[CONF_API_VERSION]}

            info, wrong_port = await _async_validate(self.hass, test_data, errors)
            if info is not None:
                # Stored verbatim, including "auto": the point of auto is that it is
                # re-resolved on every setup rather than frozen at the moment it was chosen
                return self.async_create_entry(title="", data=user_input)

        api_version_options = [AUTO_API_VERSION, *API_VERSIONS.keys()]

        current_api_version = self.config_entry.options.get(
            CONF_API_VERSION,
            self.config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )

        if current_api_version not in api_version_options:
            _LOGGER.warning(
                "Stored API version %s is invalid for Veeam Backup for Microsoft 365, "
                "falling back to default",
                current_api_version,
            )
            current_api_version = DEFAULT_API_VERSION

        options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_API_VERSION, default=current_api_version
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=api_version_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_API_VERSION,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            errors=errors,
            description_placeholders={"wrong_port": str(wrong_port or "")},
        )
