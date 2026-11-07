"""Optional adapter credentials stay private and cannot enable a partial setup."""

from __future__ import annotations

import pytest

from custom_components.mennekes_amtron_rtu.device.connection import (
    NetworkConfig,
    SerialConfig,
    validate_tcp_auth_password,
)
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig

_API_TOKEN = "isolated-service-credential-0123456789"  # noqa: S105 - fixture
_ADAPTER_PASSWORD = "T3st!Key"  # noqa: S105 - fixture


@pytest.mark.parametrize("connection_type", ["rtu_over_tcp", "modbus_tcp"])
def test_optional_authentication_flows_through_service_without_repr_leak(connection_type):
    service = ServerConfig.from_dict({
        "token": _API_TOKEN, "connection_type": connection_type,
        "host": "adapter.example", "tcp_authentication": True,
        "tcp_auth_password": _ADAPTER_PASSWORD,
    })
    assert isinstance(service.connection, NetworkConfig)
    assert service.connection.tcp_authentication
    assert service.connection.tcp_auth_password == _ADAPTER_PASSWORD
    assert _ADAPTER_PASSWORD not in repr(service)
    assert _API_TOKEN not in repr(service)
    assert _ADAPTER_PASSWORD not in service.connection.port


@pytest.mark.parametrize("value", [None, False, 123, "", " ", "a b", "a\n", "ümlaut", "1234567890"])
def test_unsupported_password_never_silently_truncates_or_leaks(value):
    with pytest.raises(ValueError, match="password") as error:
        validate_tcp_auth_password(value)
    if isinstance(value, str) and len(value) >= 3:
        assert value not in str(error.value)


@pytest.mark.parametrize("value", [None, "true", 0, 1])
def test_authentication_flag_requires_boolean(value):
    with pytest.raises(ValueError, match="tcp_authentication"):
        NetworkConfig("adapter.example", tcp_authentication=value)


@pytest.mark.parametrize("value", [None, False, 0, _ADAPTER_PASSWORD])
def test_disabled_authentication_cannot_hide_a_stale_or_malformed_credential(value):
    with pytest.raises(ValueError, match="password"):
        NetworkConfig("adapter.example", tcp_auth_password=value)


def test_serial_connection_keeps_inactive_network_options_ignored():
    config = ServerConfig.from_dict({
        "token": _API_TOKEN, "serial_port": "/dev/fake",
        "tcp_authentication": True, "tcp_auth_password": _ADAPTER_PASSWORD,
    })
    assert isinstance(config.connection, SerialConfig)
    assert _ADAPTER_PASSWORD not in repr(config)


def test_manual_network_default_needs_no_authentication_or_credentials():
    config = NetworkConfig("adapter.example")
    assert config.tcp_authentication is False
    assert config.tcp_auth_password == ""
    assert validate_tcp_auth_password("123456789") == "123456789"
