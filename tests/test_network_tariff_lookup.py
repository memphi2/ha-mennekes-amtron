"""The one-shot tariff import rejects ambiguous or provisional data."""

import asyncio
import csv
import io
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from custom_components.mennekes_amtron_rtu.charging_cost_const import (
    CONF_ACTIVE_QUARTERS,
    CONF_HIGH_PERIODS,
    CONF_REGULAR_NETWORK,
)
from custom_components.mennekes_amtron_rtu.network_tariff_lookup import (
    _csv_rows,
    _tariff_values,
    async_lookup_home_tariff,
)

PRICE = {
    "vnb_id": "3", "jahr": "2026", "status": "endgueltig",
    "m3_quartale_aktiv": "1,2,3,4", "m3_st_ct_kwh": "9.32",
    "std_arbeitspreis_ct_kwh": "9.32",
    "m3_ht_ct_kwh": "13.98", "m3_nt_ct_kwh": "2.42",
    "preisblatt_url": "https://example.org/price.pdf",
}
WINDOWS = [
    {"vnb_id": "3", "jahr": "2026", "quartal": str(q),
     "ht": "16:30-20:15", "nt": "00:00-06:00|23:00-24:00"}
    for q in range(1, 5)
]


def test_final_tariff_import_converts_net_cents_and_multiple_windows():
    result = _tariff_values("3", 2026, [PRICE], WINDOWS)
    assert result is not None
    assert result[CONF_REGULAR_NETWORK] == 9.32 * 0.0119
    assert result[CONF_HIGH_PERIODS] == "16:30-20:15"
    assert result[CONF_ACTIVE_QUARTERS] == ["1", "2", "3", "4"]


def test_import_labels_provisional_and_rejects_ambiguous_quarter_specific_data():
    provisional = _tariff_values("3", 2026, [{**PRICE, "status": "vorlaeufig"}], WINDOWS)
    assert provisional is not None
    assert provisional["_source_status"] == "vorlaeufig"
    assert _tariff_values("3", 2026, [PRICE, PRICE], WINDOWS) is None
    varied = [*WINDOWS[:-1], {**WINDOWS[-1], "ht": "17:00-20:00"}]
    assert _tariff_values("3", 2026, [PRICE], varied) is None
    different_standard = [{**PRICE, "m3_st_ct_kwh": "8.22"}]
    assert _tariff_values("3", 2026, different_standard, WINDOWS) is None


def test_import_rejects_unusable_source_and_price_sheet_values():
    assert _tariff_values("3", 2026, [], WINDOWS) is None
    assert _tariff_values("3", 2026, [{**PRICE, "status": "unknown"}], WINDOWS) is None
    assert _tariff_values("3", 2026, [{**PRICE, "preisblatt_url": "javascript:bad"}], WINDOWS) is None
    assert _tariff_values("3", 2026, [{**PRICE, "std_arbeitspreis_ct_kwh": "bad"}], WINDOWS) is None
    assert _tariff_values("3", 2026, [PRICE], WINDOWS[:1]) is None
    assert _tariff_values("3", 2026, [PRICE], [{**row, "ht": ""} for row in WINDOWS]) is None
    assert _tariff_values("3", 2026, [{**PRICE, "m3_ht_ct_kwh": "1"}], WINDOWS) is None


class _Response:
    def __init__(self, body, *, chunk_size=65_536):
        self.content = self
        self._body = body
        self._chunk_size = chunk_size

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    async def read(self, limit):
        count = min(limit, self._chunk_size)
        result, self._body = self._body[:count], self._body[count:]
        await asyncio.sleep(0)
        return result


class _Session:
    def __init__(self, operators, *, chunk_size=65_536):
        self.operators = operators
        self.urls = []
        self.chunk_size = chunk_size

    def post(self, _url, *, json, timeout):
        assert json["variables"]["coordinates"] == "50.775300,6.083900"
        assert timeout.total == 10
        import json as json_module

        body = json_module.dumps({"data": {"vnb_coordinates": {"vnbs": self.operators}}}).encode()
        return _Response(body, chunk_size=self.chunk_size)

    def get(self, url, *, timeout):
        self.urls.append(url)
        assert timeout.total == 10
        year = str(datetime.now(ZoneInfo("Europe/Berlin")).year)
        original = [PRICE] if url.endswith("preisblatt.csv") else WINDOWS
        rows = [{**row, "jahr": year} for row in original]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        return _Response(output.getvalue().encode(), chunk_size=self.chunk_size)


@pytest.mark.parametrize("chunk_size", [7, 65_536])
def test_home_lookup_uses_coordinates_and_reads_complete_fragmented_bodies(monkeypatch, chunk_size):
    session = _Session([{"_id": "3", "name": "Regionetz"}], chunk_size=chunk_size)
    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.network_tariff_lookup.async_get_clientsession",
        lambda _hass: session,
    )
    hass = SimpleNamespace(config=SimpleNamespace(
        country="DE", latitude=50.7753, longitude=6.0839,
    ))
    result = asyncio.run(async_lookup_home_tariff(hass))
    assert result is not None
    assert result[CONF_HIGH_PERIODS] == "16:30-20:15"
    assert len(session.urls) == 2


def test_home_lookup_does_not_guess_ambiguous_operator(monkeypatch):
    session = _Session([{"_id": "3"}, {"_id": "4"}])
    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.network_tariff_lookup.async_get_clientsession",
        lambda _hass: session,
    )
    hass = SimpleNamespace(config=SimpleNamespace(
        country="DE", latitude=50.7753, longitude=6.0839,
    ))
    assert asyncio.run(async_lookup_home_tariff(hass)) is None
    assert session.urls == []


def test_home_lookup_skips_non_german_and_bad_coordinates(monkeypatch):
    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.network_tariff_lookup.async_get_clientsession",
        lambda _hass: (_ for _ in ()).throw(AssertionError("unexpected request")),
    )
    for country, latitude in (("FR", 50.0), ("DE", 100.0)):
        hass = SimpleNamespace(config=SimpleNamespace(
            country=country, latitude=latitude, longitude=6.0,
        ))
        assert asyncio.run(async_lookup_home_tariff(hass)) is None


def test_home_lookup_rejects_invalid_and_oversized_responses(monkeypatch):
    class BadSession(_Session):
        def __init__(self, body):
            super().__init__([{"_id": "3"}])
            self.body = body

        def post(self, _url, *, json, timeout):
            return _Response(self.body)

    hass = SimpleNamespace(config=SimpleNamespace(
        country="DE", latitude=50.7753, longitude=6.0839,
    ))
    for body in (b"not json", b"{}", b"x" * 100_001):
        monkeypatch.setattr(
            "custom_components.mennekes_amtron_rtu.network_tariff_lookup.async_get_clientsession",
            lambda _hass, data=body: BadSession(data),
        )
        assert asyncio.run(async_lookup_home_tariff(hass)) is None


def test_tariff_csv_download_is_bounded():
    class HugeSession:
        def get(self, _url, *, timeout):
            return _Response(b"x" * 2_000_001)

    async def run():
        with pytest.raises(ValueError, match="too large"):
            await _csv_rows(HugeSession(), "https://example.org/data.csv")

    asyncio.run(run())


def test_malformed_csv_cells_and_periods_reject_import_without_exception():
    for periods in ("17:00", "17:00-18:00-extra"):
        malformed = [{**row, "ht": periods} for row in WINDOWS]
        assert _tariff_values("3", 2026, [PRICE], malformed) is None
    assert _tariff_values("3", 2026, [{**PRICE, "preisblatt_url": None}], WINDOWS) is None
    assert _tariff_values("3", 2026, [{**PRICE, None: ["extra"]}], WINDOWS) is None
    assert _tariff_values("3", 2026, [PRICE], [{**row, "quartal": "bad"} for row in WINDOWS]) is None
    assert _tariff_values("3", 2026, [{**PRICE, "std_arbeitspreis_ct_kwh": "nan"}], WINDOWS) is None


def test_malformed_external_csv_leaves_options_lookup_available(monkeypatch):
    class BadCsvSession(_Session):
        def get(self, url, *, timeout):
            # DictReader's per-field limit is deliberately exceeded while the
            # complete body stays below our HTTP response size limit.
            return _Response(b"field\n" + b"x" * 150_000)

    session = BadCsvSession([{"_id": "3"}])
    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.network_tariff_lookup.async_get_clientsession",
        lambda _hass: session,
    )
    hass = SimpleNamespace(config=SimpleNamespace(
        country="DE", latitude=50.7753, longitude=6.0839,
    ))
    assert asyncio.run(async_lookup_home_tariff(hass)) is None


@pytest.mark.parametrize(("field", "periods"), [
    ("ht", "16:31-20:15"), ("ht", "16:30-20:14"),
    ("nt", "00:01-06:00"), ("nt", "00:00-06:01"),
])
def test_new_import_rejects_non_quarter_hour_start_and_end(field, periods):
    rows = [{**row, field: periods} for row in WINDOWS]
    assert _tariff_values("3", 2026, [PRICE], rows) is None
