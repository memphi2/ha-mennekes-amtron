"""Every log line names its area: ``Area: what (reason)``."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from custom_components.mennekes_amtron_rtu.device.notice import FailureNotice

PACKAGE = Path(__file__).parents[1] / "custom_components" / "mennekes_amtron_rtu"
AREAS = (
    "Blueprints", "Charging cost", "Charging loss", "Charging session", "Connection",
    "Control", "Departure plan", "Energy target", "Heartbeat", "LAN adapter",
    "Network tariff", "Plug & Charge", "Service", "Setup", "Vehicle",
    "Vehicle charge target",
)
_LEVELS = {"info", "warning", "error", "exception", "critical"}


def _calls():
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                yield path.relative_to(PACKAGE), node


def _text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        first = node.values[0] if node.values else None
        return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else ""
    return None


def test_every_visible_log_message_starts_with_a_known_area():
    wrong = []
    for path, node in _calls():
        function = node.func
        if not (
            isinstance(function, ast.Attribute) and function.attr in _LEVELS
            and isinstance(function.value, ast.Name) and function.value.id == "_LOGGER"
        ):
            continue
        message = _text(node.args[0]) if node.args else None
        if message is None or not any(message.startswith(f"{area}: ") for area in AREAS):
            wrong.append(f"{path}:{node.lineno}: {message!r}")
    assert not wrong, "\n".join(wrong)


def test_every_failure_notice_names_a_known_area():
    areas = []
    for path, node in _calls():
        if isinstance(node.func, ast.Name) and node.func.id == "FailureNotice":
            area = _text(node.args[1]) if len(node.args) == 2 else None
            assert area in AREAS, f"{path}:{node.lineno}: {area!r}"
            areas.append(area)
    assert areas


def test_first_failure_warns_repeats_stay_quiet_and_recovery_is_reported_once(caplog):
    notice = FailureNotice(logging.getLogger("amtron.test"), "Charging cost")
    caplog.set_level(logging.DEBUG, logger="amtron.test")
    assert not notice.failing
    assert not notice.recovered()
    assert notice.failed("estimate not saved", "OSError")
    assert not notice.failed("estimate not saved", "OSError")
    assert notice.failing
    assert notice.recovered("estimate saved again")
    assert not notice.recovered("estimate saved again")
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        ("WARNING", "Charging cost: estimate not saved (OSError)"),
        ("DEBUG", "Charging cost: estimate not saved (OSError)"),
        ("INFO", "Charging cost: estimate saved again"),
    ]


@pytest.mark.parametrize("level", [logging.ERROR, logging.WARNING])
def test_failure_level_without_a_reason(caplog, level):
    notice = FailureNotice(logging.getLogger("amtron.test"), "Energy target")
    caplog.set_level(logging.DEBUG, logger="amtron.test")
    notice.failed("cannot be enforced", level=level)
    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (level, "Energy target: cannot be enforced"),
    ]


def test_a_notice_reports_a_changed_cause_again_and_its_traceback_once(caplog):
    logger = logging.getLogger("tests.notice")
    notice = FailureNotice(logger, "Control")
    caplog.set_level(logging.DEBUG, logger="tests.notice")
    assert notice.failed("command deferred", "phase_pause_state_unknown")
    assert not notice.failed("command deferred", "phase_pause_state_unknown")
    # The cause changed: the log must not keep naming the old one.
    assert not notice.failed("command deferred", "read_only_mode")
    assert not notice.failed("command deferred", "read_only_mode")
    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (logging.WARNING, "Control: command deferred (phase_pause_state_unknown)"),
        (logging.DEBUG, "Control: command deferred (phase_pause_state_unknown)"),
        (logging.WARNING, "Control: command deferred (read_only_mode)"),
        (logging.DEBUG, "Control: command deferred (read_only_mode)"),
    ]
    assert notice.recovered("command accepted again")
    caplog.clear()
    for _ in range(2):
        try:
            raise RuntimeError("display failed")
        except RuntimeError as err:
            notice.failed("listener failed", type(err).__name__, level=logging.ERROR, exc_info=True)
    first, repeat = caplog.records
    assert (first.levelno, first.exc_info is not None) == (logging.ERROR, True)
    assert (repeat.levelno, repeat.exc_info) == (logging.DEBUG, False)
