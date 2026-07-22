import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from campwatch.catalog import CampgroundOption
from campwatch.receiver import ReceiverService, create_app
from tests.conftest import FULL_ENV
from tests.test_receiver import FakeNotifier, FakeSessions


@pytest.fixture
def client(app_config, config_dir, monkeypatch):
    monkeypatch.setenv("CAMPWATCH_CONFIG", str(config_dir / "config.yaml"))
    for k, v in FULL_ENV.items():
        monkeypatch.setenv(k, v)
    service = ReceiverService(app_config, config_dir, notifier=FakeNotifier(), sessions=FakeSessions())
    # stub the catalog so no network is touched
    service.catalog.search = lambda rec, q, limit=25: [
        CampgroundOption(campground_id=111, resource_location_id=111, map_id=222,
                         name="Kanaskat-Palmer State Park"),
    ] if "kan" in q.lower() else []
    return TestClient(create_app(service)), service, config_dir


def test_dashboard_served(client):
    c, _, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert "campwatch" in r.text and "Add a site" in r.text


def test_meta_lists_sinks_and_provider(client):
    c, _, _ = client
    m = c.get("/api/meta").json()
    assert m["provider"]["rec_area_id"] == 3
    assert set(m["sinks"]) == {"home_assistant", "phone_pushover", "ntfy_homelab"}


def test_search_autocomplete(client):
    c, _, _ = client
    d = c.get("/api/search", params={"q": "kan"}).json()
    assert d["results"][0]["campground_id"] == 111
    assert d["results"][0]["map_id"] == 222
    assert c.get("/api/search", params={"q": "zzz"}).json()["results"] == []


def test_add_watch_live_and_persisted(client):
    c, service, config_dir = client
    r = c.post("/api/watches", json={
        "name": "wa-new", "campground_id": 111, "resource_location_id": 111, "map_id": 222,
        "start_date": "2026-09-01", "end_date": "2026-09-03", "notify": "home_assistant",
    })
    assert r.status_code == 200
    # applied live in-memory (no restart)
    assert service.config.watch_by_name("wa-new") is not None
    # persisted to disk
    raw = yaml.safe_load((config_dir / "config.yaml").read_text())
    assert any(w["name"] == "wa-new" for w in raw["watches"])


def test_add_watch_validation_error_returns_400(client):
    c, service, _ = client
    r = c.post("/api/watches", json={
        "name": "bad", "campground_id": 1,
        "start_date": "2026-09-01", "end_date": "2026-09-03", "notify": "ghost-sink",
    })
    assert r.status_code == 400
    assert "ghost-sink" in r.json()["error"]
    assert service.config.watch_by_name("bad") is None


def test_delete_watch(client):
    c, service, config_dir = client
    r = c.delete("/api/watches/deception-pass-backup")
    assert r.status_code == 200
    assert service.config.watch_by_name("deception-pass-backup") is None
    raw = yaml.safe_load((config_dir / "config.yaml").read_text())
    assert all(w["name"] != "deception-pass-backup" for w in raw["watches"])


def test_delete_missing_returns_400(client):
    c, _, _ = client
    assert c.delete("/api/watches/nope").status_code == 400


def test_hot_reload_picks_up_external_edit(app_config, config_dir, monkeypatch):
    for k, v in FULL_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CAMPWATCH_CONFIG", str(config_dir / "config.yaml"))
    service = ReceiverService(app_config, config_dir, notifier=FakeNotifier(), sessions=FakeSessions())
    assert service.config.watch_by_name("edited-in") is None

    # simulate an external edit to config.yaml, then the poller's reload
    raw = yaml.safe_load((config_dir / "config.yaml").read_text())
    raw["watches"].append({
        "name": "edited-in", "campground_id": 42,
        "start_date": "2026-10-01", "end_date": "2026-10-02", "notify": "home_assistant",
    })
    (config_dir / "config.yaml").write_text(yaml.safe_dump(raw))
    assert service.reload_from_file() is True
    assert service.config.watch_by_name("edited-in").campground_id == 42


def test_hot_reload_rejects_bad_edit_keeps_current(app_config, config_dir, monkeypatch):
    for k, v in FULL_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CAMPWATCH_CONFIG", str(config_dir / "config.yaml"))
    service = ReceiverService(app_config, config_dir, notifier=FakeNotifier(), sessions=FakeSessions())
    original = len(service.config.watches)
    (config_dir / "config.yaml").write_text("provider: {name: GoingToCamp}\nwatches: [oops]\n")
    assert service.reload_from_file() is False
    assert len(service.config.watches) == original  # unchanged
