"""The image gate serves real package resources and rejects incomplete images."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from scripts import check_service_assets as probe
from scripts import service_image as images


@pytest.mark.parametrize("broken", [None, "app_settings.html", "app_settings.js", "app_settings.css", "modified"])
def test_image_probe_serves_isolated_installed_package_and_rejects_missing_or_changed_assets(
    tmp_path, monkeypatch, broken,
):
    # Match the wheel's package name without relying on imports from the HA
    # checkout. The actual container runs the same probe against its wheel.
    package = tmp_path / "amtron_device"
    shutil.copytree(
        images.ROOT / "custom_components/mennekes_amtron_rtu/device", package,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    if broken == "modified":
        (package / "app_settings.js").write_text("/* incomplete or wrong build */")
    elif broken is not None:
        (package / broken).unlink()
    commands = []

    def run(*command):
        commands.append(command)
        assert command[:5] == ("docker", "exec", "isolated-test-image", "python", "-c")
        # Execute the probe passed to docker exec, including its real HTTP
        # requests, on this isolated package with no Supervisor or hardware.
        return subprocess.run(
            [sys.executable, *command[4:]], cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(tmp_path)},
            check=True, capture_output=True, timeout=20,
        ).stdout

    monkeypatch.setattr(images, "run", run)
    if broken is None:
        images.check_installed_assets("isolated-test-image")
    else:
        with pytest.raises(subprocess.CalledProcessError) as error:
            images.check_installed_assets("isolated-test-image")
        assert b"installed app asset differs or cannot be served" in error.value.stderr
    assert len(commands) == 1


@pytest.mark.parametrize(("remote", "path", "method"), [
    ("192.0.2.1", "/", "GET"), ("127.0.0.1", "/api/config", "GET"),
    ("127.0.0.1", "/", "POST"),
])
def test_probe_guard_never_allows_remote_clients_or_configuration(remote, path, method):
    async def run():
        settings = probe.ProbeSettings()
        handler = AsyncMock()
        request = SimpleNamespace(remote=remote, path=path, method=method)
        with pytest.raises(web.HTTPForbidden):
            await settings.guard(request, handler)
        handler.assert_not_awaited()
        with pytest.raises(web.HTTPForbidden):
            await settings.read(request)
        with pytest.raises(web.HTTPForbidden):
            await settings.save(request)
    asyncio.run(run())


def test_probe_requires_all_assets_before_loading_the_installed_package(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["asset-probe", "{}"])
    assert probe.main() == 1
    assert "expected hashes for all three app assets" in capsys.readouterr().err
