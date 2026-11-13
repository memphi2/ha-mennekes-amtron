"""AppArmor validation must fail closed and clean up its isolated resources."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import check_service_apparmor as gate

PARSER = "/usr/sbin/apparmor_parser"
PROFILE_NAME = "amtron-ci-regression"


@pytest.fixture
def isolated_names(monkeypatch):
    monkeypatch.setattr(gate.shutil, "which", lambda name: PARSER if name == "apparmor_parser" else None)
    monkeypatch.setattr(gate.secrets, "token_hex", lambda _count: "regression")


def test_compile_only_never_loads_policy_or_contacts_docker(monkeypatch, isolated_names):
    commands = []

    def run(*args, **_kwargs):
        commands.append(args)
        assert args[0] == PARSER
        assert "--skip-kernel-load" in args
        assert "--skip-cache" in args
        assert "--Werror" in args
        assert "--add" not in args
        assert "--remove" not in args
        assert f"profile {PROFILE_NAME} " in Path(args[-1]).read_text()
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gate, "command", run)
    monkeypatch.setattr(gate, "exercise_mode", lambda *_args: pytest.fail("compile-only ran a container"))
    assert gate.check(None, compile_only=True) == {"status": "compiled", "enforcement_tested": False}
    assert len(commands) == 1
    assert not Path(commands[0][-1]).exists()


def test_missing_parser_is_not_a_successful_skip(monkeypatch):
    monkeypatch.setattr(gate.shutil, "which", lambda _name: None)
    monkeypatch.setattr(gate, "command", lambda *_args: pytest.fail("ran without a parser"))
    with pytest.raises(RuntimeError, match="apparmor_parser is required"):
        gate.check("app:test", compile_only=False)


@pytest.mark.parametrize("security", [[], ["name=seccomp,profile=builtin"], None, {}])
def test_missing_docker_apparmor_fails_before_loading_policy(monkeypatch, isolated_names, security):
    commands = []

    def run(*args, **_kwargs):
        commands.append(args)
        if args[0] == PARSER:
            assert "--skip-kernel-load" in args
            return subprocess.CompletedProcess(args, 0, "", "")
        assert args[:2] == ("docker", "info")
        return subprocess.CompletedProcess(args, 0, json.dumps(security), "")

    monkeypatch.setattr(gate, "command", run)
    with pytest.raises(RuntimeError, match="Docker must support AppArmor"):
        gate.check("app:test", compile_only=False)
    assert len(commands) == 2
    assert not any("--add" in args for args in commands)


@pytest.mark.parametrize("failure", ["compile", "docker-info"])
def test_failed_preflight_does_not_load_policy(monkeypatch, isolated_names, failure):
    commands = []

    def run(*args, **_kwargs):
        commands.append(args)
        assert "--add" not in args
        if failure == "compile" or args[0] == "docker":
            raise subprocess.CalledProcessError(1, args, stderr="preflight failed")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gate, "command", run)
    with pytest.raises(subprocess.CalledProcessError):
        gate.check("app:test", compile_only=False)
    assert len(commands) == (1 if failure == "compile" else 2)


@pytest.mark.parametrize("uid", [0, 1000])
@pytest.mark.parametrize("failure", [None, "network-create", "probe", "smoke", "network-remove"])
def test_loaded_profile_is_removed_after_every_outcome(monkeypatch, isolated_names, uid, failure):
    events = []

    def run(*args, **_kwargs):
        if "--skip-kernel-load" in args:
            events.append("compile")
        elif args[:2] == ("docker", "info"):
            return subprocess.CompletedProcess(args, 0, '["name=apparmor"]', "")
        elif "--add" in args or "--remove" in args:
            assert args[:1] == (PARSER,) if uid == 0 else args[:3] == ("sudo", "-n", PARSER)
            events.append("load" if "--add" in args else "unload")
        elif args[:3] == ("docker", "network", "create"):
            assert args[-1] == PROFILE_NAME
            assert "--internal" in args
            events.append("network-create")
            if failure == "network-create":
                raise subprocess.CalledProcessError(1, args)
        elif args[:3] == ("docker", "network", "rm"):
            assert args[-1] == PROFILE_NAME
            events.append("network-remove")
            if failure == "network-remove":
                raise subprocess.CalledProcessError(1, args)
        else:
            pytest.fail(f"unexpected operation: {args}")
        return subprocess.CompletedProcess(args, 0, "", "")

    def exercise(image, name, mode, directory):
        assert image == "app:test"
        assert name == PROFILE_NAME
        assert directory.is_dir()
        events.append(mode)
        if failure == "probe":
            raise ValueError("probe failed")
        return {"status": "passed", "mode": mode, "profile": name, "enforcing": True}

    def smoke(image, *, apparmor_profile):
        assert image == "app:test"
        assert apparmor_profile == PROFILE_NAME
        events.append("smoke")
        if failure == "smoke":
            raise ValueError("smoke failed")

    monkeypatch.setattr(gate.os, "geteuid", lambda: uid)
    monkeypatch.setattr(gate, "command", run)
    monkeypatch.setattr(gate, "exercise_mode", exercise)
    monkeypatch.setitem(sys.modules, "service_image", SimpleNamespace(smoke=smoke))
    if failure:
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            gate.check("app:test", compile_only=False)
    else:
        result = gate.check("app:test", compile_only=False)
        assert result["status"] == "passed"
        assert result["enforcement_tested"] is True
        assert [entry["mode"] for entry in result["transports"]] == list(gate.MODES)
    assert events[:3] == ["compile", "load", "network-create"]
    assert events[-1] == "unload"
    if failure == "network-create":
        assert events == ["compile", "load", "network-create", "unload"]
    else:
        assert events[-2:] == ["network-remove", "unload"]
        assert events.count("serial") == 1
        if failure != "probe":
            assert events[3:6] == list(gate.MODES)
            assert "smoke" in events


@pytest.mark.parametrize("failure", ["timeout", "docker", "invalid-report"])
def test_probe_failure_removes_only_its_own_container(monkeypatch, tmp_path, failure):
    cleanup = []

    def run(*args, **kwargs):
        assert args[:2] == ("docker", "run")
        assert "--init" in args
        assert f"--security-opt=apparmor={PROFILE_NAME}" in args
        assert kwargs["stdin"]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 180)
        if failure == "docker":
            raise subprocess.CalledProcessError(125, args)
        return subprocess.CompletedProcess(args, 0, "not-json", "")

    def remove(args, **kwargs):
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 30
        cleanup.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(gate, "command", run)
    monkeypatch.setattr(gate.subprocess, "run", remove)
    with pytest.raises((subprocess.SubprocessError, ValueError)):
        gate.exercise_mode("app:test", PROFILE_NAME, "modbus_tcp", tmp_path)
    assert cleanup == [["docker", "rm", "--force", f"{PROFILE_NAME}-modbus-tcp"]]


@pytest.mark.parametrize(
    "replacement",
    [
        {"mode": "serial"},
        {"status": "skipped"},
        {"profile": "unconfined"},
        {"profile": "amtron-ci-another-invocation"},
        {"enforcing": False},
        {"enforcing": "true"},
        {"enforcing": 1},
        {"enforcing": None},
        {"serial_settings": False},
        {"serial_settings": None},
        {"connection_ui": False},
        {"connection_ui": None},
        {"charge_job_storage": False},
        {"charge_job_storage": None},
    ],
)
def test_success_requires_the_expected_enforcing_profile(monkeypatch, tmp_path, replacement):
    report = {"status": "passed", "mode": "modbus_tcp", "profile": PROFILE_NAME, "enforcing": True, "serial_settings": True, "connection_ui": True, "charge_job_storage": True}
    report.update(replacement)
    monkeypatch.setattr(
        gate, "command",
        lambda *args, **_kwargs: subprocess.CompletedProcess(args, 0, json.dumps(report), ""),
    )
    monkeypatch.setattr(
        gate.subprocess, "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0),
    )
    with pytest.raises(ValueError, match="did not confirm success"):
        gate.exercise_mode("app:test", PROFILE_NAME, "modbus_tcp", tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "",
        "profile another_app {}\n",
        "profile mennekes_amtron_modbus {}\nprofile other {}\n",
        "profile mennekes_amtron_modbus {}\nprofile mennekes_amtron_modbus {}\n",
    ],
)
def test_ambiguous_profile_cannot_be_loaded_under_test_name(source):
    with pytest.raises(ValueError, match="exactly the app's top-level profile"):
        gate.render_profile(source, PROFILE_NAME)


@pytest.mark.parametrize("name", ["unconfined", "amtron-ci-test\nprofile other", "../../profile", "amtron-ci-x;id"])
def test_profile_name_cannot_escape_the_isolated_namespace(name):
    with pytest.raises(ValueError, match="invalid isolated profile name"):
        gate.render_profile("profile mennekes_amtron_modbus {}\n", name)
