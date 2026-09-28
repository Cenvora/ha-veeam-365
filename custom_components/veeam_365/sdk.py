"""Loading veeam-365 without blocking the event loop.

veeam-365 imports its generated modules lazily: ``VeeamClient.connect()`` imports the auth
operation and token models, and ``client.api("job").job_get`` imports the operation module
on attribute access. Each of those is a disk read, which Home Assistant reports as a blocking
call when it happens in the event loop (``Detected blocking call to import_module ...``).
Wrapping ``client.api`` in ``asyncio.to_thread`` does not help, because the import happens
later, on the attribute access, back in the loop.

So everything the integration will touch is imported here, once, in an executor job, before
the client connects. The operation functions are resolved here too and handed out by name,
so nothing on the event loop has to import anything afterwards.

The same pass applies the null-tolerance patches (see sdk_patches.py) to every model, before
anything parses a response.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import importlib
import logging
import ssl
import sys
from typing import Any

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.util.ssl import get_default_context, get_default_no_verify_context

from .const import CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL, REQUEST_TIMEOUT
from .sdk_patches import patch_models

_LOGGER = logging.getLogger(__name__)

# Every generated operation the integration calls, as "<api module>.<operation>"
READ_OPERATIONS = (
    "job.job_get",
    "copy_job.copy_job_get",
    "service_instance.service_instance_get",
    "license_.license_get",
    "license_.license_get_auto_update",
    "backup_repository.backup_repository_get_repositories",
)
ACTION_OPERATIONS = (
    "job.job_start_action",
    "job.job_stop_action",
    "job.job_enable_action",
    "job.job_disable_action",
    "copy_job.copy_job_start",
    "copy_job.copy_job_stop",
    "copy_job.copy_job_enable",
    "copy_job.copy_job_disable",
    "backup_repository.backup_repository_start_synchronize_action",
)
# Imported by VeeamClient itself while authenticating, or by the config flow
AUTH_OPERATIONS = (
    "auth.token",
    "auth.logout",
)
OPERATIONS = READ_OPERATIONS + ACTION_OPERATIONS


class VeeamSdk:
    """The pieces of one veeam-365 API version, imported and ready to use from the loop."""

    def __init__(
        self,
        api_module: str,
        client_class: type,
        unset: Any,
        models: Any,
        operations: dict[str, Callable[..., Any]],
    ) -> None:
        self.api_module = api_module
        self.client_class = client_class
        self.unset = unset
        self.models = models
        self._operations = operations
        self._parameters = {name: _parameter_names(fn) for name, fn in operations.items()}

    def operation(self, name: str) -> Callable[..., Any]:
        """The async function for one operation, e.g. ``operation("job.job_get")``.

        Raises KeyError for an operation this API version does not have.
        """
        return self._operations[name]

    def has_operation(self, name: str) -> bool:
        return name in self._operations

    def accepts(self, name: str, parameter: str) -> bool:
        """Whether an operation takes a parameter — v8 collections take limit/offset."""
        return parameter in self._parameters.get(name, ())


def _parameter_names(fn: Callable[..., Any]) -> frozenset[str]:
    """The names an operation function accepts.

    Read from the code object rather than inspect.signature, which evaluates annotations —
    and on Python 3.14, where annotations are lazy, the generated modules that refer to
    ``Unset`` without importing it raise NameError when they are finally evaluated.
    """
    code = fn.__code__
    return frozenset(code.co_varnames[: code.co_argcount + code.co_kwonlyargcount])


def _import_api_module(package: str, module_path: str):
    """Import a generated API module the way VeeamClient does.

    Some generated v8 operation modules refer to ``Unset`` without importing it, which the
    SDK works around by putting it in builtins for the duration of the import.
    """
    from veeam_365.client import _import_with_unset_patch

    try:
        return _import_with_unset_patch(module_path, package)
    except NameError:
        # The SDK removes builtins.Unset again after every import, including ones running
        # concurrently on the event loop for another entry. Losing that race is harmless
        # to retry.
        return _import_with_unset_patch(module_path, package)


def load_sdk(api_module: str) -> VeeamSdk:
    """Import and patch everything needed for one API version. Blocking: run in an executor.

    Raises ImportError when the installed veeam-365 lacks the version entirely.
    """
    # Imported here so the SDK exception classes are loaded in the executor too
    importlib.import_module("veeam_365.exceptions")
    client_module = importlib.import_module("veeam_365.client")

    package = f"veeam_365.{api_module}"
    types_module = importlib.import_module(f"{package}.types")
    importlib.import_module(f"{package}.client")
    # Importing the package imports every model module eagerly
    models = importlib.import_module(f"{package}.models")

    patched = patch_models(f"{package}.models", types_module.UNSET, sys.modules)
    _LOGGER.debug("Patched %d %s model modules to tolerate null values", patched, api_module)

    operations: dict[str, Callable[..., Any]] = {}
    for name in AUTH_OPERATIONS + OPERATIONS:
        try:
            module = _import_api_module(package, f"{package}.api.{name}")
        except ImportError as err:
            _LOGGER.debug("%s has no %s operation: %r", api_module, name, err)
            continue
        operations[name] = module.asyncio

    return VeeamSdk(api_module, client_module.VeeamClient, types_module.UNSET, models, operations)


def ssl_context(verify_ssl: bool) -> ssl.SSLContext:
    """Home Assistant's shared SSL context, verifying or not.

    Passed to the SDK instead of a bool: httpx builds a fresh context from a bool, loading
    the CA bundle from disk each time, which blocks the event loop.
    """
    return get_default_context() if verify_ssl else get_default_no_verify_context()


def create_client(sdk: VeeamSdk, data: Mapping[str, Any]) -> Any:
    """A VeeamClient for a config entry's (or a flow's) connection details."""
    return sdk.client_class(
        host=f"https://{data[CONF_HOST]}:{data[CONF_PORT]}",
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        api_version=sdk.api_module,
        verify_ssl=ssl_context(data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
        disable_antiforgery_token=True,
        timeout=REQUEST_TIMEOUT,
    )
