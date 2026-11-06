"""One-shot lookup of published German network tariffs for the HA Home point.

The public source is incomplete. Missing or ambiguous records are never guessed.
Provisional values are marked and require normal user confirmation to save.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .charging_cost_const import (
    CONF_ACTIVE_QUARTERS,
    CONF_HIGH_NETWORK,
    CONF_HIGH_PERIODS,
    CONF_LOW_NETWORK,
    CONF_LOW_PERIODS,
    CONF_REGULAR_NETWORK,
    CONF_TARIFF_YEAR,
)
from .device.charging_cost import TariffConfig

_VNB_URL = "https://www.vnbdigital.de/gateway/graphql"
_DATA_BASE = "https://raw.githubusercontent.com/the78mole/Par14a-Database/main/data"
_VNB_QUERY = """query($coordinates:String!,$filter:vnb_FilterInput){
  vnb_coordinates(coordinates:$coordinates){
    vnbs(filter:$filter){_id name voltageTypes}
  }
}"""


async def async_lookup_home_tariff(hass: Any) -> dict[str, Any] | None:
    """Return verified form values, or None if a safe import is impossible."""
    config = hass.config
    if (
        getattr(config, "country", None) != "DE"
        or not -90 <= getattr(config, "latitude", float("nan")) <= 90
        or not -180 <= getattr(config, "longitude", float("nan")) <= 180
    ):
        return None
    year = datetime.now(ZoneInfo("Europe/Berlin")).year
    session = async_get_clientsession(hass)
    try:
        async with session.post(
            _VNB_URL,
            json={
                "query": _VNB_QUERY,
                "variables": {
                    "coordinates": f"{config.latitude:.6f},{config.longitude:.6f}",
                    "filter": {
                        "onlyNap": False,
                        "voltageTypes": ["Niederspannung"],
                        "withRegions": True,
                    },
                },
            },
            timeout=ClientTimeout(total=10),
        ) as response:
            response.raise_for_status()
            raw = await response.content.read(100_001)
            if len(raw) > 100_000:
                return None
            payload = json.loads(raw)
        vnbs = payload["data"]["vnb_coordinates"]["vnbs"]
        if not isinstance(vnbs, list) or len(vnbs) != 1:
            return None
        vnb_id = str(vnbs[0]["_id"])
        prices, windows = await asyncio.gather(
            _csv_rows(session, f"{_DATA_BASE}/{year}/preisblatt.csv"),
            _csv_rows(session, f"{_DATA_BASE}/{year}/m3_zeitfenster.csv"),
        )
        return _tariff_values(vnb_id, year, prices, windows)
    except (ClientError, KeyError, TypeError, ValueError, OSError, TimeoutError, RuntimeError):
        return None


async def _csv_rows(session: Any, url: str) -> list[dict[str, str]]:
    async with session.get(url, timeout=ClientTimeout(total=10)) as response:
        response.raise_for_status()
        raw = await response.content.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("tariff data too large")
    body = raw.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(body)))


def _tariff_values(
    vnb_id: str, year: int, prices: list[dict[str, str]],
    windows: list[dict[str, str]],
) -> dict[str, Any] | None:
    """Accept one identifiable price sheet with a representable daily schedule."""
    matches = [row for row in prices if row.get("vnb_id") == vnb_id and row.get("jahr") == str(year)]
    if len(matches) != 1 or matches[0].get("status") not in ("endgueltig", "vorlaeufig"):
        return None
    price = matches[0]
    source_url = price.get("preisblatt_url", "")
    if not source_url.startswith(("https://", "http://")) or len(source_url) > 2048:
        return None
    # The estimate replaces one standard network component. A sheet where
    # the ordinary rate differs from Module 3 ST needs two separate base rates.
    try:
        ordinary = float(price["std_arbeitspreis_ct_kwh"])
        standard = float(price["m3_st_ct_kwh"])
    except (KeyError, TypeError, ValueError):
        return None
    if abs(ordinary - standard) > 1e-6:
        return None
    rows = [row for row in windows if row.get("vnb_id") == vnb_id and row.get("jahr") == str(year)]
    quarters = sorted({int(row["quartal"]) for row in rows if row.get("ht") or row.get("nt")})
    if len(quarters) < 2 or quarters != sorted({int(q) for q in price["m3_quartale_aktiv"].split(",")}):
        return None
    period_sets = {(row["ht"], row["nt"]) for row in rows if int(row["quartal"]) in quarters}
    if len(period_sets) != 1:
        return None
    high, low = period_sets.pop()
    if not high or not low:
        return None
    def periods(raw: str) -> str:
        return "\n".join(part.replace("24:00", "00:00") for part in raw.split("|"))
    values: dict[str, Any] = {
        "_source_url": price.get("preisblatt_url", ""),
        "_source_status": price["status"],
        CONF_REGULAR_NETWORK: float(price["m3_st_ct_kwh"]) * 0.0119,
        CONF_HIGH_NETWORK: float(price["m3_ht_ct_kwh"]) * 0.0119,
        CONF_LOW_NETWORK: float(price["m3_nt_ct_kwh"]) * 0.0119,
        CONF_HIGH_PERIODS: periods(high),
        CONF_LOW_PERIODS: periods(low),
        CONF_ACTIVE_QUARTERS: [str(q) for q in quarters],
        CONF_TARIFF_YEAR: year,
    }
    try:
        TariffConfig.parse({
            "module": "module_3",
            "regular_network": values[CONF_REGULAR_NETWORK],
            "high_network": values[CONF_HIGH_NETWORK],
            "low_network": values[CONF_LOW_NETWORK],
            "active_quarters": [int(q) for q in values[CONF_ACTIVE_QUARTERS]],
            "tariff_year": year,
            "price_includes_network": True,
            "timezone": "Europe/Berlin",
            "windows": [
                {"start": part.split("-")[0], "end": part.split("-")[1], "rate": rate}
                for rate, key in (("ht", CONF_HIGH_PERIODS), ("nt", CONF_LOW_PERIODS))
                for part in values[key].splitlines()
            ],
        })
    except (ValueError, TypeError):
        return None
    return values
