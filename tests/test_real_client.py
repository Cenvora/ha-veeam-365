"""End to end through the real veeam-365 VeeamClient, over an httpx mock transport.

The other behavioural tests replace VeeamClient. These keep it, so they cover what the
integration actually depends on: how the client authenticates, what it raises, and — the
live failure — recovering from a session the server rejects mid-use with an empty body,
which used to wedge every poll with "Expecting value: line 1 column 1 (char 0)".
"""

from __future__ import annotations

import json

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
import httpx
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.veeam_365 import sdk as sdk_module
from custom_components.veeam_365.const import DOMAIN

from .conftest import ENTRY_DATA, copy_job_json, job_json, license_json, local_repo_json


class Server:
    """Answers the v8 endpoints the integration polls."""

    def __init__(self) -> None:
        self.tokens_issued = 0
        self.reject_next_jobs = 0
        self.refuse_login = False
        self.requests: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(f"{request.method} {path}")

        if path == "/v8/token":
            if self.refuse_login:
                return httpx.Response(400, json={"message": "Invalid credentials"})
            self.tokens_issued += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{self.tokens_issued}",
                    "refresh_token": "refresh",
                    "token_type": "bearer",
                    "expires_in": 3600,
                    ".issued": "2026-09-28T00:00:00+00:00",
                    ".expires": "2026-09-28T01:00:00+00:00",
                },
            )

        if path == "/v8/Jobs" and self.reject_next_jobs:
            # How VB365 answers a token it no longer accepts: 401 with an empty body
            self.reject_next_jobs -= 1
            return httpx.Response(401, content=b"")

        pages = {
            "/v8/Jobs": [job_json()],
            "/v8/CopyJobs": [copy_job_json()],
            "/v8/BackupRepositories": [local_repo_json()],
        }
        if path in pages:
            offset = int(request.url.params.get("offset", 0))
            limit = int(request.url.params.get("limit", 30))
            items = pages[path][offset : offset + limit]
            return httpx.Response(200, json={"offset": offset, "limit": limit, "results": items})
        if path == "/v8/ServiceInstance":
            return httpx.Response(200, json={"version": "8.1.0.305"})
        if path == "/v8/License":
            return httpx.Response(200, json=license_json())
        if path == "/v8/License/AutoUpdate":
            return httpx.Response(200, json={"isEnabled": False})
        return httpx.Response(404, content=json.dumps({"message": "not found"}).encode())


def _patch_transport(monkeypatch, server: Server) -> None:
    real_create_client = sdk_module.create_client

    def create_client(sdk, data):
        client = real_create_client(sdk, data)
        client._httpx_args = {"transport": httpx.MockTransport(server.handle)}
        return client

    monkeypatch.setattr("custom_components.veeam_365.create_client", create_client)


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=dict(ENTRY_DATA), unique_id="veeam:4443")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_polls_through_the_real_client(hass: HomeAssistant, monkeypatch) -> None:
    server = Server()
    _patch_transport(monkeypatch, server)

    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.vb365_job_daily_mail_last_status").state == "Success"
    assert "GET /v8/Jobs" in server.requests
    await hass.config_entries.async_unload(entry.entry_id)


async def test_a_rejected_session_recovers_within_the_same_poll(
    hass: HomeAssistant, monkeypatch
) -> None:
    """The live wedge: every poll failed until Home Assistant was restarted."""
    server = Server()
    _patch_transport(monkeypatch, server)
    entry = await _setup(hass)
    assert server.tokens_issued == 1

    server.reject_next_jobs = 1
    coordinator = entry.runtime_data["coordinator"]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert server.tokens_issued == 2, "the retry should log in again"
    assert hass.states.get("binary_sensor.vb365_server_veeam_example_com_connected").state == (
        STATE_ON
    )
    await hass.config_entries.async_unload(entry.entry_id)


async def test_refused_login_starts_reauth(hass: HomeAssistant, monkeypatch) -> None:
    server = Server()
    server.refuse_login = True
    _patch_transport(monkeypatch, server)

    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress()
    )
