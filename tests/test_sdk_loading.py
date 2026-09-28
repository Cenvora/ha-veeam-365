"""Nothing is imported on the event loop once load_sdk has run.

veeam-365 imports its generated modules lazily — on connect(), on token refresh, and on
``client.api("job").job_get`` attribute access — which Home Assistant reported as
``Detected blocking call to import_module ... await veeam_client.connect()``. load_sdk
imports all of it up front in an executor; this checks it really is all of it.

Run in a fresh interpreter: in this process other tests have already imported everything,
so a missing pre-import could not show up.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).parent.parent

SCRIPT = r"""
import asyncio, importlib, json, sys
import httpx

from custom_components.veeam_365.sdk import READ_OPERATIONS, load_sdk

API = sys.argv[1]
sdk = load_sdk(API)
before = set(sys.modules)

TOKEN = {
    "access_token": "a", "refresh_token": "r", "token_type": "bearer", "expires_in": 1,
    ".issued": "2026-09-28T00:00:00+00:00", ".expires": "2026-09-28T01:00:00+00:00",
}

def handle(request):
    if request.url.path.endswith("/token"):
        return httpx.Response(200, json=TOKEN)
    return httpx.Response(200, json=[])

async def main():
    client = sdk.client_class(
        host="https://veeam.example.com:4443", username="u", password="p",
        api_version=API, verify_ssl=False, timeout=5,
    )
    client._httpx_args = {"transport": httpx.MockTransport(handle)}
    try:
        await client.connect()
    except Exception:
        pass  # older token models want other fields; the imports have happened by then
    for name in READ_OPERATIONS:
        try:
            # expires_in=1 means every call refreshes the token too
            await client.call(sdk.operation(name))
        except Exception:
            pass  # the payloads are not realistic; only the imports matter here
    await client.close()

asyncio.run(main())
print(json.dumps(sorted(name for name in set(sys.modules) - before if name.startswith("veeam_365"))))
"""


@pytest.mark.parametrize("api_module", ["v6", "v7", "v8"])
def test_no_sdk_module_is_imported_after_load_sdk(api_module):
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, api_module],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    imported_late = json.loads(result.stdout.strip().splitlines()[-1])

    assert imported_late == [], f"imported on the event loop: {imported_late}"
