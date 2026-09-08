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
