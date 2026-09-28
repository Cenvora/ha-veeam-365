"""Common fixtures for Veeam Backup for Microsoft 365 tests.

The behavioural tests run the real integration against a fake server. The real veeam-365
package is loaded — its models parse the fake responses and its operation modules supply
the functions the integration calls — and only VeeamClient is replaced, by FakeVeeamClient,
which answers each operation from a handler table instead of the network.
"""

from __future__ import annotations

from collections.abc import Callable
import copy
from functools import cache
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.veeam_365.sdk import VeeamSdk, load_sdk

JOB_ID = "11111111-1111-1111-1111-111111111111"
COPY_JOB_ID = "22222222-2222-2222-2222-222222222222"
REPO_ID = "33333333-3333-3333-3333-333333333333"
OBJECT_REPO_ID = "44444444-4444-4444-4444-444444444444"
INSTALLATION_ID = "55555555-5555-5555-5555-555555555555"

ENTRY_DATA = {
    "host": "veeam.example.com",
    "port": 4443,
    "username": "admin",
    "password": "secret",
    "verify_ssl": True,
    "api_version": "8",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/veeam_365 in every test."""
    yield


@cache
def real_sdk(api_module: str) -> VeeamSdk:
    """Load (and patch) a real API version once per test session."""
    return load_sdk(api_module)


def job_json(job_id: str = JOB_ID, name: str = "Daily Mail", **overrides: Any) -> dict:
    data = {
        "id": job_id,
        "name": name,
        "backupType": "EntireOrganization",
        "lastRun": "2026-09-27T05:00:00+00:00",
        "nextRun": "2026-09-28T05:00:00+00:00",
        "lastBackup": "2026-09-27T05:10:00+00:00",
        "isEnabled": True,
        "lastStatus": "Success",
    }
    data.update(overrides)
    return data


def copy_job_json(copy_job_id: str = COPY_JOB_ID, name: str = "Mail Copy", **overrides) -> dict:
    data = {
        "id": copy_job_id,
        "name": name,
        "backupJobId": JOB_ID,
        "lastRun": "2026-09-27T06:00:00+00:00",
        "lastBackup": "2026-09-27T06:05:00+00:00",
        "isEnabled": True,
        "lastStatus": "Warning",
    }
    data.update(overrides)
    return data


def local_repo_json(repo_id: str = REPO_ID, name: str = "Default Backup Repository", **overrides):
    data = {
        "id": repo_id,
        "name": name,
        "description": "Local disk",
        "path": "C:\\VeeamRepository",
        "retentionType": "ItemLevel",
        "capacityBytes": 100 * 1024**3,
        "freeSpaceBytes": 40 * 1024**3,
        "isLongTerm": False,
        "isOutOfSync": False,
        "isOutOfOrder": False,
        "isOutdated": False,
    }
    data.update(overrides)
    return data


def object_repo_json(repo_id: str = OBJECT_REPO_ID, name: str = "S3 Archive", **overrides):
    data = {
        "id": repo_id,
        "name": name,
        "description": "Object storage",
        "retentionType": "SnapshotBased",
        "capacityBytes": 10 * 1024**3,
        "freeSpaceBytes": 9 * 1024**3,
        "isOutOfSync": True,
        "isOutOfOrder": False,
        "isOutdated": False,
        "objectStorage": {
            "usedSpaceBytes": 5 * 1024**3,
            "enableImmutability": True,
            "immutabilityPeriodDays": 30,
            "type": "AmazonS3",
        },
    }
    data.update(overrides)
    return data


def license_json(**overrides) -> dict:
    data = {
        "status": "Valid",
        "type": "Subscription",
        "licenseExpires": "2027-01-01T00:00:00+00:00",
        "gracePeriodExpires": "2027-02-01T00:00:00+00:00",
        "licensedTo": "Example Corp",
        "email": "it@example.com",
        "package": "Standard",
        "totalNumber": 100,
        "usedNumber": 42,
        "newNumber": 1,
    }
    data.update(overrides)
    return data


PAGE_CLASSES = {
    "job.job_get": ("RESTJob", "PageOfRESTJob"),
    "copy_job.copy_job_get": ("RESTCopyJob", "PageOfRESTCopyJob"),
    "backup_repository.backup_repository_get_repositories": (
        "RESTBackupRepository",
        "PageOfRESTBackupRepository",
    ),
}


class FakeServer:
    """What the fake clients talk to: per-operation handlers, and a record of calls."""

    def __init__(self) -> None:
        self.api_module = "v8"
        self.connect_error: BaseException | None = None
        self.collections: dict[str, list[dict]] = {
            "job.job_get": [job_json()],
            "copy_job.copy_job_get": [copy_job_json()],
            "backup_repository.backup_repository_get_repositories": [
                local_repo_json(),
                object_repo_json(),
            ],
        }
        self.license = license_json()
        # Operation name -> a result, an exception to raise, or a callable(**kwargs)
        self.overrides: dict[str, Any] = {}
        self.calls: list[tuple[str, dict]] = []
        self.clients: list[FakeVeeamClient] = []
        # Server-side page size cap for v8 collections
        self.max_page_size: int | None = None

    @property
    def sdk(self) -> VeeamSdk:
        return real_sdk(self.api_module)

    @property
    def models(self):
        return self.sdk.models

    def calls_to(self, name: str) -> list[dict]:
        return [kwargs for called, kwargs in self.calls if called == name]

    def error(self, message: str = "Access denied"):
        """The server's documented error model, as an operation returns it."""
        return self.models.RESTExceptionInfo.from_dict({"message": message})

    # -- answering -----------------------------------------------------------

    def answer(self, name: str, kwargs: dict) -> Any:
        if name in self.overrides:
            result = self.overrides[name]
            if callable(result) and not isinstance(result, type):
                result = result(**kwargs)
            return result

        if name in self.collections:
            return self._collection(name, kwargs)
        if name == "service_instance.service_instance_get":
            return self.models.RESTServiceInstance.from_dict(
                {"installationId": INSTALLATION_ID, "version": "8.1.0.305"}
            )
        if name == "license_.license_get":
            return self.models.RESTLicense.from_dict(self.license)
        if name == "license_.license_get_auto_update":
            return self.models.RESTLicenseAutoUpdate.from_dict({"isEnabled": True})
        # Actions answer 204 No Content, which the SDK returns as None
        return None

    def _collection(self, name: str, kwargs: dict) -> Any:
        item_class, page_class = PAGE_CLASSES[name]
        items = self.collections[name]
        if not self.sdk.accepts(name, "limit"):
            # v6 and v7 return a plain list
            return [getattr(self.models, item_class).from_dict(item) for item in items]

        limit = kwargs.get("limit", 30)
        if self.max_page_size is not None:
            limit = min(limit, self.max_page_size)
        offset = kwargs.get("offset", 0)
        return getattr(self.models, page_class).from_dict(
            {"offset": offset, "limit": limit, "results": items[offset : offset + limit]}
        )


class FakeVeeamClient:
    """Stands in for veeam_365.client.VeeamClient."""

    def __init__(self, server: FakeServer, **kwargs: Any) -> None:
        self.server = server
        self.kwargs = kwargs
        self.connected = False
        self.closed = False
        server.clients.append(self)

    async def connect(self) -> None:
        if self.server.connect_error is not None:
            raise self.server.connect_error
        self.connected = True

    async def call(self, fn: Callable[..., Any], **kwargs: Any) -> Any:
        name = fn.__module__.split(".api.", 1)[1]
        self.server.calls.append((name, kwargs))
        result = self.server.answer(name, kwargs)
        if isinstance(result, BaseException):
            raise result
        return result

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(name="server")
def server_fixture():
    """A fake server, with load_sdk patched to hand out FakeVeeamClient."""
    server = FakeServer()

    def fake_load(api_module: str) -> VeeamSdk:
        server.api_module = api_module
        sdk = copy.copy(real_sdk(api_module))
        sdk.client_class = lambda **kwargs: FakeVeeamClient(server, **kwargs)
        return sdk

    with (
        patch("custom_components.veeam_365.load_sdk", side_effect=fake_load),
        patch("custom_components.veeam_365.config_flow.load_sdk", side_effect=fake_load),
        # No network probing: tests pin the API version
        patch(
            "custom_components.veeam_365.api_version.detect_api_version",
            return_value=None,
        ),
        patch(
            "custom_components.veeam_365.config_flow.detect_rest_api",
            return_value=None,
        ),
    ):
        yield server
