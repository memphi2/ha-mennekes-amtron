"""The navigation is rendered by the server, complete and in the page language."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import app_settings as ui
from custom_components.mennekes_amtron_rtu.device.app_nav import PLACEHOLDER, render_nav
from custom_components.mennekes_amtron_rtu.device.console_http import CONSOLE_ASSETS
from tests.test_app_settings import HEADERS, FixtureSettings

DEVICE = Path(ui.__file__).parent
ALL = {"/", "/registers", "/jobs", "/guest", "/weekly", "/connection", "/runtime", "/log"}


def test_groups_are_tabs_and_their_pages_a_second_row():
    nav = render_nav("/guest", "de", ALL).decode()
    assert nav.count("<a ") == 7
    assert '<a href="./jobs" aria-current="true">Laden</a>' in nav
    assert '<nav class="app-pages" aria-label="Laden">' in nav
    assert '<a href="./guest" aria-current="page">Gastladen</a>' in nav
    assert nav.count("aria-current") == 2
    # A group with one page links to it and shows no second row.
    single = render_nav("/log", "fr", ALL).decode()
    assert '<a href="./log" aria-current="page">Journal</a>' in single
    assert "app-pages" not in single
    assert 'href="./"' in single


@pytest.mark.parametrize("script", sorted(p.name for p in DEVICE.glob("app_*.js")))
def test_every_page_script_reveals_its_page_after_translating(script):
    # Until then the stylesheet keeps the page invisible: no English flash.
    source = (DEVICE / script).read_text()
    assert source.count('document.documentElement.classList?.add("ready")') == 1
    translate = max(source.index(marker) for marker in ("[data-text]", "[data-overview]", "[data-i18n]") if marker in source)
    assert source.index('classList?.add("ready")') > translate


def test_page_stays_hidden_until_ready_but_never_for_good():
    css = (DEVICE / "app_settings.css").read_text()
    assert "html:not(.ready) main { opacity: 0; animation: reveal 0s 1.2s forwards; }" in css
    assert "@keyframes reveal { to { opacity: 1; } }" in css


def test_registers_are_a_subpage_of_the_overview():
    nav = render_nav("/registers", "de", ALL).decode()
    assert '<a href="./" aria-current="true">Übersicht</a>' in nav
    assert '<nav class="app-pages" aria-label="Übersicht">' in nav
    assert '<a href="./registers" aria-current="page">Register</a>' in nav


def test_pages_this_app_does_not_serve_are_left_out():
    # Without a wallbox connection only the settings app runs.
    nav = render_nav("/connection", "en", {"/", "/connection", "/log"}).decode()
    assert "Charging" not in nav
    assert "app-pages" not in nav
    assert '<a href="./connection" aria-current="page">Settings</a>' in nav
    assert render_nav("/", "xx", ALL) == render_nav("/", "en", ALL)


@pytest.mark.parametrize("page", sorted(
    name for name, kind in [*ui._ASSETS.values(), *CONSOLE_ASSETS.values()] if kind == "text/html"
))
def test_every_page_carries_the_shell(page):
    html = (DEVICE / page).read_bytes()
    assert html.count(PLACEHOLDER) == 1
    assert b'<meta name="color-scheme" content="light dark">' in html
    assert b'<link rel="stylesheet" href="./app_settings.css">' in html
    assert b"app-bar" not in html
    assert b"overview-nav" not in html
    assert b'lang="en"' in html


def test_served_page_is_complete_and_assets_revalidate(monkeypatch):
    async def check():
        monkeypatch.setattr(ui, "SUPERVISOR_PEER", "127.0.0.1")
        async with TestClient(TestServer(ui.create_settings_app(FixtureSettings()))) as client:
            page = await client.get("/log", headers={"Accept-Language": "de"})
            text = await page.text()
            assert page.headers["Cache-Control"] == "no-store"
            assert PLACEHOLDER.decode() not in text
            assert '<a href="./log" aria-current="page">Protokoll</a>' in text
            assert "./jobs" not in text
            # The styles are inside the page, under a policy naming exactly them.
            style = (DEVICE / "app_settings.css").read_bytes()
            digest = base64.b64encode(hashlib.sha256(style).digest()).decode()
            assert "<link" not in text
            assert "<style>" + style.decode() + "</style>" in text
            policy = page.headers["Content-Security-Policy"]
            assert f"style-src 'self' 'sha256-{digest}'" in policy
            assert "default-src 'none'" in policy
            api = await client.get("/api/overview", headers=HEADERS)
            assert "style-src 'self';" in api.headers["Content-Security-Policy"]
            # Scripts and styles answer 304 when unchanged: no empty frame.
            style = await client.get("/app_settings.css")
            assert style.headers["Cache-Control"] == "no-cache"
            again = await client.get("/app_settings.css", headers={"If-None-Match": style.headers["ETag"]})
            assert again.status == 304
            assert again.headers["Cache-Control"] == "no-cache"
            assert await again.read() == b""
            assert (await client.get("/app_settings.css", headers={"If-None-Match": '"other"'})).status == 200
            assert b"@view-transition" in await style.read()

    asyncio.run(check())
