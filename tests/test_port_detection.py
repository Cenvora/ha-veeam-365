"""Tests for telling the user which REST API port actually answered.

The REST API service listens on 4443 by default but the port is configurable in the console,
so a failed connection is often the wrong port rather than a wrong host or a closed firewall.
The probing itself lives in veeam_365.discovery; what matters here is that a wrong port
produces port-specific advice instead of a bare "cannot connect".

config_flow.py imports Home Assistant, so the helper is lifted out with ast and run against
a stubbed veeam_365.discovery.
"""

import ast
import asyncio
import json
from pathlib import Path
import types

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_365"
CONFIG_FLOW_PATH = COMPONENT / "config_flow.py"

SUPPORTED = {"8": "v8", "7": "v7"}


def _extract(name):
    tree = ast.parse(CONFIG_FLOW_PATH.read_text(encoding="utf-8"))
    return next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def _namespace():
    return {
        "API_VERSIONS": SUPPORTED,
        "CONF_HOST": "host",
        "CONF_VERIFY_SSL": "verify_ssl",
        "DEFAULT_VERIFY_SSL": True,
        "_LOGGER": types.SimpleNamespace(debug=lambda *a, **k: None),
        "Any": object,
        "HomeAssistant": object,
        "get_async_client": lambda hass, verify_ssl=True: ("shared-client", verify_ssl),
    }


def load_finder(endpoint=None, raises=None, record=None, ports=(4443,)):
    """Load async_find_working_port with veeam_365.discovery stubbed out."""

    async def detect_rest_api(host, *, ports=None, versions=None, verify_ssl=True, client=None):
        if record is not None:
            record.update(
                host=host, ports=ports, versions=versions, verify_ssl=verify_ssl, client=client
            )
        if raises is not None:
            raise raises
        return endpoint

    namespace = _namespace()
    namespace["detect_rest_api"] = detect_rest_api
    namespace["DEFAULT_PORTS"] = ports
    exec(
        compile(
            ast.Module(body=[_extract("async_find_working_port")], type_ignores=[]),
            str(CONFIG_FLOW_PATH),
            "exec",
        ),
        namespace,
    )
    finder = namespace["async_find_working_port"]

    async def call(data, configured_port):
        return await finder(None, data, configured_port)

    return call


class Endpoint:
    def __init__(self, port, api_version="v8"):
        self.port = port
        self.api_version = api_version


def data(**overrides):
    entry = {"host": "vb365.example.com", "verify_ssl": True}
    entry.update(overrides)
    return entry


def test_reports_the_port_that_answered():
    finder = load_finder(endpoint=Endpoint(4443))

    assert asyncio.run(finder(data(), 443)) == 4443


def test_only_the_other_ports_are_probed():
    """Re-probing the port that just failed to connect would waste a round trip."""
    record = {}
    finder = load_finder(endpoint=Endpoint(4443), record=record, ports=(4443, 443))

    asyncio.run(finder(data(), 443))

    assert record["ports"] == [4443], "should skip the port already known to fail"
    assert record["host"] == "vb365.example.com"
    assert record["versions"] == list(SUPPORTED.values())


def test_probing_is_skipped_when_the_configured_port_is_the_only_candidate():
    """Nothing left to suggest, so the generic connection error stands."""
    record = {}
    finder = load_finder(endpoint=Endpoint(4443), record=record, ports=(4443,))

    assert asyncio.run(finder(data(), 4443)) is None
    assert record == {}, "no probe should be made when there is nothing else to try"


def test_verify_ssl_is_passed_through():
    record = {}
    finder = load_finder(endpoint=Endpoint(4443), record=record)

    asyncio.run(finder(data(verify_ssl=False), 443))

    assert record["verify_ssl"] is False
    assert record["client"] == ("shared-client", False)


def test_no_answer_gives_no_advice():
    """Nothing answering means the problem is not the port."""
    finder = load_finder(endpoint=None)

    assert asyncio.run(finder(data(), 443)) is None


def test_a_failing_probe_is_not_fatal():
    """The probe is a nicety; it must not replace the real connection error."""
    finder = load_finder(raises=OSError("no route to host"))

    assert asyncio.run(finder(data(), 443)) is None


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_wrong_port_is_caught_before_connection_error():
    """WrongPortError subclasses CannotConnect, so ordering decides which wins."""
    content = CONFIG_FLOW_PATH.read_text(encoding="utf-8")

    assert "class WrongPortError(CannotConnect)" in content

    # One shared handler turns exceptions into form errors, and all four flows use it
    assert content.count('errors["base"] = "wrong_port"') == 1
    assert (
        content.count("await _async_validate(self.hass") == 4
    ), "all four flows (user, reconfigure, reauth, options) should surface it"

    handler = content[content.index("async def _async_validate") :]
    handler = handler[: handler.index("\nclass ")]
    assert handler.index("except WrongPortError") < handler.index(
        "except CannotConnect"
    ), "CannotConnect is caught before WrongPortError, which would swallow the port advice"


def test_every_form_supplies_the_placeholder():
    """A message referencing {wrong_port} with no placeholder renders broken."""
    content = CONFIG_FLOW_PATH.read_text(encoding="utf-8")

    assert content.count('"wrong_port": str(wrong_port or "")') == 4


def test_authentication_failures_do_not_trigger_a_port_probe():
    """Bad credentials prove the port is right; probing would only slow the form down."""
    content = CONFIG_FLOW_PATH.read_text(encoding="utf-8")

    validate = content[content.index("async def validate_input") :]
    handler = validate[validate.index("except VeeamAuthenticationError") :]
    handler = handler[: handler.index("except (TimeoutError")]

    assert "_raise_wrong_port_if_answering" not in handler


def test_error_text_names_the_default_port():
    """The advice is only useful if it says what the port should normally be."""
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        for section in ("config", "options"):
            message = data[section]["error"]["wrong_port"]
            assert "{wrong_port}" in message
            assert "4443" in message


@pytest.mark.parametrize("section", ["config", "options"])
def test_both_flows_can_render_the_error(section):
    """The options flow resolves errors from its own section, not the config one."""
    data = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))

    assert "wrong_port" in data[section]["error"]
