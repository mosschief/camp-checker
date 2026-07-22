import pytest
import yaml

from campwatch import store
from campwatch.config import ConfigError, load_config
from tests.conftest import FULL_ENV


def read_watch_names(config_dir):
    raw = yaml.safe_load((config_dir / "config.yaml").read_text())
    return [w["name"] for w in raw["watches"]]


def test_add_watch_persists_and_validates(config_dir):
    config = store.upsert_watch(
        config_dir / "config.yaml",
        {
            "name": "new-site",
            "campground_id": 777,
            "start_date": "2026-09-01",
            "end_date": "2026-09-03",
            "notify": "phone_pushover",
        },
        dict(FULL_ENV),
    )
    assert "new-site" in [w.name for w in config.watches]
    assert "new-site" in read_watch_names(config_dir)  # written to disk
    # reload from disk to confirm the file is valid + inheritance applied
    reloaded = load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))
    w = reloaded.watch_by_name("new-site")
    assert w.nights == 1 and w.dry_run is True


def test_upsert_replaces_same_name(config_dir):
    store.upsert_watch(
        config_dir / "config.yaml",
        {"name": "deception-pass-backup", "campground_id": 999,
         "start_date": "2026-09-01", "end_date": "2026-09-05", "notify": "phone_pushover"},
        dict(FULL_ENV),
    )
    names = read_watch_names(config_dir)
    assert names.count("deception-pass-backup") == 1
    reloaded = load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))
    assert reloaded.watch_by_name("deception-pass-backup").campground_id == 999


def test_add_invalid_watch_rejected_and_file_untouched(config_dir):
    before = (config_dir / "config.yaml").read_text()
    with pytest.raises(ConfigError, match="not defined"):
        store.upsert_watch(
            config_dir / "config.yaml",
            {"name": "bad", "campground_id": 1,
             "start_date": "2026-09-01", "end_date": "2026-09-03", "notify": "nope"},
            dict(FULL_ENV),
        )
    assert (config_dir / "config.yaml").read_text() == before  # atomic: no partial write


def test_add_auto_hold_without_prereqs_rejected(config_dir):
    with pytest.raises(ConfigError, match="hold_template|session"):
        store.upsert_watch(
            config_dir / "config.yaml",
            {"name": "armed", "campground_id": 5, "start_date": "2026-09-01",
             "end_date": "2026-09-03", "notify": "home_assistant", "auto_hold": True},
            dict(FULL_ENV),
        )


def test_remove_watch(config_dir):
    config = store.remove_watch(config_dir / "config.yaml", "deception-pass-backup", dict(FULL_ENV))
    assert "deception-pass-backup" not in [w.name for w in config.watches]
    assert "deception-pass-backup" not in read_watch_names(config_dir)


def test_remove_missing_watch_errors(config_dir):
    with pytest.raises(ConfigError, match="no watch named"):
        store.remove_watch(config_dir / "config.yaml", "ghost", dict(FULL_ENV))


def test_empty_optional_fields_dropped(config_dir):
    # blank map_id/session from a form must not become "" in the file
    store.upsert_watch(
        config_dir / "config.yaml",
        {"name": "blanks", "campground_id": 3, "start_date": "2026-09-01",
         "end_date": "2026-09-03", "notify": "home_assistant",
         "map_id": None, "session": "", "hold_template": ""},
        dict(FULL_ENV),
    )
    raw = yaml.safe_load((config_dir / "config.yaml").read_text())
    entry = next(w for w in raw["watches"] if w["name"] == "blanks")
    assert "session" not in entry and "hold_template" not in entry and "map_id" not in entry
