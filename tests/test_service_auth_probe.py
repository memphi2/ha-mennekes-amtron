"""Run the actual image-injected probe against an isolated installed package."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from scripts import service_image


@pytest.mark.parametrize("failure", [None, "missing_helper", "wrong_cipher_direction"])
def test_installed_authentication_probe_executes_real_full_handshake(tmp_path, monkeypatch, failure):
    package = tmp_path / "amtron_device"
    shutil.copytree(service_image.ROOT / "custom_components/mennekes_amtron_rtu/device", package,
                    ignore=shutil.ignore_patterns("__pycache__"))
    helper = package / "waveshare_auth.py"
    if failure == "missing_helper":
        helper.unlink()
    elif failure == "wrong_cipher_direction":
        helper.write_text(helper.read_text().replace(".decryptor()", ".encryptor()"))
    calls = []

    def run(*args):
        assert args[:5] == ("docker", "exec", "isolated-container", "python", "-c")
        result = subprocess.run(
            [sys.executable, "-c", args[5]], cwd=tmp_path,
            env=dict(os.environ, PYTHONPATH=str(tmp_path)),
            capture_output=True, text=True, check=False, timeout=15,
        )
        calls.append(result)
        if result.returncode:
            raise ValueError("installed authentication probe failed")
        return result.stdout.encode()

    monkeypatch.setattr(service_image, "run", run)
    if failure:
        with pytest.raises(ValueError, match="probe failed"):
            service_image.check_installed_authentication("isolated-container")
        assert calls[0].returncode != 0
    else:
        service_image.check_installed_authentication("isolated-container")
        assert calls[0].returncode == 0
        assert calls[0].stderr == ""
