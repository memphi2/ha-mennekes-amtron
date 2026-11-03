from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from custom_components.mennekes_amtron_rtu import registers as R
from custom_components.mennekes_amtron_rtu.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.client_errors import AmtronBusError
from custom_components.mennekes_amtron_rtu.identity import (
    async_read_identity,
    model_name,
)
from custom_components.mennekes_amtron_rtu.models import ARTICLE_MODELS
from tests.fakes import FakeModbusClient, device_bank

CONFIG = SerialConfig(port="/dev/fake", device_id=50)


@pytest.mark.parametrize(("articles", "model"), [
    (("1313207205", "1313207205BK"), "Amtron 4You 310 7.4 C2"),
    (("1313201205", "1313201205BK"), "Amtron 4You 310 11 C2"),
    (("1313201605", "1313201605BK"), "Amtron 4You 310 11 T2S"),
    (("1313202205", "1313202205BK"), "Amtron 4You 310 22 C2"),
    (("1313201265", "1313201265BK"), "Amtron 4You 310 11 C2 PV-Bundle"),
    (("1321307205",), "Amtron Compact 2.0s 7.4 C2"),
    (("1321301205",), "Amtron Compact 2.0s 11 C2"),
    (("1321302205",), "Amtron Compact 2.0s 22 C2"),
    (("1321301265",), "Amtron Compact 2.0s 11 C2 PV-Bundle"),
    (("1331307405",), "Amtron Start 2.0s 7.4"),
    (("1331307605",), "Amtron Start 2.0s 7.4 T2S"),
    (("1331302405",), "Amtron Start 2.0s 22"),
    (("1331302605",), "Amtron Start 2.0s 22 T2S"),
])
def test_supported_models_match_complete_padded_articles(
    articles: tuple[str, ...], model: str,
) -> None:
    for article in articles:
        assert model_name(article) == model
        assert model_name(article.rjust(16, "_")) == model
        assert model_name(f"{article}OTHER") == "Amtron"


@pytest.mark.parametrize("article", ["1311201205BK", "1311201605BK", "1321101205", "1315001205BK"])
def test_similarly_named_families_are_not_identified_as_supported(article: str) -> None:
    assert model_name(article) == "Amtron"


def test_the_compatibility_matrix_covers_the_model_mapping() -> None:
    document = (Path(__file__).resolve().parents[1] / "docs/compatibility.md").read_text()
    rows = [line.split("|") for line in document.splitlines() if line.startswith("| Amtron ")]
    documented = {
        article.strip().strip("`"): row[1].strip()
        for row in rows for article in row[2].split(",")
    }
    assert documented == ARTICLE_MODELS


async def _identity(transport: FakeModbusClient) -> object:
    client = MennekesModbusClient(CONFIG, client_factory=lambda _config: transport)
    await client.async_connect()
    return await async_read_identity(client)


def test_a_validated_device_reports_everything() -> None:
    identity = asyncio.run(_identity(FakeModbusClient(device_bank())))
    assert identity.layout_version == R.LAYOUT_V01_03
    assert identity.serial_number == "ABC123456789"
    assert identity.article_number == "1313201205"
    assert identity.firmware_version == "2023.21.11024"
    assert identity.phase_options_hw == 2
    assert identity.max_evse_current == 16.0


def test_an_older_layout_skips_the_registers_it_lacks() -> None:
    bank = device_bank(layout=R.LAYOUT_V01_00)
    identity = asyncio.run(_identity(FakeModbusClient(bank)))
    assert identity.layout_version == R.LAYOUT_V01_00
    assert identity.serial_number is None
    assert identity.article_number is None
    assert identity.phase_options_hw is None
    assert identity.firmware_version == "2023.21.11024"


def test_a_register_that_fails_is_simply_absent() -> None:
    transport = FakeModbusClient(device_bank())
    transport.read_exceptions[R.SERIAL_NUMBER.address] = 0x02
    identity = asyncio.run(_identity(transport))
    assert identity.serial_number is None
    assert identity.article_number == "1313201205"


def test_a_device_without_a_layout_version_is_refused() -> None:
    transport = FakeModbusClient({R.MODBUS_LAYOUT_VERSION.address: 0})
    with pytest.raises(AmtronBusError, match="layout version"):
        asyncio.run(_identity(transport))


def test_an_unreadable_maximum_current_is_absent() -> None:
    bank = device_bank()
    bank[R.MAX_EVSE_CURRENT.address] = 0x7FC0
    bank[R.MAX_EVSE_CURRENT.address + 1] = 0x0000
    identity = asyncio.run(_identity(FakeModbusClient(bank)))
    assert identity.max_evse_current is None
