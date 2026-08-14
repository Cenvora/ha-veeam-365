"""Config flow for Veeam Backup for Microsoft 365 integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv, selector
import voluptuous as vol

from .api_version import async_resolve_api_version
from .const import (
    API_VERSIONS,
    AUTO_API_VERSION,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_MODULE,
    DEFAULT_API_VERSION,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class WrongPortError(ConnectionError):
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


async def async_find_working_port(data: dict[str, Any], configured_port: int) -> int | None:
    """Return another port the REST API answers on, or None.

    The REST API service listens on 4443 by default but the port is configurable, so "cannot
    connect" is quite often the wrong port rather than a wrong host or a firewall. Worth one
    extra probe to be able to say which.
    """
    try:
        # Guarded with the probe itself: this runs inside validate_input's failure handler,
        # so an ImportError escaping here would replace the real connection error with
        # "unknown". A hand-installed older veeam-365 should degrade to the generic error,
        # not a misleading one.
        from veeam_365.discovery import DEFAULT_PORTS, detect_rest_api

        others = [port for port in DEFAULT_PORTS if port != configured_port]
        if not others:
            return None

        endpoint = await detect_rest_api(
            data[CONF_HOST],
            ports=others,
            versions=list(API_VERSIONS.values()),
            verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )
    except Exception as err:  # noqa: BLE001 - a failed probe just means no advice to give
        _LOGGER.debug("Port probe failed: %s", err)
        return None

    return endpoint.port if endpoint else None


async def _raise_wrong_port_if_answering(data: dict[str, Any], err: Exception) -> None:
    """Turn a connection failure into WrongPortError when another port answers."""
    working_port = await async_find_working_port(data, data[CONF_PORT])
    if working_port is None:
        return

    _LOGGER.warning(
        "Could not reach the Veeam REST API on %s:%s, but it answered on port %s",
        data[CONF_HOST],
        data[CONF_PORT],
        working_port,
    )
    raise WrongPortError(working_port) from err


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    A stored "auto" is resolved here only to test the connection — it is deliberately not
    written back. Keeping the sentinel means every setup re-resolves it, so a server upgrade
    or a newer veeam-365 moves the entry onto the newer version on its own.
    """
    api_version_display = await async_resolve_api_version(data)
    # Convert display version (e.g., "8") to module version (e.g., "v8") for VeeamClient
    api_version = API_VERSIONS.get(api_version_display, DEFAULT_API_MODULE)

    try:
        from veeam_365.client import VeeamClient
    except ImportError as err:
        _LOGGER.error("Error importing veeam_365: %s", err)
        raise ConnectionError("Failed to import veeam_365 modules") from err

    base_url = f"https://{data[CONF_HOST]}:{data[CONF_PORT]}"
    _LOGGER.debug(
        "Attempting to validate connection to Veeam server at %s (verify_ssl=%s, api_version=%s)",
        base_url,
        data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        api_version_display,
    )

    vc = None
    try:
        import json

        # Create VeeamClient - constructor is not blocking
        _LOGGER.debug(
            "Creating VeeamClient with username=%s, api_version=%s",
            data[CONF_USERNAME],
            api_version,
        )
        vc = VeeamClient(
            host=base_url,
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            api_version=api_version,
            verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            disable_antiforgery_token=True,
        )

        # Connect is async, just await it directly
        _LOGGER.debug("Calling vc.connect()...")
        try:
            await vc.connect()
            _LOGGER.debug("vc.connect() succeeded")
        except json.JSONDecodeError as err:
            # Server returned non-JSON response (likely an error with empty body)
            _LOGGER.error(
                "Server at %s returned invalid JSON. Exception: %s. Position: line %s column %s.",
                base_url,
                err.msg,
                err.lineno,
                err.colno,
                exc_info=True,
            )
            raise ConnectionError(
                "Server returned invalid response. Check credentials and server."
            ) from err
        except Exception as err:
            _LOGGER.error(
                "Unexpected error during connection: %s (type: %s)",
                err,
                type(err).__name__,
                exc_info=True,
            )
            raise

    except PermissionError as err:
        _LOGGER.error("Authentication failed for user %s: %s", data[CONF_USERNAME], err)
        raise PermissionError("Invalid credentials") from err
    except ConnectionError as err:
        _LOGGER.error("Network connection error to %s: %s", base_url, err)
        await _raise_wrong_port_if_answering(data, err)
        raise ConnectionError(f"Cannot connect to server at {base_url}") from err
    except Exception as err:
        _LOGGER.error(
            "Failed to connect to Veeam server at %s (api_version=%s): %s",
            base_url,
            api_version,
            err,
            exc_info=True,
        )
        await _raise_wrong_port_if_answering(data, err)
        raise ConnectionError(f"Failed to connect: {type(err).__name__}: {err}") from err
    finally:
        # Always logout and close the validation client to free up resources
        if vc is not None:
            try:
                # Properly logout to revoke the token on the server
                # This prevents race conditions when async_setup_entry is called
                # immediately after config flow validation
                try:
                    logout_module = await asyncio.to_thread(
                        __import__,
                        f"veeam_365.{api_version}.api.auth.logout",
                        fromlist=["asyncio"],
                    )
                    # The logout module has an async function named "asyncio" (library convention)
                    logout_async_func = getattr(logout_module, "asyncio")
                    await vc.call(logout_async_func)
                    _LOGGER.debug("Successfully logged out validation VeeamClient")
                    # Give server time to process logout before setup creates new session
                    await asyncio.sleep(1.0)
                except Exception as logout_err:
                    _LOGGER.debug(
                        "Could not logout validation client: %s (type: %s)",
                        logout_err,
                        type(logout_err).__name__,
                    )

                await vc.close()
                _LOGGER.debug("Closed validation VeeamClient")
            except Exception as err:
                _LOGGER.warning("Error closing validation client: %s", err)

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

            try:
                await validate_input(self.hass, data)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except WrongPortError as err:
                # Subclasses ConnectionError, so it has to be caught before it
                errors["base"] = "wrong_port"
                wrong_port = err.port
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during reconfigure")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reconf_entry,
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

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauth upon API authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
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

            try:
                await validate_input(self.hass, data)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except WrongPortError as err:
                # Subclasses ConnectionError, so it has to be caught before it
                errors["base"] = "wrong_port"
                wrong_port = err.port
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"
            else:
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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        wrong_port: int | None = None

        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()

            try:
                info = await validate_input(self.hass, user_input)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except WrongPortError as err:
                # Subclasses ConnectionError, so it has to be caught before it
                errors["base"] = "wrong_port"
                wrong_port = err.port
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
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


class Veeam365OptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Veeam Backup for Microsoft 365 integration."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        wrong_port: int | None = None

        if user_input is not None:
            test_data = {**self.config_entry.data, CONF_API_VERSION: user_input[CONF_API_VERSION]}

            try:
                await validate_input(self.hass, test_data)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except WrongPortError as err:
                # Subclasses ConnectionError, so it has to be caught before it
                errors["base"] = "wrong_port"
                wrong_port = err.port
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception validating options")
                errors["base"] = "unknown"
            else:
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
                "Stored API version %s is invalid for Veeam Backup for Microsoft 365, falling back to default",
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
