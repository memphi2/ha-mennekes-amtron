"""Exercise the actual App choices and defaults at the service boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from check_service_apparmor import APP_CONNECTIONS

from custom_components.mennekes_amtron_rtu.device.connection import (
    NetworkConfig,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from tests.test_modbus_service import TOKEN

APP = Path(__file__).resolve().parents[1] / "modbus_service"
CHOICES = {
    "USB / RS-485": "serial",
    "LAN: Modbus RTU (TCP)": "rtu_over_tcp",
    "LAN: Modbus TCP": "modbus_tcp",
}
STANDALONE_CHOICES = {
    "serial": "serial",
    "rtu_over_tcp": "rtu_over_tcp",
    "modbus_tcp": "modbus_tcp",
}


@pytest.fixture
def app_config():
    return yaml.safe_load((APP / "config.yaml").read_text())


def test_app_shows_host_even_before_a_network_address_has_been_entered(app_config):
    # Frontend shows optional fields when present in options, including "".
    # Supervisor merges new defaults into saved options on App updates as well.
    assert app_config["options"]["host"] == ""
    assert app_config["schema"]["host"] == "str?"
    assert app_config["options"]["connection_type"] == "USB / RS-485"
    config = ServerConfig.from_dict({
        **app_config["options"], "token": TOKEN, "serial_port": "/dev/fake"
    })
    assert config.connection == SerialConfig("/dev/fake")


def test_native_app_form_exposes_tcp_login_and_password_by_default(app_config):
    # Supervisor merges defaults into existing installations, and its form
    # hides optional schema entries that have no option value by default.
    assert app_config["options"]["tcp_authentication"] is False
    assert app_config["options"]["tcp_auth_password"] == ""
    assert app_config["schema"]["tcp_authentication"] == "bool?"
    assert app_config["schema"]["tcp_auth_password"] == "password?"  # noqa: S105 - schema type
    for connection_type in ("LAN: Modbus RTU (TCP)", "LAN: Modbus TCP"):
        config = ServerConfig.from_dict({
            **app_config["options"], "token": TOKEN,
            "connection_type": connection_type, "host": "gateway.local",
        })
        assert config.connection.tcp_authentication is False
        assert config.connection.tcp_auth_password == ""


def test_added_tcp_defaults_preserve_existing_login_on_supervisor_upgrade(app_config):
    # Supervisor 2026.11.0 merges user options over new app defaults.
    existing = {
        "connection_type": "LAN: Modbus TCP", "host": "gateway.local",
        "tcp_authentication": True, "tcp_auth_password": "Demo123",
    }
    config = ServerConfig.from_dict({**app_config["options"], **existing, "token": TOKEN})
    assert config.connection.tcp_authentication is True
    assert config.connection.tcp_auth_password == "Demo123"  # noqa: S105 - fixture


def test_app_and_standalone_default_to_300_seconds_between_state_changes(app_config):
    key = "state_change_interval_seconds"
    assert app_config["options"][key] == 300
    assert app_config["schema"][key] == "int(0,3600)"
    for options in ({}, app_config["options"]):
        config = ServerConfig.from_dict({
            **options, "token": TOKEN, "serial_port": "/dev/fake",
        })
        assert config.state_change_interval_seconds == 300


@pytest.mark.parametrize("seconds", [0, 1, 175, 300, 3600])
def test_service_retains_the_configured_state_change_interval(app_config, seconds):
    config = ServerConfig.from_dict({
        **app_config["options"],
        "token": TOKEN,
        "serial_port": "/dev/fake",
        "state_change_interval_seconds": seconds,
    })
    assert config.state_change_interval_seconds == seconds


@pytest.mark.parametrize(
    "seconds", [None, True, -1, 3601, 1.5, 300.0, "300", [], {}, float("nan"), float("inf")]
)
def test_service_rejects_invalid_state_change_intervals(app_config, seconds):
    with pytest.raises(ValueError, match="state_change_interval_seconds"):
        ServerConfig.from_dict({
            **app_config["options"],
            "token": TOKEN,
            "serial_port": "/dev/fake",
            "state_change_interval_seconds": seconds,
        })


def test_app_offers_exactly_three_readable_choices_without_duplicate_transports(app_config):
    schema = app_config["schema"]["connection_type"]
    assert schema.startswith("list(")
    assert schema.endswith(")")
    assert schema[5:-1].split("|") == list(CHOICES)
    assert len(set(CHOICES.values())) == 3
    assert {protocol: label for label, protocol in CHOICES.items()} == APP_CONNECTIONS


@pytest.mark.parametrize(("selection", "protocol"), (CHOICES | STANDALONE_CHOICES).items())
def test_app_and_standalone_choices_select_the_expected_transport(app_config, selection, protocol):
    options = {
        **app_config["options"],
        "connection_type": selection,
        "token": TOKEN,
    }
    if protocol == "serial":
        options["serial_port"] = "/dev/fake"
        expected = SerialConfig("/dev/fake")
    else:
        options.update(host="Gateway.Local", tcp_port=1502)
        expected = NetworkConfig(
            "gateway.local", tcp_port=1502, connection_type=protocol
        )
    config = ServerConfig.from_dict(options)
    assert config.connection == expected
    assert config.port == 8765


@pytest.mark.parametrize(
    "selection", [choice for choice, protocol in (CHOICES | STANDALONE_CHOICES).items() if protocol != "serial"]
)
@pytest.mark.parametrize("host", [None, "", " ", "http://gateway.local", "gateway.local:502"])
def test_lan_choices_require_an_address_even_when_usb_fields_are_valid(
    app_config, selection, host
):
    with pytest.raises(ValueError, match="host"):
        ServerConfig.from_dict({
            **app_config["options"],
            "token": TOKEN,
            "serial_port": "/dev/fake",
            "connection_type": selection,
            "host": host,
        })


@pytest.mark.parametrize(
    "selection",
    [None, [], {}, True, "", "LAN", "usb / rs-485", "LAN: Modbus TCP ", "Modbus TCP"],
)
def test_invalid_app_choices_never_fall_back_to_usb(app_config, selection):
    with pytest.raises(ValueError, match="connection_type"):
        ServerConfig.from_dict({
            **app_config["options"],
            "token": TOKEN,
            "serial_port": "/dev/fake",
            "connection_type": selection,
        })
