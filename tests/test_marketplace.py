"""Install our release through the real Marketplace using offline downloads."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

import pytest
from awesomeversion import AwesomeVersion
from homeassistant.const import __version__ as HA_VERSION

from tests.test_release_package import _build

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "mennekes_amtron_rtu"
REPOSITORY = "memphi2/ha-mennekes-amtron"

pytestmark = pytest.mark.skipif(
    AwesomeVersion(HA_VERSION) < "2026.11.0b0",
    reason="Marketplace is built into Home Assistant starting with 2026.11",
)


@pytest.mark.parametrize("reject_update", [False, True])
def test_marketplace_installs_and_updates_the_release(
    tmp_path: Path, reject_update: bool
) -> None:
    """Exercise extraction, loader discovery, restart requests and rollback."""

    from aiohttp import ClientSession
    from homeassistant import bootstrap, config_entries, core, loader
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.marketplace.base import MarketplaceManager
    from homeassistant.components.marketplace.enums import RepositoryCategory
    from homeassistant.components.marketplace.exceptions import MarketplaceError
    from homeassistant.components.marketplace.repositories.integration import (
        IntegrationRepository,
    )
    from homeassistant.helpers import issue_registry as ir

    import custom_components

    archive_path = _build(tmp_path / "build")
    hacs_json = (ROOT / "hacs.json").read_bytes()
    source_manifest = (
        ROOT / "custom_components" / DOMAIN / "manifest.json"
    ).read_bytes()
    remote_manifest = source_manifest
    manifest = json.loads(source_manifest)
    version = f"v{manifest['version']}"
    archive_bytes = archive_path.read_bytes()
    downloads: list[str] = []

    async def download(url: object, **kwargs: object) -> bytes:
        """Only the remote transport is replaced, unexpected requests fail."""

        if not isinstance(url, str):
            from homeassistant.components.marketplace.profiles import Endpoint
            host = "raw.githubusercontent.com" if url.endpoint is Endpoint.RAW else "github.com"
            url = f"https://{host}/{url.path}"
        downloads.append(url)
        files = {
            f"https://raw.githubusercontent.com/{REPOSITORY}/{version}/hacs.json": hacs_json,
            f"https://raw.githubusercontent.com/{REPOSITORY}/{version}/custom_components/{DOMAIN}/manifest.json": remote_manifest,
            f"https://github.com/{REPOSITORY}/releases/download/{version}/{archive_path.name}": archive_bytes,
        }
        return files[url]

    async def run() -> None:
        config = tmp_path / "config"
        component_root = config / "custom_components"
        component_root.mkdir(parents=True)
        # A different TCP integration owns the old domain and must survive.
        tcp = component_root / "mennekes_amtron"
        tcp.mkdir()
        sentinel = tcp / "manifest.json"
        sentinel.write_text('{"domain":"mennekes_amtron","version":"1.0.0"}')
        hass = core.HomeAssistant(str(config))
        loader.async_setup(hass)
        hass.config_entries = config_entries.ConfigEntries(hass, {})
        # No frontend or remote account is needed for an installer test.
        hass.data[LOVELACE_DATA] = SimpleNamespace(resource_mode="storage")
        entry = config_entries.ConfigEntry(
            domain="marketplace",
            title="Marketplace",
            version=1,
            minor_version=1,
            data={},
            options={},
            source="system",
            unique_id=None,
            discovery_keys=MappingProxyType({}),
            subentries_data=[],
        )
        session = ClientSession()
        with (
            patch.object(custom_components, "__path__", [str(component_root)]),
            patch(
                "homeassistant.components.marketplace.base.async_get_clientsession",
                return_value=session,
            ),
        ):
            try:
                await bootstrap.async_load_base_functionality(hass)
                await hass.async_start()
                manager = MarketplaceManager(hass, entry)
                manager.common.categories.add(RepositoryCategory.INTEGRATION)
                repository = IntegrationRepository(manager, REPOSITORY)
                repository.data.id = "123"
                repository.data.domain = DOMAIN
                repository.data.last_version = version
                # Synthetic catalog registration does not claim real acceptance.
                manager.repositories.register(repository, default=True)
                assert DOMAIN not in await loader.async_get_custom_components(hass)
                with (
                    patch.object(manager, "async_download_file", side_effect=download),
                    patch.object(
                        manager,
                        "async_github_api_method",
                        side_effect=AssertionError("Unexpected GitHub API request"),
                    ),
                ):
                    await repository.async_install_repository(ref=version)
                    installed = component_root / DOMAIN
                    assert (installed / "manifest.json").read_bytes() == source_manifest
                    assert repository.data.installed_version == version
                    assert not repository.pending_restart
                    integration = await loader.async_get_integration(hass, DOMAIN)
                    assert integration.config_flow
                    assert integration.categories == ["energy.ev_charging"]
                    assert (installed / "translations/en.json").is_file()
                    assert (installed / "brand/icon.png").is_file()

                    # A synthetic next release proves replacement of loaded code.
                    previous_version = version
                    prepare_update()
                    repository.data.last_version = version
                    if reject_update:
                        with pytest.raises(MarketplaceError) as error:
                            await repository.async_install_repository(ref=version)
                        assert (
                            error.value.translation_key
                            == "installed_manifest_other_domain"
                        )
                        assert repository.data.installed_version == previous_version
                        assert (
                            installed / "manifest.json"
                        ).read_bytes() == source_manifest
                    else:
                        await repository.async_install_repository(ref=version)
                        assert repository.data.installed_version == version
                        assert repository.pending_restart
                        assert (
                            json.loads((installed / "manifest.json").read_bytes())[
                                "version"
                            ]
                            == version[1:]
                        )
                        assert any(
                            domain == "marketplace"
                            for domain, _ in ir.async_get(hass).issues
                        )
                    assert repository.data.installed
                    assert (
                        sentinel.read_text()
                        == '{"domain":"mennekes_amtron","version":"1.0.0"}'
                    )
                    assert len(downloads) == 6  # Both manifests and the ZIP, twice.
            finally:
                await hass.async_stop()
                await session.close()

    def prepare_update() -> None:
        nonlocal version, archive_bytes, remote_manifest
        version = f"v{manifest['version']}.post1"
        updated = {**manifest, "version": version[1:]}
        remote_manifest = json.dumps(updated).encode()
        if reject_update:
            updated["domain"] = "unexpected_domain"
        output = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(archive_bytes)) as original,
            zipfile.ZipFile(output, "w") as target,
        ):
            for member in original.infolist():
                content = (
                    json.dumps(updated).encode()
                    if member.filename == "manifest.json"
                    else original.read(member)
                )
                target.writestr(member, content)
        archive_bytes = output.getvalue()

    asyncio.run(asyncio.wait_for(run(), timeout=60))
