"""
Runtime CRUD for watch entries, backed by config.yaml.

Every mutation is validated through the exact same pipeline as startup
(``build_config``) before anything is written, so the dashboard can never
persist a config that the services would reject — a bad add fails loudly and
leaves config.yaml untouched. On success the file is written atomically and
the fresh AppConfig is returned so the caller can apply it in place.

config.yaml stays the single source of truth (Unraid appdata, hand-editable);
the dashboard is just another writer of it. Round-tripping through YAML drops
comments but preserves every value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from campwatch.config import AppConfig, ConfigError, build_config, read_raw, write_raw

# Fields a dashboard-managed watch may set (mirrors the Watch model).
WATCH_FIELDS = {
    "name",
    "campground_id",
    "resource_location_id",
    "map_id",
    "start_date",
    "end_date",
    "nights",
    "polling_interval",
    "auto_hold",
    "dry_run",
    "hold_template",
    "session",
    "notify",
}


def _clean(watch: Dict) -> Dict:
    """Drop None/empty optional values so defaults apply cleanly."""
    return {
        k: v
        for k, v in watch.items()
        if k in WATCH_FIELDS and v is not None and v != ""
    }


def _validate_and_write(
    config_path: Path, raw: Dict, environ: Dict[str, str]
) -> AppConfig:
    config = build_config(raw, Path(config_path).parent, environ)  # raises ConfigError
    write_raw(config_path, raw)
    return config


def upsert_watch(
    config_path: str | Path, watch: Dict, environ: Dict[str, str]
) -> AppConfig:
    """Add a watch, or replace an existing one with the same name."""
    config_path = Path(config_path)
    raw = read_raw(config_path)
    watches = list(raw.get("watches") or [])
    entry = _clean(watch)
    name = entry.get("name")
    if not name:
        raise ConfigError("watch is missing a 'name'")

    replaced = False
    for i, existing in enumerate(watches):
        if isinstance(existing, dict) and existing.get("name") == name:
            watches[i] = entry
            replaced = True
            break
    if not replaced:
        watches.append(entry)

    raw["watches"] = watches
    return _validate_and_write(config_path, raw, environ)


def remove_watch(
    config_path: str | Path, name: str, environ: Dict[str, str]
) -> AppConfig:
    config_path = Path(config_path)
    raw = read_raw(config_path)
    watches = list(raw.get("watches") or [])
    kept = [w for w in watches if not (isinstance(w, dict) and w.get("name") == name)]
    if len(kept) == len(watches):
        raise ConfigError(f"no watch named {name!r}")
    raw["watches"] = kept
    return _validate_and_write(config_path, raw, environ)
