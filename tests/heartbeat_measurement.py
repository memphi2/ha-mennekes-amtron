"""Constant-space observation of delivered heartbeats, including silent tails."""

from dataclasses import dataclass


@dataclass
class HeartbeatMeasurement:
    last_delivery: float
    deliveries: int = 0
    max_gap: float = 0.0

    def delivered(self, now: float) -> None:
        self.max_gap = max(self.max_gap, now - self.last_delivery)
        self.last_delivery = now
        self.deliveries += 1

    def maximum_gap(self, now: float) -> float:
        return max(self.max_gap, now - self.last_delivery)
