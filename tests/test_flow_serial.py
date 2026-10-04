from __future__ import annotations

from pathlib import Path

from custom_components.mennekes_amtron._flow_serial import (
    PortOption,
    list_by_id_ports,
    list_comports,
    merge_port_options,
    port_category,
)


def test_port_category_hides_identifying_paths() -> None:
    assert port_category("/dev/serial/by-id/usb-FTDI_serial-if00-port0") == "by-id"
    assert port_category("/dev/ttyUSB0") == "tty"
    assert port_category("socket://10.0.0.5:502") == "other"


def test_by_id_ports_are_listed_from_the_directory(tmp_path: Path) -> None:
    (tmp_path / "usb-Adapter-if00-port0").write_text("", encoding="utf-8")
    (tmp_path / ".hidden").write_text("", encoding="utf-8")
    options = list_by_id_ports(str(tmp_path))
    assert [option.label for option in options] == ["usb-Adapter-if00-port0"]
    assert options[0].value.endswith("usb-Adapter-if00-port0")


def test_a_missing_directory_yields_no_ports() -> None:
    assert list_by_id_ports("/does/not/exist") == []


def test_comports_are_listed_without_raising() -> None:
    assert isinstance(list_comports(), list)


def test_stable_paths_are_offered_first() -> None:
    merged = merge_port_options(
        [PortOption(value="/dev/serial/by-id/usb-A", label="usb-A")],
        [
            PortOption(value="/dev/ttyUSB0", label="/dev/ttyUSB0"),
            PortOption(value="/dev/serial/by-id/usb-A", label="duplicate"),
        ],
    )
    assert [option.value for option in merged] == [
        "/dev/serial/by-id/usb-A",
        "/dev/ttyUSB0",
    ]
