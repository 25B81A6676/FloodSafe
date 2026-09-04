"""Structured, human-readable logging.

The backend deliberately logs the data-pipeline events an evaluator needs to
see: which source was hit, whether it was a cache hit or miss, when a fallback
was activated, and when risk was computed.
"""
from __future__ import annotations

import logging
import sys

_LEVEL_COLORS = {
    "DEBUG": "\033[38;5;244m",
    "INFO": "\033[38;5;39m",
    "WARNING": "\033[38;5;214m",
    "ERROR": "\033[38;5;196m",
    "CRITICAL": "\033[48;5;196;38;5;231m",
}
_RESET = "\033[0m"
_DIM = "\033[38;5;244m"

# Event tags used across services so logs read as a pipeline trace.
EV_FETCH = "FETCH"
EV_CACHE_HIT = "CACHE-HIT"
EV_CACHE_MISS = "CACHE-MISS"
EV_CACHE_STALE = "CACHE-STALE"
EV_API_FAIL = "API-FAIL"
EV_FALLBACK = "FALLBACK"
EV_DEMO = "DEMO-DATA"
EV_RISK = "RISK"
EV_SIM = "SIMULATION"
EV_DB = "DB"


class _Formatter(logging.Formatter):
    def __init__(self, use_color: bool) -> None:
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        name = record.name.replace("app.services.", "").replace("app.api.routes.", "")
        level = record.levelname
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        if self.use_color:
            c = _LEVEL_COLORS.get(level, "")
            return f"{_DIM}{ts}{_RESET} {c}{level:<8}{_RESET} {_DIM}{name:<22}{_RESET} {msg}"
        return f"{ts} {level:<8} {name:<22} {msg}"


def configure_logging(level: str = "INFO") -> None:
    use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter(use_color))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # External libraries are noisy at INFO.
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
