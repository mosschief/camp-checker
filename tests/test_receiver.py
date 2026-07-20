import datetime
from pathlib import Path
from typing import List

import httpx
import pytest
from fastapi.testclient import TestClient

from campwatch.notify import NotificationMessage
from campwatch.receiver import (
    IdempotencyStore,
    ReceiverService,
    create_app,
    parse_camply_payload,
)
from campwatch.sessions import SessionMaterial, SessionProvider
from tests.conftest import camply_webhook_payload


class FakeNotifier:
    def __init__(self):
        self.sent: List[tuple] = []

    def send(self, sink_name: str, message: NotificationMessage) -> None:
        self.sent.append((sink_name, message))


class FakeSessions(SessionProvider):
    def get_session(self, name: str) -> SessionMaterial:
        return SessionMaterial(name=name, cookie="cookie-secret", csrf="csrf-secret")


@pytest.fixture
def service(app_config, config_dir: Path):
    return ReceiverService(
        app_config, config_dir, notifier=FakeNotifier(), sessions=FakeSessions()
    )


def test_parse_camply_payload_shapes():
    events = parse_camply_payload(camply_webhook_payload())
    assert len(events) == 1
    assert events[0].facility_id == 111
    assert events[0].booking_date == datetime.date(2026, 8, 14)
    # bare list also accepted
    assert len(parse_camply_payload(camply_webhook_payload()["campsites"])) == 1
    # garbage is skipped, not fatal
    assert parse_camply_payload({"campsites": [{"nope": 1}]}) == []
    assert parse_camply_payload("junk") == []


def test_auto_hold_watch_dry_run_notifies_without_sending(service):
    results = service.handle_payload(camply_webhook_payload(facility_id=111))
    assert [r.action for r in results] == ["hold_dry_run"]
    (sink, message), = service.notifier.sent
    assert sink == "home_assistant"
    assert message.status == "dry_run"
    assert "DRY RUN" in message.title


def test_watch_only_entry_notifies_open(service):
    results = service.handle_payload(camply_webhook_payload(facility_id=333))
    assert [r.action for r in results] == ["notified_open"]
    (sink, message), = service.notifier.sent
    assert sink == "phone_pushover"
    assert message.status == "open"
    assert "book now" in message.title.lower()


def test_duplicate_hooks_fire_once(service):
    first = service.handle_payload(camply_webhook_payload())
    second = service.handle_payload(camply_webhook_payload())
    assert first[0].action == "hold_dry_run"
    assert second[0].action == "duplicate"
    assert len(service.notifier.sent) == 1


def test_different_sites_are_not_deduped(service):
    service.handle_payload(camply_webhook_payload(campsite_id=987))
    service.handle_payload(camply_webhook_payload(campsite_id=988))
    assert len(service.notifier.sent) == 2


def test_unmatched_event_falls_back_to_default_sink(service):
    results = service.handle_payload(camply_webhook_payload(facility_id=999))
    assert [r.action for r in results] == ["unmatched"]
    (sink, message), = service.notifier.sent
    assert sink == "home_assistant"
    assert message.status == "open"


def test_armed_hold_success_sends_held_notification(app_config, config_dir, monkeypatch):
    armed = app_config.copy(deep=True)
    armed.watches[0].dry_run = False
    service = ReceiverService(armed, config_dir, notifier=FakeNotifier(), sessions=FakeSessions())

    calls = []

    def fake_execute(template, values, dry_run, transport=None):
        from campwatch.holds import HoldOutcome
        calls.append((template, values, dry_run))
        return HoldOutcome(status="held", http_status=200)

    monkeypatch.setattr("campwatch.receiver.execute_hold", fake_execute)
    results = service.handle_payload(camply_webhook_payload())
    assert [r.action for r in results] == ["held"]
    assert calls[0][2] is False  # dry_run off
    assert calls[0][1]["CAMPSITE_ID"] == 987
    (_, message), = service.notifier.sent
    assert message.status == "held"
    assert "pay now" in message.title.lower()


def test_hold_failure_still_notifies_with_fallback_link(app_config, config_dir, monkeypatch):
    armed = app_config.copy(deep=True)
    armed.watches[0].dry_run = False
    service = ReceiverService(armed, config_dir, notifier=FakeNotifier(), sessions=FakeSessions())

    def fake_execute(template, values, dry_run, transport=None):
        from campwatch.holds import HoldOutcome
        return HoldOutcome(status="failed", http_status=409, detail="already taken")

    monkeypatch.setattr("campwatch.receiver.execute_hold", fake_execute)
    results = service.handle_payload(camply_webhook_payload())
    assert [r.action for r in results] == ["hold_failed"]
    (_, message), = service.notifier.sent
    assert message.status == "hold_failed"
    assert message.url  # user always gets a link to book manually


def test_idempotency_store_ttl():
    store = IdempotencyStore(ttl_minutes=30)
    assert store.first_sighting("a") is True
    assert store.first_sighting("a") is False
    assert store.first_sighting("b") is True


def test_fastapi_endpoints(service):
    client = TestClient(create_app(service))
    assert client.get("/healthz").json() == {"ok": True}

    response = client.post("/camply", json=camply_webhook_payload())
    assert response.status_code == 200
    assert response.json()["results"][0]["action"] == "hold_dry_run"

    status = client.get("/status").json()
    assert len(status["watches"]) == 2
    assert status["watches"][0]["name"] == "kanaskat-palmer-aug"
    assert status["recent_events"][0]["action"] == "hold_dry_run"
    assert status["last_webhook_at"] is not None
