"""Bounded atomic storage for one charge job; never persist resume authority."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .charge_job import ChargeJob
from .persistence import StorageCommitUncertainError

MAX_JOB_BYTES = 8192


class ChargeJobStore:
    """Keep optional jobs separate from the established control-intent format."""

    def __init__(self, path: Path, identity: str) -> None:
        self.path = path
        self.identity = identity
        self._last: bytes | None = None

    def load(self) -> ChargeJob | None:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_JOB_BYTES + 1)
        except FileNotFoundError:
            self._last = self._encode(None)
            return None
        if len(raw) > MAX_JOB_BYTES:
            raise ValueError("saved charge job exceeds size limit")
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict) or payload.keys() != {"version", "identity", "job"}
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["identity"] != self.identity
        ):
            raise ValueError("saved charge job identity or format differs")
        self._last = raw
        return None if payload["job"] is None else ChargeJob.restore(payload["job"])

    def _encode(self, job: ChargeJob | None) -> bytes:
        return json.dumps({
            "version": 1, "identity": self.identity,
            "job": None if job is None else job.snapshot(),
        }, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()

    def save(self, job: ChargeJob | None) -> None:
        payload = self._encode(job)
        if payload == self._last:
            return
        if len(payload) > MAX_JOB_BYTES:
            raise ValueError("charge job exceeds size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".job-", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
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
                raise StorageCommitUncertainError("job replaced but directory sync failed") from err
            self._last = payload
        finally:
            temporary.unlink(missing_ok=True)
