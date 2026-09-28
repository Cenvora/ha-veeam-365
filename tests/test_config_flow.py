"""Config, reauth, reconfigure and options flows against the fake server."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from veeam_365.exceptions import VeeamAuthenticationError

from custom_components.veeam_365.const import DOMAIN

from .conftest import ENTRY_DATA, FakeServer

USER_INPUT = {key: value for key, value in ENTRY_DATA.items()}


@pytest.fixture(autouse=True)
def fast_logout():
    with patch("custom_components.veeam_365.config_flow.LOGOUT_SETTLE_SECONDS", 0):
        yield


@pytest.fixture
def no_setup():
    """Creating an entry would set it up; these tests are about the flow."""
    with patch("custom_components.veeam_365.async_setup_entry", return_value=True) as setup:
        yield setup


async def start_user_flow(hass: HomeAssistant, user_input: dict):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_user_flow_creates_the_entry(
    hass: HomeAssistant, server: FakeServer, no_setup
) -> None:
    result = await start_user_flow(hass, USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == "veeam.example.com:4443"
    client = server.clients[-1]
    assert client.closed, "the validation client must be closed"
    assert server.calls_to("auth.logout") == [{}]


async def test_refused_credentials(hass: HomeAssistant, server: FakeServer) -> None:
    server.connect_error = VeeamAuthenticationError("refused")

    result = await start_user_flow(hass, USER_INPUT)

    assert result["errors"] == {"base": "invalid_auth"}
    assert server.clients[-1].closed
    assert server.calls_to("auth.logout") == [], "nothing to log out of"


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), TimeoutError(), OSError("unreachable")],
    ids=lambda err: type(err).__name__,
)
async def test_unreachable_server(hass: HomeAssistant, server: FakeServer, error) -> None:
    server.connect_error = error

    result = await start_user_flow(hass, USER_INPUT)

    assert result["errors"] == {"base": "cannot_connect"}
    assert server.clients[-1].closed


async def test_wrong_port_is_named(hass: HomeAssistant, server: FakeServer) -> None:
    server.connect_error = httpx.ConnectError("refused")

    with patch(
        "custom_components.veeam_365.config_flow.detect_rest_api",
        return_value=SimpleNamespace(port=4443),
    ):
        result = await start_user_flow(hass, {**USER_INPUT, "port": 443})

    assert result["errors"] == {"base": "wrong_port"}
    assert result["description_placeholders"]["wrong_port"] == "4443"


async def test_reauth_updates_the_credentials(hass: HomeAssistant, server: FakeServer) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(ENTRY_DATA), unique_id="veeam.example.com:4443"
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    with patch("custom_components.veeam_365.async_setup_entry", return_value=True) as setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "admin", "password": "new-secret"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new-secret"
    assert len(setup.mock_calls) == 1, "reloaded exactly once"


async def test_reauth_with_wrong_credentials_stays_on_the_form(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(ENTRY_DATA), unique_id="veeam.example.com:4443"
    )
    entry.add_to_hass(hass)
    server.connect_error = VeeamAuthenticationError("refused")

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "admin", "password": "wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_moves_the_unique_id(hass: HomeAssistant, server: FakeServer) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(ENTRY_DATA), unique_id="veeam.example.com:4443"
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    with patch("custom_components.veeam_365.async_setup_entry", return_value=True) as setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "host": "vb365.example.com",
                "port": 4443,
                "username": "admin",
                "password": "secret",
                "verify_ssl": False,
            },
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == "vb365.example.com:4443"
    assert entry.data["host"] == "vb365.example.com"
    assert entry.data["verify_ssl"] is False
    assert len(setup.mock_calls) == 1, "reloaded exactly once"


async def test_reconfigure_onto_another_entrys_server_is_refused(
    hass: HomeAssistant, server: FakeServer
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(ENTRY_DATA), unique_id="veeam.example.com:4443"
    )
    entry.add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN, data={**ENTRY_DATA, "host": "other"}, unique_id="other:4443"
    ).add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"host": "other", "port": 4443, "username": "admin", "password": "secret"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.unique_id == "veeam.example.com:4443"


async def test_options_store_auto_verbatim(hass: HomeAssistant, server: FakeServer) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(ENTRY_DATA), unique_id="veeam.example.com:4443"
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch("custom_components.veeam_365.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"api_version": "auto"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"api_version": "auto"}
