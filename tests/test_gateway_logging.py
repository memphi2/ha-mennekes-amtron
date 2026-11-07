"""Adapter setup logs explain failed phases without exposing private data."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from custom_components.mennekes_amtron_rtu.device import waveshare_gateway as gw
from tests.test_waveshare_gateway import ADDRESS, PASSWORD, Adapter


def test_setup_logs_apply_readback_and_commit_without_private_values(monkeypatch, caplog):
    adapter = Adapter(monkeypatch)
    caplog.set_level(logging.INFO, logger=gw.__name__)

    async def run():
        async with await gw.configure_gateway(
            ADDRESS, gw.GatewaySettings(tcp_authentication=True), PASSWORD,
        ) as receipt:
            await receipt.commit()

    asyncio.run(run())
    messages = [record.getMessage() for record in caplog.records if record.name == gw.__name__]
    assert messages == [
        "LAN adapter inspection: reading management settings",
        "LAN adapter detected: RS485 TO POE ETH (B) (firmware 1.523); automatic setup available: True",
        "LAN adapter setup: checking existing authentication",
        "LAN adapter setup: applying selected settings and passive profile",
        "LAN adapter setup: changed settings confirmed by readback",
        "LAN adapter setup: checking connection after settings verification",
        "LAN adapter setup verified; waiting for connection settings to be saved",
        "LAN adapter setup committed",
    ]
    for private in (PASSWORD, ADDRESS, adapter.native.hex(), "02:00:00:00:00:01"):
        assert private not in caplog.text


@pytest.mark.parametrize("reason", ["gateway_authentication_failed", "secret-error-text"])
def test_failed_auth_logs_only_known_reason_without_exception_text(monkeypatch, caplog, reason):
    adapter = Adapter(monkeypatch)
    caplog.set_level(logging.DEBUG, logger=gw.__name__)
    monkeypatch.setattr(gw, "_verify_auth", AsyncMock(side_effect=gw.GatewayError(reason)))

    async def run():
        with pytest.raises(gw.GatewayError):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(tcp_authentication=True), PASSWORD)

    asyncio.run(run())
    safe_reason = reason if reason == "gateway_authentication_failed" else "unclassified"
    assert f"initial authentication failed: GatewayError ({safe_reason})" in caplog.text
    assert "secret-error-text" not in caplog.text
    assert PASSWORD not in caplog.text
    assert adapter.writes == []


def test_original_write_failure_is_visible_even_if_rollback_cannot_be_confirmed(monkeypatch, caplog):
    Adapter(monkeypatch)
    caplog.set_level(logging.DEBUG, logger=gw.__name__)
    monkeypatch.setattr(gw, "_send_parameters", AsyncMock(side_effect=OSError(f"private native packet {PASSWORD}")))

    async def run():
        with pytest.raises(gw.GatewayRollbackError):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(tcp_authentication=True), PASSWORD)

    asyncio.run(run())
    assert "parameter write failed: OSError (unclassified)" in caplog.text
    assert "rollback failed: GatewayRollbackError (gateway_rollback_failed)" in caplog.text
    assert "private native packet" not in caplog.text
    assert PASSWORD not in caplog.text


def test_failed_detection_does_not_log_arbitrary_host_or_exception_text(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=gw.__name__)
    monkeypatch.setattr(gw, "_resolve_address", AsyncMock(side_effect=ValueError("private invalid address")))

    async def run():
        with pytest.raises(ValueError, match="private invalid address"):
            await gw.inspect_gateway("secret.invalid")

    asyncio.run(run())
    assert "inspection failed: ValueError (unclassified)" in caplog.text
    assert "secret.invalid" not in caplog.text
    assert "private invalid address" not in caplog.text


def test_uncommitted_setup_logs_verified_restoration(monkeypatch, caplog):
    adapter = Adapter(monkeypatch)
    caplog.set_level(logging.DEBUG, logger=gw.__name__)

    async def run():
        async with await gw.configure_gateway(
            ADDRESS, gw.GatewaySettings(tcp_authentication=True), PASSWORD,
        ):
            pass

    asyncio.run(run())
    assert "rollback: restoring changes from this setup" in caplog.text
    assert "rollback verified" in caplog.text
    assert "setup committed" not in caplog.text
    assert len(adapter.writes) == 2
    assert PASSWORD not in caplog.text
