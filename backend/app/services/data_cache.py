"""SQLite-backed cache for external API responses.

The cache is what makes the LIVE -> CACHE -> DEMO fallback chain possible:
expired entries are kept rather than deleted, so that when an upstream API is
unreachable the platform can still serve the last known real observation and
label it honestly as stale.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config.logging_config import (
    EV_CACHE_HIT,
    EV_CACHE_MISS,
    EV_CACHE_STALE,
    get_logger,
)
from app.database.db import get_conn, jdump, jload, write_conn

log = get_logger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(slots=True)
class CachedValue:
    payload: Any
    source: str
    fetched_at: datetime
    expires_at: datetime

    @property
    def age_seconds(self) -> float:
        return max(0.0, (utcnow() - self.fetched_at).total_seconds())

    @property
    def age_minutes(self) -> float:
        return self.age_seconds / 60.0

    @property
    def expired(self) -> bool:
        return utcnow() >= self.expires_at


def make_key(source: str, **parts: Any) -> str:
    """Stable cache key. Coordinates are rounded so that near-identical
    requests share an entry instead of thrashing the upstream API."""
    bits = []
    for k in sorted(parts):
        v = parts[k]
        if isinstance(v, float):
            v = f"{v:.4f}"
        bits.append(f"{k}={v}")
    raw = f"{source}|{'|'.join(bits)}"
    if len(raw) > 160:
        raw = f"{source}|{hashlib.sha256(raw.encode()).hexdigest()[:32]}"
    return raw


def get(key: str, *, allow_expired: bool = False) -> CachedValue | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT source, payload, fetched_at, expires_at FROM api_cache WHERE cache_key=?",
            (key,),
        ).fetchone()
    if row is None:
        log.debug("%s %s", EV_CACHE_MISS, key)
        return None

    cv = CachedValue(
        payload=jload(row["payload"]),
        source=row["source"],
        fetched_at=parse_iso(row["fetched_at"]),
        expires_at=parse_iso(row["expires_at"]),
    )
    if cv.expired and not allow_expired:
        log.debug("%s %s (age %.1f min)", EV_CACHE_STALE, key, cv.age_minutes)
        return None

    try:
        with write_conn() as conn:
            conn.execute(
                "UPDATE api_cache SET hit_count = hit_count + 1 WHERE cache_key=?", (key,)
            )
    except Exception:  # a hit counter must never break a request
        pass

    tag = EV_CACHE_STALE if cv.expired else EV_CACHE_HIT
    log.info("%s %s (age %.1f min)", tag, key, cv.age_minutes)
    return cv


def put(key: str, payload: Any, *, source: str, ttl_seconds: int) -> None:
    now = utcnow()
    with write_conn() as conn:
        conn.execute(
            """INSERT INTO api_cache (cache_key, source, payload, fetched_at, expires_at, hit_count)
               VALUES (?,?,?,?,?,0)
               ON CONFLICT(cache_key) DO UPDATE SET
                   payload=excluded.payload,
                   source=excluded.source,
                   fetched_at=excluded.fetched_at,
                   expires_at=excluded.expires_at""",
            (
                key,
                source,
                jdump(payload),
                iso(now),
                iso(now + timedelta(seconds=ttl_seconds)),
            ),
        )


def stats() -> dict[str, Any]:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM api_cache").fetchone()["c"]
        hits = conn.execute(
            "SELECT COALESCE(SUM(hit_count),0) s FROM api_cache"
        ).fetchone()["s"]
        by_source = conn.execute(
            """SELECT source, COUNT(*) n, COALESCE(SUM(hit_count),0) hits,
                      MAX(fetched_at) newest
               FROM api_cache GROUP BY source ORDER BY source"""
        ).fetchall()
        live = conn.execute(
            "SELECT COUNT(*) c FROM api_cache WHERE expires_at > ?", (iso(utcnow()),)
        ).fetchone()["c"]
    return {
        "entries": total,
        "unexpired_entries": live,
        "total_hits": hits,
        "by_source": [
            {
                "source": r["source"],
                "entries": r["n"],
                "hits": r["hits"],
                "newest": r["newest"],
            }
            for r in by_source
        ],
    }


def clear(source: str | None = None) -> int:
    with write_conn() as conn:
        if source:
            cur = conn.execute("DELETE FROM api_cache WHERE source=?", (source,))
        else:
            cur = conn.execute("DELETE FROM api_cache")
        return cur.rowcount
