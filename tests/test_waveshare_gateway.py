"""Explicit adapter setup against synthetic, in-memory vendor parameter images.

No real adapter fixture, address, identifier or credential is used. Every socket
operation is replaced; failure and cancellation can be controlled precisely.
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.mennekes_amtron_rtu.device import waveshare_gateway as gw
from custom_components.mennekes_amtron_rtu.device.waveshare_auth import (
    WaveshareAuthenticationError,
    WaveshareAuthenticationRejectedError,
)

ADDRESS = "192.0.2.24"
PASSWORD = "Fixture7"  # noqa: S105 - Synthetic password, unrelated to hardware.
MODEL = "RS485 TO POE ETH (B)"


def parameters():
    """Construct a fictitious valid image, with distinctive reserved bytes."""
    value = bytearray((index * 17 + 41) % 256 for index in range(167))
    value[:4] = bytes((192, 0, 2, 24))
    value[0x10:0x12] = (502).to_bytes(2, "big")
    value[0x14] = 0
    value[0x1F:0x25] = b"\x02\0\0\0\0\x01"
    value[0x25] = 9  # Documented baud-table entry 57600.
    value[0x30] = 0
    value[0x34] = 0
    value[0x37] = 0
    value[0x39] = 0
    value[0x3B] = 0
    value[0x3C] = 0
    value[0x62:0x64] = (80).to_bytes(2, "big")
    value[0x67] = 140  # 1.(383 + 140) = 1.523.
    value[0x68] = 0xBE
    value[0x6E] = 0x10  # P2P bit is deliberately present and must be preserved.
    value[0x70] = 6
    value[0x71] = 0
    value[0x72] = 1  # Two stop bits, auth disabled.
    value[0x73:0x75] = b"\x09\x04"
    value[0x79:0x7B] = b"\x0a\x04"
    value[0x7F:] = bytes(40)
    return bytes(value)


class Adapter:
    def __init__(self, monkeypatch):
        self.native = parameters()
        self.writes = []
        self.reads = 0
        self.auth = []
        self.closed = []
        self.model = MODEL
        self.after_send = None
        self.before_read = None
        self.failure = None
        self.real_sleep = asyncio.sleep
        monkeypatch.setattr(gw, "_query_native", self.read)
        monkeypatch.setattr(gw, "_send_parameters", self.send)
        monkeypatch.setattr(gw, "_verify_model", self.identify)
        monkeypatch.setattr(gw, "async_open_authenticated_socket", self.authenticate)
        monkeypatch.setattr(gw.asyncio, "sleep", self.yield_once)
        monkeypatch.setattr(gw, "_APPLY_TIMEOUT", 0.03)
        monkeypatch.setattr(gw, "_ROLLBACK_TIMEOUT", 0.3)

    async def yield_once(self, _delay):
        await self.real_sleep(0)

    async def read(self, address):
        assert address == ADDRESS
        self.reads += 1
        if self.before_read is not None:
            await self.before_read(self)
        return gw._snapshot(address, self.native)

    async def identify(self, _snapshot):
        return self.model

    async def send(self, before, native):
        assert before.address == ADDRESS
        self.writes.append(native)
        self.native = native
        if self.after_send is not None:
            await self.after_send(self)

    async def authenticate(self, host, port, password, *, timeout_seconds):
        assert host == ADDRESS
        assert timeout_seconds == 8
        assert port == int.from_bytes(self.native[0x10:0x12], "big")
        assert self.native[0x72] & 2
        self.auth.append((port, password))
        if password != PASSWORD or self.failure == "auth":
            raise WaveshareAuthenticationRejectedError
        sock = MagicMock()
        sock.close.side_effect = lambda: self.closed.append(port)
        return sock

    def change(self, offset, value):
        native = bytearray(self.native)
        native[offset] = value
        self.native = bytes(native)


@pytest.fixture
def adapter(monkeypatch):
    return Adapter(monkeypatch)


@pytest.mark.parametrize("settings", [
    {"baudrate": True}, {"baudrate": 56000}, {"baudrate": 115200},
    {"frame": "8n1"}, {"frame": "7e1"}, {"connection_type": "serial"},
    {"tcp_authentication": 1}, {"tcp_port": True}, {"tcp_port": 0},
    {"tcp_port": 65536}, {"tcp_port": "502"},
])
def test_reject_invalid_explicit_settings(settings):
    with pytest.raises(ValueError, match=r"Unsupported|Adapter|tcp_port"):
        gw.GatewaySettings(**settings)


@pytest.mark.parametrize(("offset", "new"), [
    (0, 193), (0x67, 139), (0x68, 0xBF), (0x70, 5), (0x14, 4),
    (0x3C, 2), (0x25, 14), (0x30, 5), (0x3B, 4), (0x73, 8),
    (0x74, 3), (0x79, 9), (0x7A, 3),
])
def test_unverified_native_profiles_are_rejected(offset, new):
    native = bytearray(parameters())
    native[offset] = new
    with pytest.raises(gw.GatewayUnsupportedError):
        gw._snapshot(ADDRESS, bytes(native))


@pytest.mark.parametrize("kind", ["short", "long", "zero_id", "ff_id", "zero_port", "zero_web", "same_port"])
def test_malformed_native_image_is_rejected(kind):
    native = bytearray(parameters())
    if kind == "short":
        native.pop()
    elif kind == "long":
        native.append(0)
    elif kind in ("zero_id", "ff_id"):
        native[0x1F:0x25] = bytes([255 if kind == "ff_id" else 0]) * 6
    elif kind == "zero_port":
        native[0x10:0x12] = bytes(2)
    elif kind == "zero_web":
        native[0x62:0x64] = bytes(2)
    else:
        native[0x62:0x64] = native[0x10:0x12]
    with pytest.raises(gw.GatewayUnsupportedError):
        gw._snapshot(ADDRESS, bytes(native))


@pytest.mark.parametrize("model", [MODEL, "RS485 TO ETH (B)", "RS485 TO ETH (C)"])
def test_inspection_is_read_only_and_reports_recognized_model(adapter, model):
    async def run():
        adapter.model = model
        observed = await gw.inspect_gateway(ADDRESS)
        assert observed.model == model
        assert observed.firmware == "1.523"
        assert observed.identifier == "02:00:00:00:00:01"
        assert observed.frame == "8n2"
        assert observed.baudrate == 57600
        assert observed.connection_type == "rtu_over_tcp"
        assert observed.can_configure
        assert not observed.tcp_authentication
        assert "_native" not in repr(observed)
        assert adapter.reads == 2
        assert adapter.writes == []
        assert adapter.auth == []

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("offset", [0x24, 0x25, 0x66])
def test_inspect_rejects_identity_or_settings_race(adapter, offset):
    async def run():
        async def concurrent_change(device):
            if device.reads == 2:
                device.change(offset, device.native[offset] ^ 1)

        adapter.before_read = concurrent_change
        with pytest.raises(gw.GatewayConflictError):
            await gw.inspect_gateway(ADDRESS)
        assert adapter.writes == []

    asyncio.run(asyncio.wait_for(run(), 3))


def test_inspect_allows_only_documented_live_fields_to_change(adapter):
    async def run():
        async def traffic(device):
            device.change(0x3D, device.native[0x3D] ^ 1)
            device.change(0x75, (device.native[0x75] + 1) % 256)
            device.change(0x7E, (device.native[0x7E] + 1) % 256)

        adapter.before_read = traffic
        await gw.inspect_gateway(ADDRESS)
        assert adapter.writes == []

    asyncio.run(asyncio.wait_for(run(), 3))


def test_explicit_configuration_changes_only_selected_fields(adapter):
    async def run():
        before = adapter.native
        settings = gw.GatewaySettings(baudrate=9600, frame="8e1", connection_type="modbus_tcp", tcp_port=1502)
        async with await gw.configure_gateway(ADDRESS, settings) as receipt:
            assert receipt.after.model == MODEL
            assert receipt.after.baudrate == 9600
            assert receipt.after.frame == "8e1"
            assert receipt.after.connection_type == "modbus_tcp"
            assert receipt.after.tcp_port == 1502
            assert not receipt.rollback_verified
            changed = {index for index, value in enumerate(adapter.native) if value != before[index]}
            assert changed == {0x10, 0x11, 0x25, 0x30, 0x3C, 0x72}
            assert adapter.native[0x72] == before[0x72] & ~1
            await receipt.commit()
            await receipt.commit()
        assert len(adapter.writes) == 1
        assert adapter.auth == []

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("baud", gw.GATEWAY_BAUDRATES)
def test_each_exposed_baud_uses_documented_mapping(adapter, baud):
    async def run():
        expected = {9600: 4, 14400: 5, 19200: 6, 28800: 7, 38400: 8, 57600: 9}
        async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=baud)) as receipt:
            assert adapter.native[0x25] == expected[baud]
            await receipt.commit()

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize(("frame", "parity", "stop"), [("8n2", 0, 1), ("8e1", 2, 0), ("8o1", 1, 0)])
def test_frame_preserves_auth_and_unrelated_configuration(adapter, frame, parity, stop):
    async def run():
        adapter.change(0x72, adapter.native[0x72] | 2)
        before = adapter.native
        async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings(frame=frame), PASSWORD) as receipt:
            assert adapter.native[0x30] == parity
            assert adapter.native[0x3B] == 0
            assert adapter.native[0x72] == (before[0x72] & ~1) | stop
            await receipt.commit()
        assert len(adapter.auth) == 2
        assert len(adapter.closed) == 2

    asyncio.run(asyncio.wait_for(run(), 3))


def test_enabling_auth_uses_existing_key_without_changing_password_bytes(adapter):
    async def run():
        before = adapter.native
        async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings(tcp_authentication=True), PASSWORD) as receipt:
            assert receipt.after.tcp_authentication
            assert adapter.auth == [(502, PASSWORD)]
            assert adapter.closed == [502]
            assert adapter.native[:0x72] == before[:0x72]
            assert adapter.native[0x73:] == before[0x73:]
            assert adapter.native[0x72] == before[0x72] | 2
            await receipt.commit()

    asyncio.run(asyncio.wait_for(run(), 3))


def test_disabling_auth_verifies_existing_auth_before_mutation(adapter):
    async def run():
        adapter.change(0x72, adapter.native[0x72] | 2)
        async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings(tcp_authentication=False), PASSWORD) as receipt:
            assert adapter.auth == [(502, PASSWORD)]
            assert not receipt.after.tcp_authentication
            await receipt.commit()

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("password", ["", "invalid", "morethan9x", "ümlaut"])
def test_current_auth_cannot_be_disabled_without_valid_key(adapter, password):
    async def run():
        adapter.change(0x72, adapter.native[0x72] | 2)
        before = adapter.native
        with pytest.raises((ValueError, gw.GatewayError)):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(tcp_authentication=False), password)
        assert adapter.native == before
        assert adapter.writes == []

    asyncio.run(asyncio.wait_for(run(), 3))


def test_bad_enable_key_restores_auth_off(adapter):
    async def run():
        before = adapter.native
        with pytest.raises(gw.GatewayError, match="gateway_authentication_failed"):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(tcp_authentication=True), "incorrect")
        assert adapter.native == before
        assert len(adapter.writes) == 2

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("existing_auth", [False, True])
@pytest.mark.parametrize(("error", "reason"), [
    (WaveshareAuthenticationRejectedError("TCP gateway rejected authentication"), "gateway_authentication_failed"),
    (WaveshareAuthenticationError("TCP gateway authentication timed out"), "gateway_unreachable"),
    (WaveshareAuthenticationError("TCP gateway sent an invalid authentication frame"), "gateway_unreachable"),
])
def test_setup_distinguishes_password_rejection_from_unconfirmed_login(adapter, monkeypatch, existing_auth, error, reason):
    async def run():
        if existing_auth:
            adapter.change(0x72, adapter.native[0x72] | 2)
        before = adapter.native
        login = AsyncMock(side_effect=error)
        monkeypatch.setattr(gw, "async_open_authenticated_socket", login)
        with pytest.raises(gw.GatewayError) as caught:
            await gw.configure_gateway(
                ADDRESS, gw.GatewaySettings(tcp_authentication=not existing_auth), PASSWORD,
            )
        assert caught.value.reason == reason
        assert str(caught.value) == reason
        assert adapter.native == before
        # An enabled login is verified before any mutation; a failed new login
        # is restored to its original disabled setting before returning.
        assert len(adapter.writes) == (0 if existing_auth else 2)
        assert login.await_count == 1

    asyncio.run(asyncio.wait_for(run(), 3))


def test_auth_verification_uses_new_port_then_restored_port(adapter):
    async def run():
        adapter.change(0x72, adapter.native[0x72] | 2)
        before = adapter.native
        async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings(tcp_port=1502), PASSWORD) as receipt:
            assert adapter.auth == [(502, PASSWORD), (1502, PASSWORD)]
        assert receipt.rollback_verified
        assert adapter.native == before
        assert adapter.auth[-1] == (502, PASSWORD)

    asyncio.run(asyncio.wait_for(run(), 3))


def test_context_error_restores_owned_fields_preserving_unrelated_edits(adapter):
    async def run():
        before = adapter.native
        with pytest.raises(RuntimeError, match="caller save failed"):  # noqa: PT012 - Exercise context cleanup.
            async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600, frame="8e1")) as receipt:
                adapter.change(0x66, 31)
                adapter.change(0x6E, adapter.native[0x6E] ^ 0x40)
                raise RuntimeError("caller save failed")
        expected = bytearray(before)
        expected[0x66] = 31
        expected[0x6E] ^= 0x40
        assert adapter.native == bytes(expected)
        assert receipt.rollback_verified
        await receipt.rollback()
        assert len(adapter.writes) == 2

    asyncio.run(asyncio.wait_for(run(), 3))


def test_unchanged_selection_does_not_claim_another_actors_edit(adapter):
    async def run():
        async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600, frame="8n2")) as receipt:
            adapter.change(0x30, 1)
        assert receipt.rollback_verified
        assert adapter.native[0x25] == 9
        assert adapter.native[0x30] == 1

    asyncio.run(asyncio.wait_for(run(), 3))


def test_owned_field_conflict_is_not_overwritten(adapter):
    async def run():
        receipt = await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600))
        adapter.change(0x25, 6)
        with pytest.raises(gw.GatewayRollbackError, match="gateway_rollback_failed"):
            await receipt.rollback()
        assert not receipt.rollback_verified
        assert adapter.native[0x25] == 6
        assert len(adapter.writes) == 1

    asyncio.run(asyncio.wait_for(run(), 3))


def test_receipt_commit_conflict_triggers_context_rollback(adapter):
    async def run():
        before = adapter.native
        with pytest.raises(gw.GatewayConflictError):  # noqa: PT012 - Exercise context cleanup.
            async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600)) as receipt:
                adapter.change(0x66, 31)
                await receipt.commit()
        assert receipt.rollback_verified
        assert adapter.native[0x25] == before[0x25]
        assert adapter.native[0x66] == 31

    asyncio.run(asyncio.wait_for(run(), 3))


def test_expired_inspection_cannot_change_new_device_or_configuration(adapter):
    async def run():
        inspected = await gw.inspect_gateway(ADDRESS)
        adapter.change(0x66, 31)
        with pytest.raises(gw.GatewayConflictError):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600), expected=inspected)
        assert adapter.writes == []

    asyncio.run(asyncio.wait_for(run(), 3))


def test_expired_inspection_cannot_change_different_model(adapter):
    async def run():
        inspected = await gw.inspect_gateway(ADDRESS)
        adapter.model = "RS485 TO ETH (C)"
        with pytest.raises(gw.GatewayConflictError):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600), expected=inspected)
        assert adapter.writes == []

    asyncio.run(asyncio.wait_for(run(), 3))


def test_management_password_profile_is_readable_but_cannot_be_changed(adapter):
    async def run():
        adapter.change(0x6E, adapter.native[0x6E] | 4)
        assert not (await gw.inspect_gateway(ADDRESS)).can_configure
        with pytest.raises(gw.GatewayUnsupportedError):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600))
        assert adapter.writes == []

    asyncio.run(asyncio.wait_for(run(), 3))


def test_noop_receipt_never_sends_configuration(adapter):
    async def run():
        async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings()) as receipt:
            assert receipt.before == receipt.after
        assert receipt.rollback_verified
        assert adapter.writes == []

    asyncio.run(asyncio.wait_for(run(), 3))


def test_requested_web_port_is_rejected_before_write(adapter):
    async def run():
        with pytest.raises(ValueError, match="ports must differ"):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(tcp_port=80))
        assert adapter.writes == []

    asyncio.run(asyncio.wait_for(run(), 3))


def test_changed_identity_after_apply_is_not_written_again(adapter):
    async def run():
        async def replace_device(device):
            device.change(0x24, 19)

        adapter.after_send = replace_device
        with pytest.raises(gw.GatewayRollbackError):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600))
        assert len(adapter.writes) == 1

    asyncio.run(asyncio.wait_for(run(), 3))


def test_unconfirmed_apply_restores_after_temporary_reboot_but_remains_uncertain(adapter):
    async def run():
        before = adapter.native
        unavailable = 0

        async def send_lost(device):
            if len(device.writes) == 1:
                raise OSError("synthetic lost ACK")

        async def rebooting(device):
            nonlocal unavailable
            if device.writes and unavailable < 3:
                unavailable += 1
                raise gw.GatewayError

        adapter.after_send = send_lost
        adapter.before_read = rebooting
        with pytest.raises(gw.GatewayRollbackError, match="gateway_rollback_failed"):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600))
        assert adapter.native == before
        assert unavailable == 3
        assert len(adapter.writes) == 2

    asyncio.run(asyncio.wait_for(run(), 3))


def test_partial_combined_flag_apply_restores_each_flag(adapter):
    async def run():
        before = adapter.native

        async def partial_apply(device):
            if len(device.writes) == 1:
                # Device applied auth but not the stop-bit change.
                device.change(0x72, device.native[0x72] | 1)

        adapter.after_send = partial_apply
        with pytest.raises(gw.GatewayRollbackError, match="gateway_rollback_failed"):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(frame="8e1", tcp_authentication=True), PASSWORD)
        assert adapter.native == before
        assert len(adapter.writes) == 2

    asyncio.run(asyncio.wait_for(run(), 3))


def test_cancellation_during_apply_completes_bounded_restore(adapter):
    async def run():
        before = adapter.native
        applied = asyncio.Event()
        restore_started = asyncio.Event()
        restore_allowed = asyncio.Event()

        async def hold_send(device):
            if len(device.writes) == 1:
                applied.set()
                await asyncio.Event().wait()
            else:
                restore_started.set()
                await restore_allowed.wait()

        adapter.after_send = hold_send
        task = asyncio.create_task(gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600)))
        await applied.wait()
        task.cancel()
        await restore_started.wait()
        task.cancel()
        await adapter.real_sleep(0)
        assert not task.done()
        restore_allowed.set()
        with pytest.raises(gw.GatewayRollbackError, match="gateway_rollback_failed"):
            await task
        assert adapter.native == before
        assert len(adapter.writes) == 2
        # Endpoint transaction lock was released exactly once and is reusable.
        async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings()) as receipt:
            await receipt.commit()

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("failure", ["cancel", "timeout"])
def test_unconfirmed_udp_apply_cannot_be_declared_restored_when_it_arrives_late(adapter, monkeypatch, failure):
    async def run():
        before = adapter.native
        queued = []
        sent = asyncio.Event()
        receipts = []
        receipt_type = gw.GatewayReceipt

        class RecordingReceipt(receipt_type):
            def __init__(self, *args):
                super().__init__(*args)
                receipts.append(self)

        async def delayed_delivery(_snapshot, native):
            # A UDP send can return before the adapter processes its datagram.
            queued.append(native)
            sent.set()

        async def unconfirmed_readback(_before, _native):
            if failure == "timeout":
                raise gw.GatewayError("gateway_apply_failed")
            await asyncio.Event().wait()

        monkeypatch.setattr(gw, "GatewayReceipt", RecordingReceipt)
        monkeypatch.setattr(gw, "_send_parameters", delayed_delivery)
        monkeypatch.setattr(gw, "_wait_readback", unconfirmed_readback)
        task = asyncio.create_task(gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600)))
        await sent.wait()
        if failure == "cancel":
            task.cancel()
        with pytest.raises(gw.GatewayRollbackError, match="gateway_rollback_failed"):
            await task
        assert len(queued) == 1
        assert adapter.native == before
        assert not receipts[0].rollback_verified
        # A late original datagram can still change the adapter after the old
        # settings were read during the attempted restoration.
        adapter.native = queued[0]
        assert adapter.native != before
        assert adapter.native[0x25] == 4

    asyncio.run(asyncio.wait_for(run(), 3))


def test_cancellation_after_confirmed_apply_restores_and_propagates_cancellation(adapter):
    async def run():
        before = adapter.native
        applied = asyncio.Event()
        holder = []

        async def configure_and_wait():
            async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600)) as receipt:
                holder.append(receipt)
                applied.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(configure_and_wait())
        await applied.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert holder[0].rollback_verified
        assert adapter.native == before
        assert len(adapter.writes) == 2

    asyncio.run(asyncio.wait_for(run(), 3))


def test_failed_restore_is_distinct_from_original_failure(adapter, monkeypatch):
    async def run():
        async def cannot_read_after_apply(device):
            if device.writes:
                raise gw.GatewayError

        monkeypatch.setattr(gw, "_ROLLBACK_TIMEOUT", 0.01)
        adapter.before_read = cannot_read_after_apply
        with pytest.raises(gw.GatewayRollbackError) as caught:
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600))
        assert str(caught.value) == "gateway_rollback_failed"
        assert PASSWORD not in str(caught.value)
        assert len(adapter.writes) == 1

    asyncio.run(asyncio.wait_for(run(), 3))


def test_concurrent_receipts_serialize_until_commit(adapter):
    async def run():
        first = await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600))
        second_task = asyncio.create_task(gw.configure_gateway(ADDRESS, gw.GatewaySettings(frame="8e1")))
        await adapter.real_sleep(0)
        assert not second_task.done()
        assert len(adapter.writes) == 1
        await first.commit()
        second = await second_task
        await first.rollback()  # Already committed, must not release the second lock.
        assert second._lock.locked()
        await second.rollback()
        assert adapter.native[0x25] == 4
        assert adapter.native[0x30] == 0

    asyncio.run(asyncio.wait_for(run(), 3))


class WebResponse:
    def __init__(self, body, status=200):
        self.status = status
        self.body = body
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def iter_chunked(self, size):
        for index in range(0, len(self.body), size):
            yield self.body[index:index + size]


def fake_web(monkeypatch, body, status=200):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get.return_value = WebResponse(body, status)
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(gw.aiohttp, "ClientSession", factory)
    return session, factory


@pytest.mark.parametrize("model", [MODEL, "RS485 TO ETH (B)", "RS485 TO ETH (C)"])
def test_model_page_identifies_only_official_single_channel_profiles(monkeypatch, model):
    async def run():
        body = f'<h2> {model} </h2><a href="https://www.waveshare.com/">Waveshare</a>'.encode()
        session, factory = fake_web(monkeypatch, body)
        assert await gw._verify_model(gw._snapshot(ADDRESS, parameters())) == model
        session.get.assert_called_once_with(f"http://{ADDRESS}:80/index.html", allow_redirects=False)
        assert factory.call_args.kwargs["trust_env"] is False

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("body", [
    b'<h2>RS485 TO ETH</h2><a href="https://www.waveshare.com/">vendor</a>',
    b'<h2>2-CH RS485 TO ETH (B)</h2><a href="https://www.waveshare.com/">vendor</a>',
    b'<h2>RS232 TO ETH (B)</h2><a href="https://www.waveshare.com/">vendor</a>',
    b'<h2>RS485 TO POE ETH (B)</h2><a href="https://waveshare.com.evil.test/">vendor</a>',
    b"<h2>RS485 TO POE ETH (B)</h2>",
    b'<h2>RS485 TO POE ETH (B)</h2><h2>Other</h2><a href="https://waveshare.com/">vendor</a>',
    b"x" * 65537,
    b'<h2>RS485 TO POE ETH (B)</h2><a href="http://[">invalid</a>',
])
def test_unknown_or_malformed_model_page_rejects_profile(monkeypatch, body):
    async def run():
        fake_web(monkeypatch, body)
        with pytest.raises(gw.GatewayUnsupportedError):
            await gw._verify_model(gw._snapshot(ADDRESS, parameters()))

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("status", [301, 401, 404, 500])
def test_identification_never_follows_redirect_or_login(monkeypatch, status):
    async def run():
        fake_web(monkeypatch, b"", status)
        with pytest.raises(gw.GatewayUnsupportedError):
            await gw._verify_model(gw._snapshot(ADDRESS, parameters()))

    asyncio.run(asyncio.wait_for(run(), 3))


def test_http_failure_is_sanitized(monkeypatch):
    async def run():
        session, _factory = fake_web(monkeypatch, b"")
        session.get.side_effect = aiohttp.ClientError("private request details")
        with pytest.raises(gw.GatewayError, match=r"^gateway_unreachable$"):
            await gw._verify_model(gw._snapshot(ADDRESS, parameters()))

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("host", ["0.0.0.0", "255.255.255.255", "224.0.0.1", "::1", "2001:db8::1"])  # noqa: S104 - Invalid input, never bound.
def test_adapter_management_rejects_non_unicast_ipv4(host):
    async def run():
        with pytest.raises(gw.GatewayUnsupportedError):
            await gw._resolve_address(host)

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("addresses", [[], [ADDRESS, "192.0.2.25"]])
def test_ambiguous_or_empty_dns_cannot_select_an_adapter(monkeypatch, addresses):
    async def run():
        records = [(socket.AF_INET, socket.SOCK_DGRAM, 0, "", (address, 1092)) for address in addresses]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=records))
        with pytest.raises(gw.GatewayUnsupportedError):
            await gw._resolve_address("adapter.example.test")

    asyncio.run(asyncio.wait_for(run(), 3))


def test_dns_resolves_once_to_a_pinned_ipv4(monkeypatch):
    async def run():
        record = (socket.AF_INET, socket.SOCK_DGRAM, 0, "", (ADDRESS, 1092))
        lookup = AsyncMock(return_value=[record, record])
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
        assert await gw._resolve_address("adapter.example.test") == ADDRESS
        lookup.assert_awaited_once()

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("failure", [OSError("private"), TimeoutError])
def test_dns_error_is_sanitized(monkeypatch, failure):
    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(side_effect=failure))
        with pytest.raises(gw.GatewayError, match=r"^gateway_unreachable$"):
            await gw._resolve_address("adapter.example.test")

    asyncio.run(asyncio.wait_for(run(), 3))


def fake_udp(monkeypatch, response):
    sock = MagicMock()
    sock.__enter__.return_value = sock
    factory = MagicMock(return_value=sock)
    loop = asyncio.get_running_loop()
    connect, send, receive = AsyncMock(), AsyncMock(), AsyncMock(return_value=response)
    monkeypatch.setattr(gw.socket, "socket", factory)
    monkeypatch.setattr(loop, "sock_connect", connect)
    monkeypatch.setattr(loop, "sock_sendall", send)
    monkeypatch.setattr(loop, "sock_recv", receive)
    return sock, factory, connect, send, receive


def test_management_read_is_exact_unicast_query_and_closes_socket(monkeypatch):
    async def run():
        sock, factory, connect, send, receive = fake_udp(monkeypatch, b"ZL\x01" + parameters())
        observed = await gw._query_native(ADDRESS)
        assert observed.tcp_port == 502
        factory.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking.assert_called_once_with(False)
        connect.assert_awaited_once_with(sock, (ADDRESS, 1092))
        send.assert_awaited_once_with(sock, b"ZL\x04" + bytes(167))
        receive.assert_awaited_once_with(sock, 171)
        sock.__exit__.assert_called_once()
        sock.setsockopt.assert_not_called()

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("packet", [b"", b"ZL\x04" + parameters(), b"ZL\x01" + parameters()[:-1], b"ZL\x01" + parameters() + b"x"])
def test_management_read_rejects_other_command_or_wrong_length(monkeypatch, packet):
    async def run():
        sock, *_ = fake_udp(monkeypatch, packet)
        with pytest.raises(gw.GatewayUnsupportedError):
            await gw._query_native(ADDRESS)
        sock.__exit__.assert_called_once()

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("failure", [OSError("private"), TimeoutError, asyncio.CancelledError])
def test_management_read_failure_closes_socket(monkeypatch, failure):
    async def run():
        sock, _factory, _connect, _send, receive = fake_udp(monkeypatch, b"")
        receive.side_effect = failure
        error = asyncio.CancelledError if failure is asyncio.CancelledError else gw.GatewayError
        with pytest.raises(error):
            await gw._query_native(ADDRESS)
        sock.__exit__.assert_called_once()

    asyncio.run(asyncio.wait_for(run(), 3))


def test_management_apply_never_uses_password_change_or_broadcast_commands(monkeypatch):
    async def run():
        sock, _factory, connect, send, receive = fake_udp(monkeypatch, b"")
        before = gw._snapshot(ADDRESS, parameters())
        await gw._send_parameters(before, parameters())
        connect.assert_awaited_once_with(sock, (ADDRESS, 1092))
        send.assert_awaited_once_with(sock, b"ZL\x02" + parameters())
        receive.assert_not_awaited()
        sock.__exit__.assert_called_once()

    asyncio.run(asyncio.wait_for(run(), 3))


def test_pre_apply_auth_toctou_recheck_blocks_settings_change(adapter):
    async def run():
        adapter.change(0x72, adapter.native[0x72] | 2)

        async def external_edit_after_auth(device):
            if device.auth:
                device.change(0x25, 6)

        adapter.before_read = external_edit_after_auth
        with pytest.raises(gw.GatewayConflictError):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600), PASSWORD)
        assert adapter.writes == []

    asyncio.run(asyncio.wait_for(run(), 3))


def test_source_expected_identity_is_required_even_with_matching_settings(adapter):
    async def run():
        expected = await gw.inspect_gateway(ADDRESS)
        other = bytearray(expected._native)
        other[0x24] = 99
        expected = replace(expected, _native=bytes(other))
        with pytest.raises(gw.GatewayConflictError):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600), expected=expected)
        assert adapter.writes == []

    asyncio.run(asyncio.wait_for(run(), 3))


def test_matching_preview_can_be_applied_and_committed(adapter):
    async def run():
        preview = await gw.inspect_gateway(ADDRESS)
        async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600), expected=preview) as receipt:
            await receipt.commit()
        assert adapter.native[0x25] == 4
        assert len(adapter.writes) == 1

    asyncio.run(asyncio.wait_for(run(), 3))


def test_changed_firmware_after_write_prevents_unsafe_restore(adapter):
    async def run():
        async def unsupported_profile(device):
            device.change(0x67, 141)

        adapter.after_send = unsupported_profile
        with pytest.raises(gw.GatewayRollbackError):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600))
        assert len(adapter.writes) == 1

    asyncio.run(asyncio.wait_for(run(), 3))


def test_unconfirmed_unapplied_request_remains_uncertain_after_readback(adapter):
    async def run():
        original = adapter.native

        async def dropped_write(device):
            device.native = original

        adapter.after_send = dropped_write
        with pytest.raises(gw.GatewayRollbackError, match="gateway_rollback_failed"):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600))
        assert adapter.native == original
        assert len(adapter.writes) == 1

    asyncio.run(asyncio.wait_for(run(), 3))


def test_simultaneous_rollback_requests_share_one_restoration(adapter):
    async def run():
        original = adapter.native
        receipt = await gw.configure_gateway(ADDRESS, gw.GatewaySettings(baudrate=9600))
        await asyncio.gather(receipt.rollback(), receipt.rollback())
        assert receipt.rollback_verified
        assert adapter.native == original
        assert len(adapter.writes) == 2

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("protocol", ["rtu_over_tcp", "modbus_tcp"])
@pytest.mark.parametrize("workmode", [0, 1, 2, 3])
def test_managed_setup_applies_complete_passive_profile(adapter, protocol, workmode):
    async def run():
        adapter.change(0x14, workmode)
        adapter.change(0x37, 3)  # Vendor auto-query storage type.
        adapter.change(0x71, 20)  # Query/multi-host engine enabled.
        adapter.change(0x39, 3)  # XON/XOFF would alter binary RTU data.
        adapter.change(0x6E, 0xFB)  # All flags except management password.
        before = adapter.native
        async with await gw.configure_gateway(ADDRESS, gw.GatewaySettings(connection_type=protocol)) as receipt:
            assert receipt.after.connection_type == protocol
            for offset in (0x14, 0x37, 0x71, 0x39):
                assert adapter.native[offset] == 0
            assert adapter.native[0x6E] == 0x58  # P2P, ping and broadcast filter kept.
            assert adapter.native[0x3C] == (1 if protocol == "modbus_tcp" else 0)
            for offset, value in enumerate(before):
                if offset not in (0x14, 0x37, 0x71, 0x39, 0x6E, 0x3C):
                    assert adapter.native[offset] == value
            await receipt.commit()
        assert len(adapter.writes) == 1

    asyncio.run(asyncio.wait_for(run(), 3))


def test_failed_caller_save_restores_all_passive_profile_changes(adapter):
    async def run():
        adapter.change(0x14, 2)
        adapter.change(0x37, 3)
        adapter.change(0x71, 20)
        adapter.change(0x39, 3)
        adapter.change(0x6E, 0xB3)
        before = adapter.native
        with pytest.raises(RuntimeError, match="save failed"):  # noqa: PT012 - Caller-owned context.
            async with await gw.configure_gateway(
                ADDRESS, gw.GatewaySettings(baudrate=9600, frame="8e1", connection_type="modbus_tcp", tcp_port=1502),
            ) as receipt:
                adapter.change(0x6E, adapter.native[0x6E] | 0x40)  # Another administrator enabled ping.
                raise RuntimeError("save failed")
        expected = bytearray(before)
        expected[0x6E] |= 0x40
        assert receipt.rollback_verified
        assert adapter.native == bytes(expected)
        assert len(adapter.writes) == 2

    asyncio.run(asyncio.wait_for(run(), 3))


def test_partial_passive_flag_apply_rolls_back_each_owned_bit(adapter):
    async def run():
        adapter.change(0x6E, 0xB3)
        before = adapter.native

        async def partial_flags(device):
            if len(device.writes) == 1:
                device.change(0x6E, device.native[0x6E] | 0x80)

        adapter.after_send = partial_flags
        with pytest.raises(gw.GatewayRollbackError, match="gateway_rollback_failed"):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(connection_type="rtu_over_tcp"))
        assert adapter.native == before
        assert len(adapter.writes) == 2

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize(("offset", "value"), [
    (0x7F, 1), (0x82, 65), (0xA6, 1),  # Unknown extension or registration/heartbeat payload.
    (0x72, 5), (0x72, 129), (0x34, 1),  # Unverified flags and obsolete custom-framing field.
    (0x37, 1), (0x37, 5), (0x39, 4),
])
def test_unsafe_unknown_profile_is_never_silently_cleared(adapter, offset, value):
    async def run():
        adapter.change(offset, value)
        before = adapter.native
        with pytest.raises(gw.GatewayUnsupportedError):
            await gw.configure_gateway(ADDRESS, gw.GatewaySettings(connection_type="modbus_tcp"))
        assert adapter.writes == []
        assert adapter.native == before

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("workmode", [1, 2, 3])
def test_auth_enabled_in_unverified_current_workmode_cannot_be_disabled(adapter, workmode):
    async def run():
        adapter.change(0x14, workmode)
        adapter.change(0x72, adapter.native[0x72] | 2)
        assert not (await gw.inspect_gateway(ADDRESS)).can_configure
        with pytest.raises(gw.GatewayUnsupportedError):
            await gw.configure_gateway(
                ADDRESS, gw.GatewaySettings(connection_type="modbus_tcp", tcp_authentication=False), PASSWORD,
            )
        assert adapter.auth == []
        assert adapter.writes == []

    asyncio.run(asyncio.wait_for(run(), 3))
