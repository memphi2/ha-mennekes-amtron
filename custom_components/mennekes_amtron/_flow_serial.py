"""Serial-port discovery for the config flow.

A USB RS-485 adapter cannot be identified as this wallbox, so the flow offers
a port list instead of pretending to discover the device. ``/dev/serial/by-id``
entries are preferred because ``/dev/ttyUSB0`` moves between reboots.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BY_ID_DIRECTORY = "/dev/serial/by-id"


@dataclass(frozen=True, slots=True)
class PortOption:
    """One selectable serial port."""

    value: str
    label: str


def port_category(port: str) -> str:
    """Return a non-identifying category for a port path.

    Diagnostics report the category instead of the path: a ``by-id`` path
    contains the adapter's serial number.
    """

    if port.startswith(BY_ID_DIRECTORY):
        return "by-id"
    if port.startswith("/dev/tty"):
        return "tty"
    return "other"


def list_by_id_ports(directory: str = BY_ID_DIRECTORY) -> list[PortOption]:
    """Return the stable ``by-id`` serial ports present on this host."""

    base = Path(directory)
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return []
    return [
        PortOption(value=str(entry), label=entry.name)
        for entry in entries
        if not entry.name.startswith(".")
    ]


def list_comports() -> list[PortOption]:
    """Return the serial ports pyserial reports, as a fallback.

    This runs in the executor: pyserial walks sysfs, which is blocking.
    """

    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover - pyserial ships with pymodbus[serial]
        return []
    options: list[PortOption] = []
    for info in sorted(list_ports.comports(), key=lambda item: item.device):
        description = " ".join(
            part
            for part in (info.description, info.manufacturer)
            if part and part != "n/a"
        )
        label = f"{info.device} - {description}" if description else info.device
        options.append(PortOption(value=info.device, label=label))
    return options


def merge_port_options(
    by_id: list[PortOption],
    comports: list[PortOption],
) -> list[PortOption]:
    """Return one port list, with stable ``by-id`` paths listed first."""

    merged = list(by_id)
    known = {option.value for option in merged}
    merged.extend(option for option in comports if option.value not in known)
    return merged
