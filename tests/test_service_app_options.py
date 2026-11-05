"""Exercise the actual App choices and defaults at the service boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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


def test_app_offers_readable_choices_and_keeps_published_settings_valid(app_config):
    schema = app_config["schema"]["connection_type"]
    assert schema.startswith("list(")
    assert schema.endswith(")")
    assert schema[5:-1].split("|") == list(CHOICES)


@pytest.mark.parametrize(("selection", "protocol"), CHOICES.items())
def test_every_app_choice_selects_the_expected_transport(app_config, selection, protocol):
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
    "selection", [choice for choice, protocol in CHOICES.items() if protocol != "serial"]
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
