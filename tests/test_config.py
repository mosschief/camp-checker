import datetime

import pytest
import yaml

from campwatch.config import ConfigError, load_config
from tests.conftest import FULL_ENV, VALID_CONFIG


def rewrite(config_dir, mutate):
    data = yaml.safe_load(VALID_CONFIG)
    mutate(data)
    (config_dir / "config.yaml").write_text(yaml.safe_dump(data))


def test_valid_config_loads(app_config):
    assert app_config.provider.host == "washington.goingtocamp.com"
    assert app_config.provider.rec_area_id == 3
    assert len(app_config.watches) == 2


def test_defaults_are_inherited(app_config):
    backup = app_config.watch_by_name("deception-pass-backup")
    assert backup.nights == 1
    assert backup.polling_interval == 5
    assert backup.dry_run is True          # global safety default
    assert backup.auto_hold is False
    primary = app_config.watch_by_name("kanaskat-palmer-aug")
    assert primary.auto_hold is True       # explicit override wins


def test_missing_sink_reference_rejected(config_dir):
    rewrite(config_dir, lambda d: d["watches"][1].update(notify="nope"))
    with pytest.raises(ConfigError, match="nope"):
        load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))


def test_sink_missing_env_var_rejected(config_dir):
    env = dict(FULL_ENV)
    del env["PUSHOVER_PUSH_TOKEN"]
    with pytest.raises(ConfigError, match="PUSHOVER_PUSH_TOKEN"):
        load_config(config_dir / "config.yaml", environ=env)


def test_auto_hold_without_template_rejected(config_dir):
    rewrite(config_dir, lambda d: d["watches"][0].pop("hold_template"))
    with pytest.raises(ConfigError, match="hold_template"):
        load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))


def test_auto_hold_with_missing_template_file_rejected(config_dir):
    (config_dir / "holds" / "kanaskat.json").unlink()
    with pytest.raises(ConfigError, match="not found"):
        load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))


def test_auto_hold_without_session_rejected(config_dir):
    rewrite(config_dir, lambda d: d["watches"][0].pop("session"))
    with pytest.raises(ConfigError, match="session"):
        load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))


def test_auto_hold_with_unset_session_env_rejected(config_dir):
    env = dict(FULL_ENV)
    env["SESSION_WA_PRIMARY_CSRF"] = ""
    with pytest.raises(ConfigError, match="SESSION_WA_PRIMARY_CSRF"):
        load_config(config_dir / "config.yaml", environ=env)


def test_polling_interval_floor_enforced(config_dir):
    rewrite(config_dir, lambda d: d["watches"][1].update(polling_interval=1))
    with pytest.raises(ConfigError, match="floor"):
        load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))


def test_end_date_must_follow_start_date(config_dir):
    rewrite(config_dir, lambda d: d["watches"][1].update(end_date="2026-08-14"))
    with pytest.raises(ConfigError, match="end_date"):
        load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))


def test_duplicate_watch_names_rejected(config_dir):
    rewrite(config_dir, lambda d: d["watches"][1].update(name="kanaskat-palmer-aug"))
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))


def test_unknown_top_level_key_rejected(config_dir):
    rewrite(config_dir, lambda d: d.update(surprise=True))
    with pytest.raises(ConfigError):
        load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))


def test_match_event_by_facility_and_date(app_config):
    watch = app_config.match_event(111, datetime.date(2026, 8, 15))
    assert watch.name == "kanaskat-palmer-aug"
    # end_date is checkout day — a night starting then is out of range
    assert app_config.match_event(111, datetime.date(2026, 8, 16)) is None
    assert app_config.match_event(999, datetime.date(2026, 8, 14)) is None


def test_watch_only_needs_required_fields(config_dir):
    rewrite(
        config_dir,
        lambda d: d["watches"].__setitem__(
            1,
            {
                "name": "minimal",
                "campground_id": 5,
                "start_date": "2026-09-01",
                "end_date": "2026-09-03",
            },
        ),
    )
    config = load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))
    minimal = config.watch_by_name("minimal")
    assert minimal.notify == "home_assistant"  # defaults.notify fallback
    assert minimal.auto_hold is False
