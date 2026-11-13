"""Private bounded advisory rates; this file never contains charging consent."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .charge_rate import RateHistory
from .persistence import StorageCommitUncertainError

MAX_RATE_BYTES = 65536


class RateStore:
    def __init__(self, path: Path, identity: str) -> None:
        self.path = path
        self.identity = identity
        self._last: bytes | None = None

    def load(self) -> RateHistory:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_RATE_BYTES + 1)
        except FileNotFoundError:
            return RateHistory()
        if len(raw) > MAX_RATE_BYTES:
            raise ValueError("rate history exceeds size limit")
        try:
            payload = json.loads(raw)
        except (ValueError, RecursionError) as err:
            raise ValueError("invalid rate history JSON") from err
        if (
            not isinstance(payload, dict) or payload.keys() != {"version", "identity", "rates"}
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["identity"] != self.identity
        ):
            raise ValueError("rate history identity or format differs")
        result = RateHistory.restore(payload["rates"])
        self._last = raw
        return result

    def save(self, rates: object) -> None:
        history = RateHistory.restore(rates)
        raw = json.dumps({"version": 1, "identity": self.identity, "rates": history.snapshot()},
                         allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
        if raw == self._last:
            return
        if len(raw) > MAX_RATE_BYTES:
            raise ValueError("rate history exceeds size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".rates-", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            self._last = None
            try:
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError as err:
                raise StorageCommitUncertainError("rates replaced but directory sync failed") from err
            self._last = raw
        finally:
            temporary.unlink(missing_ok=True)
