"""Two things the deployment depends on: durable devices and edge caching.

Both exist only because the application runs serverless, where the process and
its /tmp database are discarded without warning. Neither can be verified by
looking at the code alone, so they are pinned here.
"""
from __future__ import annotations

import pytest

from app.config.settings import settings
from app.database.db import get_conn, write_conn
from app.main import _edge_cache_header
from app.services import device_registry, device_store


class _Request:
    """The three things the cache rule actually reads off a request."""

    def __init__(self, path: str, method: str = "GET", **query: str):
        self.method = method
        self.query_params = query
        self.url = type("U", (), {"path": path})()


class TestEdgeCacheRules:
    def test_expensive_reads_are_cacheable_by_the_cdn(self):
        header = _edge_cache_header(_Request("/api/dashboard/summary"))
        assert header is not None
        assert "s-maxage=" in header and "stale-while-revalidate=" in header

    def test_static_geography_is_cached_far_longer_than_conditions(self):
        geography = _edge_cache_header(_Request("/api/geography/states"))
        conditions = _edge_cache_header(_Request("/api/dashboard/summary"))
        assert geography is not None and conditions is not None

        def s_maxage(header: str) -> int:
            return int(header.split("s-maxage=")[1].split(",")[0])

        assert s_maxage(geography) > s_maxage(conditions)

    @pytest.mark.parametrize(
        "path",
        ["/api/notifications/status", "/api/simulation/state", "/api/alerts", "/api/health"],
    )
    def test_live_state_is_never_cached(self, path):
        """A cached device list or simulation state would be actively wrong."""
        assert _edge_cache_header(_Request(path)) is None

    def test_writes_are_never_cached(self):
        assert _edge_cache_header(_Request("/api/dashboard/summary", method="POST")) is None

    def test_an_explicit_refresh_bypasses_the_cache(self):
        assert _edge_cache_header(_Request("/api/dashboard/summary", refresh="true")) is None

    def test_a_simulation_stamped_read_is_never_cached(self):
        """A CDN hit never reaches the server, so the client must change the
        URL; this is the server honouring that."""
        assert _edge_cache_header(_Request("/api/dashboard/summary", _sim="x")) == "no-store"
        assert _edge_cache_header(_Request("/api/geography/states", _sim="x")) == "no-store"

    def test_conditions_are_not_cached_while_the_simulator_runs(self, monkeypatch):
        """Otherwise the map would keep showing the pre-flood picture."""
        monkeypatch.setattr("app.main._simulation_active", lambda: True)
        assert _edge_cache_header(_Request("/api/dashboard/summary")) == "no-store"
        # Administrative geography does not change under simulation, so it stays cached.
        assert _edge_cache_header(_Request("/api/geography/states")) != "no-store"


class TestDeviceStoreEncoding:
    def test_a_device_survives_a_round_trip(self):
        device = {
            "fcm_token": "tok-123", "label": "Device 1",
            "location_id": "loc_a", "location_name": "Gaurikund",
            "district": "Rudraprayag", "state_id": "uttarakhand", "state_name": "Uttarakhand",
            "latitude": 30.74, "longitude": 79.02, "active": True,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "last_seen_at": "2026-01-01T00:00:00+00:00",
        }
        back = device_store._decode(device_store._encode(device))
        assert back == device

    def test_missing_coordinates_survive_as_null(self):
        back = device_store._decode(
            device_store._encode({"fcm_token": "t", "label": "x", "active": False})
        )
        assert back is not None
        assert back["latitude"] is None and back["active"] is False

    def test_a_document_without_a_token_is_discarded(self):
        """It could not be pushed to, and would occupy a row claiming otherwise."""
        assert device_store._decode({"fields": {"label": {"stringValue": "ghost"}}}) is None

    def test_the_document_id_hides_the_token_but_is_stable(self):
        first = device_store._doc_id("secret-token")
        assert first == device_store._doc_id("secret-token")
        assert "secret-token" not in first
        assert first != device_store._doc_id("other-token")

    def test_mirroring_is_off_unless_configured(self):
        assert settings.device_store == ""
        assert device_store.enabled() is False


class TestColdStartRestore:
    """The whole point: an empty database must refill itself from the store."""

    @staticmethod
    def _mirrored(token: str, label: str, active: bool = True) -> dict:
        return {
            "fcm_token": token, "label": label, "location_id": "loc_a",
            "location_name": "Gaurikund", "district": None, "state_id": None,
            "state_name": None, "latitude": None, "longitude": None, "active": active,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "last_seen_at": "2026-01-01T00:00:00+00:00",
        }

    @pytest.fixture(autouse=True)
    def _empty_devices(self, monkeypatch):
        with write_conn() as conn:
            conn.execute("DELETE FROM devices")
        monkeypatch.setattr(device_registry, "_restored", False)
        yield
        with write_conn() as conn:
            conn.execute("DELETE FROM devices")

    def _enable(self, monkeypatch, devices: list[dict], saved: list | None = None):
        monkeypatch.setattr(device_store, "enabled", lambda: True)
        monkeypatch.setattr(device_store, "load_all", lambda: devices)
        monkeypatch.setattr(device_store, "save", lambda d: (saved is None or saved.append(d)) or True)

    def test_devices_come_back_after_the_database_is_lost(self, monkeypatch):
        self._enable(monkeypatch, [self._mirrored("tok-a", "Device 1")])
        devices = device_registry.list_devices(include_token=True)
        assert [d["fcm_token"] for d in devices] == ["tok-a"]

    def test_a_disabled_device_is_not_resurrected_as_active(self, monkeypatch):
        self._enable(monkeypatch, [self._mirrored("tok-a", "Device 1", active=False)])
        assert device_registry.count_active() == 0

    def test_the_store_is_read_once_per_process(self, monkeypatch):
        calls = []
        monkeypatch.setattr(device_store, "enabled", lambda: True)
        monkeypatch.setattr(device_store, "save", lambda d: True)
        monkeypatch.setattr(
            device_store, "load_all",
            lambda: calls.append(1) or [self._mirrored("tok-a", "Device 1")],
        )
        device_registry.list_devices()
        device_registry.list_devices()
        device_registry.count_active()
        assert len(calls) == 1

    def test_restoring_never_duplicates_a_phone(self, monkeypatch):
        """A phone that re-registered in this process keeps its newer row."""
        self._enable(monkeypatch, [self._mirrored("tok-a", "Device 1")])
        device_registry.register(fcm_token="tok-a", location_id="loc_b", location_name="Joshimath")
        monkeypatch.setattr(device_registry, "_restored", False)
        devices = device_registry.list_devices(include_token=True)
        assert len(devices) == 1
        assert devices[0]["location_name"] == "Joshimath"

    def test_registering_writes_through_to_the_store(self, monkeypatch):
        saved: list[dict] = []
        self._enable(monkeypatch, [], saved)
        device_registry.register(fcm_token="tok-new", location_id="loc_a", location_name="Gaurikund")
        assert [d["fcm_token"] for d in saved] == ["tok-new"]
        assert saved[0]["location_name"] == "Gaurikund"

    def test_disabling_writes_through_so_it_stays_disabled(self, monkeypatch):
        saved: list[dict] = []
        self._enable(monkeypatch, [], saved)
        device = device_registry.register(fcm_token="tok-x", location_id="loc_a")
        saved.clear()
        device_registry.set_active(device["id"], False)
        assert len(saved) == 1 and saved[0]["active"] is False

    def test_a_dead_token_is_retired_in_the_store_too(self, monkeypatch):
        saved: list[dict] = []
        self._enable(monkeypatch, [], saved)
        device_registry.register(fcm_token="tok-dead", location_id="loc_a")
        saved.clear()
        device_registry.deactivate_tokens(["tok-dead"])
        assert len(saved) == 1 and saved[0]["active"] is False

    def test_a_broken_store_does_not_break_registration(self, monkeypatch):
        """Firestore being down must not stop a phone registering."""
        monkeypatch.setattr(device_store, "enabled", lambda: True)
        monkeypatch.setattr(device_store, "load_all", lambda: [])
        monkeypatch.setattr(
            device_store, "save",
            lambda d: (_ for _ in ()).throw(RuntimeError("firestore down")),
        )
        device = device_registry.register(fcm_token="tok-y", location_id="loc_a")
        assert device["token_hint"]
        with get_conn() as conn:
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM devices WHERE fcm_token='tok-y'"
            ).fetchone()["n"] == 1


class TestThePhoneRepairsItsOwnRegistration:
    """The phone is the only participant that cannot lose its token.

    Server-side durability (Firestore) needs the project owner to switch it on.
    This path needs nothing, so it is what actually protects a demonstration,
    and it is worth pinning even though it lives in the frontend.
    """

    FRONTEND = __import__("pathlib").Path(__file__).resolve().parents[2] / "frontend" / "src"

    def _source(self, *parts: str) -> str:
        return (self.FRONTEND.joinpath(*parts)).read_text(encoding="utf-8")

    def test_a_successful_registration_is_remembered_on_the_phone(self):
        source = self._source("services", "notifications.ts")
        assert "rememberRegistration(target)" in source
        assert "floodsafe.registration" in source

    def test_reaffirming_never_prompts(self):
        """A permission prompt needs a tap; this runs on a timer."""
        source = self._source("services", "notifications.ts")
        body = source.split("export async function reaffirmRegistration")[1]
        assert "requestPermission" not in body
        assert "permissionState() !== 'granted'" in body

    def test_the_app_reaffirms_on_load_and_on_a_timer(self):
        source = self._source("App.tsx")
        assert "reaffirmRegistration" in source
        assert "setInterval" in source and "visibilitychange" in source
