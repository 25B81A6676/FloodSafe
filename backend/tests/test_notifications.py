"""Flood-alert notifications: registration, targeting, transitions, safety.

Every test here runs with Firebase unconfigured and the network disabled, which
is deliberate: the interesting behaviour is the DECIDING - when an alert should
fire, who it should reach, and when it must be suppressed - and none of that
needs a real Firebase project. Where the send itself matters, the transport is
replaced with a recorder so the assertion is about what would have been sent.
"""
from __future__ import annotations

import pytest

from app.config.settings import settings
from app.database.db import write_conn
from app.models.enums import RunMode
from app.services import alert_dispatch, device_registry, fcm_client

TOKEN_A = "fcm-token-device-a-0000000000000000"
TOKEN_B = "fcm-token-device-b-1111111111111111"
TOKEN_C = "fcm-token-device-c-2222222222222222"

GAURIKUND = {
    "id": "loc_gaurikund", "name": "Gaurikund", "district": "Rudraprayag",
    "state_id": "uttarakhand", "state_name": "Uttarakhand",
    "latitude": 30.6417, "longitude": 79.0231,
}
RISHIKESH = {
    "id": "loc_rishikesh", "name": "Rishikesh", "district": "Dehradun",
    "state_id": "uttarakhand", "state_name": "Uttarakhand",
    "latitude": 30.0869, "longitude": 78.2676,
}


def risk(level: str, score: float = 70.0) -> dict:
    return {"risk_level": level, "risk_score": score, "risk_score_precise": score}


@pytest.fixture(autouse=True)
def _clean_notification_tables():
    """Each test starts with no devices and no dispatch history."""
    with write_conn() as conn:
        conn.execute("DELETE FROM devices")
        conn.execute("DELETE FROM alert_dispatches")
    yield
    with write_conn() as conn:
        conn.execute("DELETE FROM devices")
        conn.execute("DELETE FROM alert_dispatches")


@pytest.fixture
def sent(monkeypatch):
    """Replace the FCM transport with a recorder and allow real sends."""
    calls: list[dict] = []

    async def fake_send(tokens, *, title, body, data, severity, click_url):
        calls.append({
            "tokens": list(tokens), "title": title, "body": body,
            "data": data, "severity": severity, "click_url": click_url,
        })
        return fcm_client.SendResult(status="SENT", accepted=len(tokens))

    monkeypatch.setattr(fcm_client, "send_to_tokens", fake_send)
    monkeypatch.setattr(alert_dispatch.fcm_client, "send_to_tokens", fake_send)
    # Test mode is the safe default; a test that asserts a real send must opt out.
    monkeypatch.setattr(settings, "flood_alert_test_mode", False)
    return calls


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------
class TestRegistration:
    def test_register_one_device(self):
        device = device_registry.register(fcm_token=TOKEN_A, **{
            "location_id": GAURIKUND["id"], "location_name": "Gaurikund",
            "district": "Rudraprayag", "state_id": "uttarakhand",
        })
        assert device["location_name"] == "Gaurikund"
        assert device["active"] is True
        assert device_registry.count_active() == 1

    def test_register_multiple_devices(self):
        for token in (TOKEN_A, TOKEN_B, TOKEN_C):
            device_registry.register(fcm_token=token, location_id=GAURIKUND["id"])
        assert device_registry.count_active() == 3

    def test_registering_the_same_token_twice_updates_rather_than_duplicates(self):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"],
                                 location_name="Gaurikund")
        first = device_registry.list_devices()[0]
        device_registry.register(fcm_token=TOKEN_A, location_id=RISHIKESH["id"],
                                 location_name="Rishikesh")
        devices = device_registry.list_devices()
        assert len(devices) == 1, "a re-registered phone must not create a second row"
        assert devices[0]["id"] == first["id"]
        assert devices[0]["location_name"] == "Rishikesh", "location must update"

    def test_a_device_can_be_disabled_and_re_enabled(self):
        device = device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        device_registry.set_active(device["id"], False)
        assert device_registry.count_active() == 0
        device_registry.set_active(device["id"], True)
        assert device_registry.count_active() == 1

    def test_the_registration_token_is_never_exposed_by_default(self):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        device = device_registry.list_devices()[0]
        assert "fcm_token" not in device
        assert TOKEN_A not in device["token_hint"]
        assert device["token_hint"].startswith(TOKEN_A[:6])

    def test_dead_tokens_are_deactivated_not_deleted(self):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        device_registry.deactivate_tokens([TOKEN_A])
        devices = device_registry.list_devices()
        assert len(devices) == 1, "an unreachable phone stays visible"
        assert devices[0]["active"] is False


# --------------------------------------------------------------------------
# Targeting
# --------------------------------------------------------------------------
class TestTargeting:
    def test_only_devices_at_the_affected_location_are_targeted(self):
        for token in (TOKEN_A, TOKEN_B):
            device_registry.register(fcm_token=token, location_id=GAURIKUND["id"],
                                     location_name="Gaurikund", district="Rudraprayag")
        device_registry.register(fcm_token=TOKEN_C, location_id=RISHIKESH["id"],
                                 location_name="Rishikesh", district="Dehradun")

        targets = device_registry.find_targets(location_id=GAURIKUND["id"])
        tokens = {t["fcm_token"] for t in targets}
        assert tokens == {TOKEN_A, TOKEN_B}
        assert TOKEN_C not in tokens, "Rishikesh must not receive a Gaurikund alert"

    def test_disabled_devices_are_never_targeted(self):
        device = device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        device_registry.set_active(device["id"], False)
        assert device_registry.find_targets(location_id=GAURIKUND["id"]) == []

    def test_district_scope_widens_the_audience(self):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"],
                                 district="Rudraprayag")
        device_registry.register(fcm_token=TOKEN_B, location_id="loc_other",
                                 district="Rudraprayag")
        targets = device_registry.find_targets(
            location_id=GAURIKUND["id"], district="Rudraprayag", scope="district"
        )
        assert len(targets) == 2


# --------------------------------------------------------------------------
# Transition rules
# --------------------------------------------------------------------------
class TestTransitionRules:
    @pytest.mark.parametrize(
        "previous,new,expected",
        [
            ("LOW", "MODERATE", False),
            ("MODERATE", "HIGH", True),
            ("HIGH", "HIGH", False),
            ("HIGH", "EXTREME", True),
            ("EXTREME", "EXTREME", False),
            ("EXTREME", "MODERATE", False),
            ("EXTREME", "HIGH", True),   # still a transition INTO a trigger level
            (None, "HIGH", True),        # first ever assessment already severe
            (None, "SAFE", False),
            ("SAFE", "SAFE", False),
        ],
    )
    def test_only_transitions_into_high_or_extreme_trigger(self, previous, new, expected):
        assert alert_dispatch.should_trigger(previous, new) is expected


@pytest.mark.asyncio
class TestDispatch:
    async def test_moderate_to_high_notifies_the_right_devices(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"],
                                 location_name="Gaurikund")
        device_registry.register(fcm_token=TOKEN_C, location_id=RISHIKESH["id"],
                                 location_name="Rishikesh")

        record = await alert_dispatch.dispatch_emergency(
            GAURIKUND, risk("HIGH", 64), "MODERATE", mode=RunMode.LIVE
        )
        assert record is not None and record["status"] == "SENT"
        assert len(sent) == 1
        assert sent[0]["tokens"] == [TOKEN_A]
        assert "HIGH" in sent[0]["title"]
        assert "Gaurikund" in sent[0]["body"]

    async def test_low_to_moderate_sends_nothing(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        record = await alert_dispatch.dispatch_emergency(
            GAURIKUND, risk("MODERATE", 45), "LOW", mode=RunMode.LIVE
        )
        assert record is None
        assert sent == []

    async def test_high_to_high_does_not_notify_again(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await alert_dispatch.dispatch_emergency(GAURIKUND, risk("HIGH", 64), "MODERATE",
                                                mode=RunMode.LIVE)
        assert len(sent) == 1
        await alert_dispatch.dispatch_emergency(GAURIKUND, risk("HIGH", 66), "HIGH",
                                                mode=RunMode.LIVE)
        assert len(sent) == 1, "the level did not change; no second notification"

    async def test_high_to_extreme_escalates(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await alert_dispatch.dispatch_emergency(GAURIKUND, risk("HIGH", 64), "MODERATE",
                                                mode=RunMode.LIVE)
        await alert_dispatch.dispatch_emergency(GAURIKUND, risk("EXTREME", 88), "HIGH",
                                                mode=RunMode.LIVE)
        assert len(sent) == 2, "an escalation is a new alert even inside the cooldown"
        assert "EXTREME" in sent[1]["title"]

    async def test_extreme_to_extreme_does_not_notify_again(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await alert_dispatch.dispatch_emergency(GAURIKUND, risk("EXTREME", 88), "HIGH",
                                                mode=RunMode.LIVE)
        await alert_dispatch.dispatch_emergency(GAURIKUND, risk("EXTREME", 90), "EXTREME",
                                                mode=RunMode.LIVE)
        assert len(sent) == 1

    async def test_cooldown_suppresses_a_flapping_repeat(self, sent):
        """HIGH -> MODERATE -> HIGH must not re-notify inside the window."""
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await alert_dispatch.dispatch_emergency(GAURIKUND, risk("HIGH", 64), "MODERATE",
                                                mode=RunMode.LIVE)
        assert len(sent) == 1
        record = await alert_dispatch.dispatch_emergency(
            GAURIKUND, risk("HIGH", 63), "MODERATE", mode=RunMode.LIVE
        )
        assert len(sent) == 1, "still inside the cooldown"
        assert record["status"] == "SUPPRESSED_COOLDOWN"

    async def test_cooldown_is_configurable_not_hardcoded(self, sent, monkeypatch):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await alert_dispatch.dispatch_emergency(GAURIKUND, risk("HIGH", 64), "MODERATE",
                                                mode=RunMode.LIVE)
        monkeypatch.setattr(settings, "alert_cooldown_minutes", 0)
        await alert_dispatch.dispatch_emergency(GAURIKUND, risk("HIGH", 64), "MODERATE",
                                                mode=RunMode.LIVE)
        assert len(sent) == 2, "a zero cooldown must disable suppression"

    async def test_one_dead_device_does_not_block_the_others(self, monkeypatch):
        async def partial_send(tokens, **kwargs):
            return fcm_client.SendResult(
                status="PARTIAL", accepted=len(tokens) - 1, rejected=1,
                dead_tokens=[tokens[-1]],
            )

        monkeypatch.setattr(alert_dispatch.fcm_client, "send_to_tokens", partial_send)
        monkeypatch.setattr(settings, "flood_alert_test_mode", False)
        for token in (TOKEN_A, TOKEN_B, TOKEN_C):
            device_registry.register(fcm_token=token, location_id=GAURIKUND["id"])

        record = await alert_dispatch.dispatch_emergency(
            GAURIKUND, risk("HIGH", 64), "MODERATE", mode=RunMode.LIVE
        )
        assert record["accepted"] == 2, "the healthy devices were still sent to"
        assert record["rejected"] == 1
        assert device_registry.count_active() == 2, "the dead token was retired"

    async def test_a_messaging_failure_never_raises(self, monkeypatch):
        async def boom(*args, **kwargs):
            raise RuntimeError("FCM exploded")

        monkeypatch.setattr(alert_dispatch.fcm_client, "send_to_tokens", boom)
        monkeypatch.setattr(settings, "flood_alert_test_mode", False)
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        # on_risk_assessed is the pipeline hook; it must swallow everything.
        await alert_dispatch.on_risk_assessed(
            GAURIKUND, risk("HIGH", 64), "MODERATE", mode=RunMode.LIVE
        )


@pytest.mark.asyncio
class TestSafety:
    async def test_simulation_never_sends_a_real_emergency_alert(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        record = await alert_dispatch.dispatch_emergency(
            GAURIKUND, risk("EXTREME", 95), "MODERATE", mode=RunMode.SIMULATION
        )
        assert sent == [], "a scenario is not an emergency"
        assert record["status"] == "SUPPRESSED_SIMULATION"

    async def test_simulation_is_blocked_even_with_test_mode_off(self, sent, monkeypatch):
        """Enabling real alerts must never also arm the simulator."""
        monkeypatch.setattr(settings, "flood_alert_test_mode", False)
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        record = await alert_dispatch.dispatch_emergency(
            GAURIKUND, risk("EXTREME", 95), "MODERATE", mode=RunMode.SIMULATION
        )
        assert sent == []
        assert record["status"] == "SUPPRESSED_SIMULATION"

    async def test_test_mode_suppresses_real_pushes_but_records_the_transition(self, monkeypatch):
        calls: list = []

        async def fake_send(tokens, **kwargs):
            calls.append(tokens)
            return fcm_client.SendResult(status="SENT", accepted=len(tokens))

        monkeypatch.setattr(alert_dispatch.fcm_client, "send_to_tokens", fake_send)
        monkeypatch.setattr(settings, "flood_alert_test_mode", True)
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])

        record = await alert_dispatch.dispatch_emergency(
            GAURIKUND, risk("HIGH", 64), "MODERATE", mode=RunMode.LIVE
        )
        assert calls == [], "test mode must not send a real push"
        assert record["status"] == "SUPPRESSED_TEST_MODE"
        assert record["targeted"] == 1, "but it still reports who WOULD have been reached"

    async def test_test_alert_is_clearly_labelled_and_always_allowed(self, monkeypatch):
        calls: list[dict] = []

        async def fake_send(tokens, *, title, body, **kwargs):
            calls.append({"tokens": list(tokens), "title": title, "body": body})
            return fcm_client.SendResult(status="SENT", accepted=len(tokens))

        monkeypatch.setattr(alert_dispatch.fcm_client, "send_to_tokens", fake_send)
        monkeypatch.setattr(settings, "flood_alert_test_mode", True)
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])

        result = await alert_dispatch.send_test_alert()
        assert result["status"] == "SENT"
        assert "TEST" in calls[0]["title"]
        assert "No emergency is occurring" in calls[0]["body"]


class TestUnconfigured:
    """FloodSafe must be fully usable with no Firebase project at all."""

    def test_messaging_reports_itself_unconfigured(self):
        assert fcm_client.configured() is False
        status = fcm_client.config_status()
        assert status["configured"] is False
        assert status["project_id"] is None

    def test_no_private_key_material_is_ever_exposed(self):
        status = fcm_client.config_status()
        assert "private_key" not in status
        assert not any("PRIVATE" in str(v).upper() for v in status.values() if v)

    def test_token_masking_never_reveals_the_token(self):
        hint = fcm_client.mask_token(TOKEN_A)
        assert TOKEN_A not in hint
        assert len(hint) < len(TOKEN_A)

    @pytest.mark.asyncio
    async def test_sending_without_configuration_reports_it_honestly(self):
        result = await fcm_client.send_to_tokens(
            [TOKEN_A], title="t", body="b", data={}, severity="HIGH", click_url="/",
        )
        # Never a fake success.
        assert result.status in {"NOT_CONFIGURED", "DISABLED"}
        assert result.accepted == 0
        assert result.ok is False


class TestNotificationApi:
    def test_status_endpoint_works_without_firebase(self, client):
        body = client.get("/api/notifications/status").json()
        assert body["configured"] is False
        assert body["active_devices"] == 0
        assert "accepted" in body["note"]

    def test_register_endpoint_upserts(self, client):
        payload = {
            "fcm_token": TOKEN_A, "location_id": GAURIKUND["id"],
            "location_name": "Gaurikund", "district": "Rudraprayag",
            "state_id": "uttarakhand", "state_name": "Uttarakhand",
            "latitude": 30.6417, "longitude": 79.0231,
        }
        first = client.post("/api/notifications/register", json=payload)
        assert first.status_code == 200
        assert first.json()["registered"] is True
        # It says plainly that nothing can be delivered yet.
        assert first.json()["notice"] is not None

        client.post("/api/notifications/register", json=payload)
        assert client.get("/api/notifications/status").json()["total_devices"] == 1

    def test_register_rejects_a_malformed_token(self, client):
        response = client.post("/api/notifications/register", json={"fcm_token": "short"})
        assert response.status_code == 422

    def test_status_never_returns_a_registration_token(self, client):
        client.post("/api/notifications/register", json={
            "fcm_token": TOKEN_A, "location_id": GAURIKUND["id"],
        })
        raw = client.get("/api/notifications/status").text
        assert TOKEN_A not in raw

    def test_test_endpoint_reports_missing_configuration(self, client):
        client.post("/api/notifications/register", json={
            "fcm_token": TOKEN_A, "location_id": GAURIKUND["id"],
        })
        response = client.post("/api/notifications/test")
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"].lower()

    def test_public_config_never_leaks_the_service_account(self, client):
        raw = client.get("/api/notifications/config").text
        for secret in ("private_key", "BEGIN PRIVATE KEY", "client_email"):
            assert secret not in raw


# ==========================================================================
# Simulator demonstration alerts
# ==========================================================================
EPISODE = "episode-one"


@pytest.mark.asyncio
class TestSimulationDemoAlerts:
    """The simulator drives the SELECTED location; its phones get a real,
    clearly-labelled FCM push when that location enters HIGH or EXTREME."""

    async def _sim(self, level: str, score: float, *, episode: str = EPISODE, location=GAURIKUND):
        return await alert_dispatch.dispatch_simulation(
            location, risk(level, score), episode_id=episode
        )

    async def test_entering_high_sends_a_high_demo_alert(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        result = await self._sim("HIGH", 64)
        assert result["status"] == "SENT"
        assert len(sent) == 1
        assert sent[0]["severity"] == "HIGH"
        assert sent[0]["title"] == "⚠️ FLOODSAFE HIGH SIMULATION ALERT"

    async def test_entering_extreme_sends_an_extreme_demo_alert(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await self._sim("EXTREME", 87)
        assert len(sent) == 1
        assert sent[0]["title"] == "🚨 FLOODSAFE EXTREME SIMULATION ALERT"

    async def test_below_high_sends_nothing(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        for level, score in (("SAFE", 10), ("LOW", 30), ("MODERATE", 50)):
            assert await self._sim(level, score) is None
        assert sent == []

    async def test_high_to_high_does_not_spam(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await self._sim("HIGH", 61)
        await self._sim("HIGH", 70)
        await self._sim("HIGH", 79)
        assert len(sent) == 1

    async def test_extreme_to_extreme_does_not_spam(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await self._sim("EXTREME", 81)
        await self._sim("EXTREME", 90)
        assert len(sent) == 1

    async def test_high_to_extreme_sends_the_upgrade(self, sent):
        """The exact sequence from the brief: 40->50 nothing, 50->61 HIGH,
        61->70 nothing, 70->81 EXTREME, 81->90 nothing."""
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        for level, score in (("LOW", 40), ("MODERATE", 50), ("HIGH", 61),
                             ("HIGH", 70), ("EXTREME", 81), ("EXTREME", 90)):
            await self._sim(level, score)
        assert [c["severity"] for c in sent] == ["HIGH", "EXTREME"]

    async def test_falling_back_to_high_after_extreme_does_not_alert(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await self._sim("EXTREME", 90)
        await self._sim("HIGH", 70)
        assert len(sent) == 1

    async def test_flapping_within_one_episode_does_not_repeat(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await self._sim("HIGH", 62)
        await self._sim("MODERATE", 55)
        await self._sim("HIGH", 63)
        assert len(sent) == 1

    async def test_a_new_episode_can_alert_again(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await self._sim("HIGH", 64, episode="first")
        await self._sim("HIGH", 64, episode="first")
        await self._sim("HIGH", 64, episode="second")
        assert len(sent) == 2, "after Exit Simulation the demonstration must be repeatable"

    async def test_all_devices_at_the_location_are_targeted(self, sent):
        for token in (TOKEN_A, TOKEN_B, TOKEN_C):
            device_registry.register(fcm_token=token, location_id=GAURIKUND["id"])
        result = await self._sim("EXTREME", 87)
        assert sorted(sent[0]["tokens"]) == sorted([TOKEN_A, TOKEN_B, TOKEN_C])
        assert result["targeted"] == 3 and result["accepted"] == 3

    async def test_wrong_location_devices_are_excluded(self, sent):
        """Phones at Gaurikund receive; a phone at Rishikesh does not."""
        for token in (TOKEN_A, TOKEN_B):
            device_registry.register(fcm_token=token, location_id=GAURIKUND["id"])
        device_registry.register(fcm_token=TOKEN_C, location_id=RISHIKESH["id"])
        await self._sim("EXTREME", 87)
        assert TOKEN_C not in sent[0]["tokens"]
        assert len(sent[0]["tokens"]) == 2

    async def test_no_registered_devices_is_reported_not_faked(self, sent):
        device_registry.register(fcm_token=TOKEN_C, location_id=RISHIKESH["id"])
        result = await self._sim("EXTREME", 87)
        assert sent == []
        assert result["status"] == "NO_TARGETS"
        assert result["accepted"] == 0

    async def test_one_invalid_token_does_not_block_the_others(self, monkeypatch):
        async def partial(tokens, **kwargs):
            return fcm_client.SendResult(
                status="PARTIAL", accepted=len(tokens) - 1, rejected=1, dead_tokens=[tokens[-1]]
            )

        monkeypatch.setattr(alert_dispatch.fcm_client, "send_to_tokens", partial)
        for token in (TOKEN_A, TOKEN_B, TOKEN_C):
            device_registry.register(fcm_token=token, location_id=GAURIKUND["id"])
        result = await self._sim("EXTREME", 87)
        assert result["accepted"] == 2
        assert result["invalid_tokens"] == 1
        assert device_registry.count_active() == 2, "the dead token is deactivated"

    async def test_a_failed_send_is_retried_on_the_next_change(self, monkeypatch):
        calls = []

        async def failing_then_ok(tokens, **kwargs):
            calls.append(tokens)
            if len(calls) == 1:
                return fcm_client.SendResult(status="FAILED", rejected=len(tokens), detail="blip")
            return fcm_client.SendResult(status="SENT", accepted=len(tokens))

        monkeypatch.setattr(alert_dispatch.fcm_client, "send_to_tokens", failing_then_ok)
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        first = await self._sim("EXTREME", 87)
        second = await self._sim("EXTREME", 88)
        assert first["status"] == "FAILED"
        assert second["status"] == "SENT", "a network blip must not cost the demo its alert"

    async def test_simulation_alert_is_clearly_labelled(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await self._sim("EXTREME", 87)
        title, body = sent[0]["title"], sent[0]["body"]
        assert "SIMULATION" in title
        assert "SIH DEMONSTRATION — NOT A REAL EMERGENCY" in body
        assert "Gaurikund" in body and "Uttarakhand" in body
        assert "Risk Score: 87" in body
        assert sent[0]["data"]["kind"] == "SIMULATION"

    async def test_disabled_by_configuration_sends_nothing(self, sent, monkeypatch):
        monkeypatch.setattr(settings, "simulation_alerts_enabled", False)
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        assert await self._sim("EXTREME", 87) is None
        assert sent == []

    async def test_the_real_risk_hook_never_pages_during_simulation(self, sent):
        """Overrides apply to every location, so the generic hook must stay out."""
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await alert_dispatch.on_risk_assessed(
            GAURIKUND, risk("EXTREME", 95), "MODERATE", mode=RunMode.SIMULATION
        )
        assert sent == []
        assert alert_dispatch.recent_dispatches() == [], "no ledger noise either"

    async def test_real_data_alerts_still_work(self, sent):
        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        await alert_dispatch.on_risk_assessed(
            GAURIKUND, risk("HIGH", 64), "MODERATE", mode=RunMode.LIVE
        )
        assert len(sent) == 1
        assert sent[0]["data"]["kind"] == "EMERGENCY"
        assert "SIMULATION" not in sent[0]["title"]


class TestSimulationEpisodes:
    def test_starting_a_simulation_opens_an_episode(self):
        from app.services import simulation_service

        first = simulation_service.run(overrides={"rainfall_intensity": 50})
        assert first["active"] and first["episode_id"]

        second = simulation_service.run(overrides={"rainfall_intensity": 80})
        assert second["episode_id"] == first["episode_id"], "moving a slider keeps the episode"

        simulation_service.reset()
        third = simulation_service.run(overrides={"rainfall_intensity": 80})
        assert third["episode_id"] != first["episode_id"], "a reset starts a new episode"


class TestSimulatorEndpointAlerts:
    """End to end through /api/simulation/run with the real risk engine."""

    def _register_gaurikund(self, *tokens):
        for token in tokens:
            device_registry.register(fcm_token=token, location_id=GAURIKUND["id"])

    def test_extreme_scenario_pages_only_the_simulated_location(self, client, sent):
        self._register_gaurikund(TOKEN_A, TOKEN_B)
        device_registry.register(fcm_token=TOKEN_C, location_id=RISHIKESH["id"])

        body = client.post("/api/simulation/run", json={
            "scenario_id": "extreme_flash_flood", "location_id": GAURIKUND["id"],
        }).json()

        level = body["monitoring"]["risk"]["risk_level"]
        assert level in {"HIGH", "EXTREME"}, "the existing engine drives the level"
        assert body["demo_alert"]["status"] == "SENT"
        assert body["demo_alert"]["risk_level"] == level
        assert len(sent) == 1
        assert sorted(sent[0]["tokens"]) == sorted([TOKEN_A, TOKEN_B])
        assert TOKEN_C not in sent[0]["tokens"]

    def test_repeated_runs_do_not_spam(self, client, sent):
        self._register_gaurikund(TOKEN_A)
        payload = {"scenario_id": "extreme_flash_flood", "location_id": GAURIKUND["id"]}
        for _ in range(3):
            client.post("/api/simulation/run", json=payload)
        assert len(sent) == 1

    def test_exit_simulation_sends_nothing_and_a_new_run_can_alert_again(self, client, sent):
        self._register_gaurikund(TOKEN_A)
        payload = {"scenario_id": "extreme_flash_flood", "location_id": GAURIKUND["id"]}
        client.post("/api/simulation/run", json=payload)
        assert len(sent) == 1

        reset = client.post("/api/simulation/reset", params={"location_id": GAURIKUND["id"]})
        assert reset.status_code == 200
        assert len(sent) == 1, "exiting simulation must not notify anyone"

        client.post("/api/simulation/run", json=payload)
        assert len(sent) == 2, "a fresh simulation can demonstrate again"

    def test_firebase_unavailable_never_breaks_the_simulator(self, client):
        """No monkeypatch: Firebase is genuinely unconfigured in the test env."""
        self._register_gaurikund(TOKEN_A)
        response = client.post("/api/simulation/run", json={
            "scenario_id": "extreme_flash_flood", "location_id": GAURIKUND["id"],
        })
        assert response.status_code == 200
        body = response.json()
        assert body["active"] is True
        assert body["monitoring"]["risk"]["mode"] == "SIMULATION"
        assert body["demo_alert"]["status"] == "NOT_CONFIGURED"
        assert body["demo_alert"]["accepted"] == 0, "never a fake success"

    def test_existing_test_alert_still_labelled(self, sent):
        import asyncio

        device_registry.register(fcm_token=TOKEN_A, location_id=GAURIKUND["id"])
        result = asyncio.run(alert_dispatch.send_test_alert())
        assert result["status"] == "SENT"
        assert sent[0]["title"] == "🧪 FLOODSAFE TEST ALERT"
        assert "notification delivery test" in sent[0]["body"]


class TestRegistrationGeography:
    def test_server_resolves_geography_from_the_location(self, client):
        """The page used to send a state NAME in the state_id slot."""
        client.post("/api/notifications/register", json={
            "fcm_token": TOKEN_A, "location_id": GAURIKUND["id"],
            "state_id": "Uttarakhand", "state_name": "Uttarakhand",
        })
        device = device_registry.list_devices()[0]
        assert device["state_id"] == "uttarakhand"
        assert device["location_name"] == "Gaurikund"

    def test_click_path_is_relative_and_restores_the_scope(self):
        ctx = alert_dispatch.location_context(GAURIKUND["id"])
        path = alert_dispatch.click_path(ctx)
        assert path.startswith("/?"), "must resolve against the phone's own origin"
        assert "state=uttarakhand" in path and "location=loc_gaurikund" in path


class TestMessageAndAssets:
    """Sound, vibration and icon configuration the phones depend on."""

    FRONTEND = settings.project_root / "frontend"

    def test_messages_are_data_only(self):
        message = fcm_client.build_message(
            TOKEN_A, title="t", body="b", data={"kind": "SIMULATION"},
            severity="EXTREME", click_url="/",
        )["message"]
        assert "notification" not in message, "a notification block causes duplicates on Android"
        assert all(isinstance(v, str) for v in message["data"].values())
        assert message["webpush"]["headers"]["Urgency"] == "high"

    def test_emergency_tone_exists_and_is_two_to_four_seconds(self):
        import wave

        for name, low, high in (("floodsafe-extreme-alert.wav", 2.0, 4.0),
                                ("floodsafe-alert.wav", 0.5, 3.0)):
            path = self.FRONTEND / "public" / "sounds" / name
            assert path.exists(), f"{name} missing - run scripts/make_alert_sound.py"
            with wave.open(str(path)) as wav:
                seconds = wav.getnframes() / wav.getframerate()
                assert low <= seconds <= high, f"{name} is {seconds:.2f}s"
                assert wav.getnchannels() == 1
            assert path.stat().st_size < 300_000, "keep alert audio small"

    def test_extreme_vibration_pattern_matches_between_worker_and_page(self):
        pattern = "EXTREME: [500, 200, 500, 200, 1000]"
        worker = (self.FRONTEND / "public" / "firebase-messaging-sw.js").read_text(encoding="utf-8")
        page = (self.FRONTEND / "src" / "services" / "notifications.ts").read_text(encoding="utf-8")
        assert pattern in worker and pattern in page

    def test_notification_icons_exist_and_the_badge_has_transparency(self):
        icon = self.FRONTEND / "public" / "icons" / "floodsafe-192.png"
        badge = self.FRONTEND / "public" / "icons" / "floodsafe-badge-96.png"
        for path in (icon, badge):
            assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
        # IHDR colour type 6 = RGBA; Android renders the badge from alpha only.
        assert badge.read_bytes()[25] == 6
        worker = (self.FRONTEND / "public" / "firebase-messaging-sw.js").read_text(encoding="utf-8")
        assert "/icons/floodsafe-192.png" in worker and "favicon.svg" not in worker

    def test_foreground_alerts_use_the_service_worker_on_android(self):
        """Android Chrome rejects `new Notification()` from a page."""
        import re

        page = (self.FRONTEND / "src" / "services" / "notifications.ts").read_text(encoding="utf-8")
        # Judge the code, not the comments that explain why the constructor is avoided.
        code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        assert "registration.showNotification" in code
        assert "new Notification(" not in code
        assert "playEmergencySound" in code
