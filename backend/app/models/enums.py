"""Domain enumerations shared across services and API schemas."""
from __future__ import annotations

from enum import Enum


class Freshness(str, Enum):
    """Where a value actually came from.

    This is surfaced all the way to the UI so that live, cached, simulated and
    demonstration values are never displayed as if they were the same thing.
    """

    LIVE = "LIVE"              # fetched from the external API just now
    CACHED = "CACHED"          # served from SQLite cache, still within TTL
    STALE_CACHE = "STALE_CACHE"  # cache expired but used because the API failed
    DEMO = "DEMO"              # deterministic placeholder, external source unavailable
    SIMULATION = "SIMULATION"  # value overridden by the scenario simulator


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    EXTREME = "EXTREME"

    @property
    def order(self) -> int:
        return _RISK_ORDER[self]


_RISK_ORDER = {
    RiskLevel.SAFE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MODERATE: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.EXTREME: 4,
}


class Confidence(str, Enum):
    """Confidence in the DATA, not in the occurrence of a flood."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Impact(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SourceStatus(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class RunMode(str, Enum):
    LIVE = "LIVE"
    SIMULATION = "SIMULATION"
