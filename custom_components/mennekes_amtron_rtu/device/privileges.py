"""Give up every privilege the service does not need.

The service reads its options, opens a serial port or a TCP connection and
writes four files. None of that needs a capability, so it holds none from its
start: a process running as root, as Supervisor apps do, is then left with
root's files and nothing else.
"""

from __future__ import annotations

import logging
import struct
import sys

_LOGGER = logging.getLogger(__name__)

_CAPABILITY_VERSION_3 = 0x20080522
_PR_SET_NO_NEW_PRIVS = 38


def drop_capabilities() -> None:
    """Empty all capability sets and forbid gaining any; never fatal."""
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        # A later program could not regain what is given up here.
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0):
            raise OSError(ctypes.get_errno(), "prctl")
        header = struct.pack("=Ii", _CAPABILITY_VERSION_3, 0)
        # Effective, permitted and inheritable, twice 32 bits each: all empty.
        if libc.capset(header, bytes(24)):
            raise OSError(ctypes.get_errno(), "capset")
    except (OSError, AttributeError, ImportError, MemoryError) as err:
        _LOGGER.warning("Service: privileges kept (%s)", type(err).__name__)
