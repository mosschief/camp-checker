"""
Config layer: pydantic models for config.yaml + cross-reference validation.

config.yaml holds no secrets; anything secret is referenced indirectly via
``*_env`` keys (notification sinks) or a ``session`` name (auth material) and
resolved against the process environment (populated from .env).

Loading fails fast with a ConfigError listing every problem found, so a bad
config never half-starts a service.
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Extra, Field, validator

POLLING_INTERVAL_FLOOR = 5  # minutes; camply's hard minimum — do not lower


class ConfigError(Exception):
    """Raised when config.yaml is invalid; message lists every problem."""


class StrictModel(BaseModel):
    class Config:
        extra = Extra.forbid


class ProviderConfig(StrictModel):
    name: str = "GoingToCamp"
    host: str = "washington.goingtocamp.com"
    rec_area_id: int = 3

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"


class NotificationSink(StrictModel):
    type: str  # webhook | pushover | ntfy
    url_env: Optional[str] = None      # webhook
    token_env: Optional[str] = None    # pushover
    user_env: Optional[str] = None     # pushover
    topic_env: Optional[str] = None    # ntfy
    server_env: Optional[str] = None   # ntfy (optional; defaults to ntfy.sh)

    @validator("type")
    def _known_type(cls, v: str) -> str:
        if v not in ("webhook", "pushover", "ntfy"):
            raise ValueError(f"unknown sink type {v!r} (expected webhook, pushover, or ntfy)")
        return v

    def required_env_keys(self) -> List[str]:
        if self.type == "webhook":
            return [self.url_env or ""]
        if self.type == "pushover":
            return [self.token_env or "", self.user_env or ""]
        return [self.topic_env or ""]


class Defaults(StrictModel):
    nights: int = 1
    polling_interval: int = POLLING_INTERVAL_FLOOR  # minutes
    auto_hold: bool = False
    dry_run: bool = True  # global safety default; flip deliberately per §3
    notify: Optional[str] = None

    @validator("polling_interval")
    def _floor(cls, v: int) -> int:
        if v < POLLING_INTERVAL_FLOOR:
            raise ValueError(
                f"polling_interval {v} is below the hard floor of "
                f"{POLLING_INTERVAL_FLOOR} minutes"
            )
        return v


class ReceiverConfig(StrictModel):
    """Receiver-side knobs. All defaulted so the block is optional."""

    port: int = 8000
    # URL camply POSTs to; the default resolves on the compose network.
    webhook_url: str = "http://receiver:8000/camply"
    hold_window_minutes: int = 15   # platform hold window, for the countdown text
    dedupe_ttl_minutes: int = 30    # duplicate hooks within this window are ignored


class Watch(StrictModel):
    name: str
    campground_id: int
    map_id: Optional[int] = None
    resource_location_id: Optional[int] = None
    start_date: datetime.date
    end_date: datetime.date
    nights: Optional[int] = None
    polling_interval: Optional[int] = None
    auto_hold: Optional[bool] = None
    dry_run: Optional[bool] = None
    hold_template: Optional[str] = None
    session: Optional[str] = None
    notify: Optional[str] = None

    @validator("name")
    def _name_shape(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", v):
            raise ValueError(
                f"watch name {v!r} may only contain letters, digits, '.', '_', '-'"
            )
        return v

    @validator("end_date")
    def _dates_ordered(cls, v: datetime.date, values: dict) -> datetime.date:
        start = values.get("start_date")
        if start is not None and v <= start:
            raise ValueError(f"end_date {v} must be after start_date {start}")
        return v


class ResolvedWatch(BaseModel):
    """A watch entry with defaults applied — what the services actually consume."""

    name: str
    campground_id: int
    map_id: Optional[int]
    resource_location_id: Optional[int]
    start_date: datetime.date
    end_date: datetime.date
    nights: int
    polling_interval: int
    auto_hold: bool
    dry_run: bool
    hold_template: Optional[str]
    session: Optional[str]
    notify: str

    def matches_facility(self, facility_id: int) -> bool:
        return facility_id in {self.campground_id, self.resource_location_id}

    def matches_date(self, booking_date: datetime.date) -> bool:
        # camply reports per-night openings; a valid night starts within
        # [start_date, end_date) so that checkout lands by end_date.
        return self.start_date <= booking_date < self.end_date


class AppConfig(BaseModel):
    provider: ProviderConfig
    defaults: Defaults
    notifications: Dict[str, NotificationSink]
    watches: List[ResolvedWatch]
    receiver: ReceiverConfig

    def watch_by_name(self, name: str) -> Optional[ResolvedWatch]:
        return next((w for w in self.watches if w.name == name), None)

    def match_event(
        self, facility_id: int, booking_date: datetime.date
    ) -> Optional[ResolvedWatch]:
        return next(
            (
                w
                for w in self.watches
                if w.matches_facility(facility_id) and w.matches_date(booking_date)
            ),
            None,
        )


class RawConfig(StrictModel):
    provider: ProviderConfig = ProviderConfig()
    defaults: Defaults = Defaults()
    notifications: Dict[str, NotificationSink] = {}
    watches: List[Watch]
    receiver: ReceiverConfig = ReceiverConfig()


def session_env_keys(session_name: str) -> Dict[str, str]:
    """Map a session name from config.yaml to its .env variable names."""
    slug = re.sub(r"[^A-Za-z0-9]", "_", session_name).upper()
    return {
        "cookie": f"SESSION_{slug}_COOKIE",
        "csrf": f"SESSION_{slug}_CSRF",
    }


def _resolve_watch(watch: Watch, defaults: Defaults) -> ResolvedWatch:
    def pick(field: str):
        value = getattr(watch, field)
        return getattr(defaults, field) if value is None else value

    return ResolvedWatch(
        name=watch.name,
        campground_id=watch.campground_id,
        map_id=watch.map_id,
        resource_location_id=watch.resource_location_id,
        start_date=watch.start_date,
        end_date=watch.end_date,
        nights=pick("nights"),
        polling_interval=pick("polling_interval"),
        auto_hold=pick("auto_hold"),
        dry_run=pick("dry_run"),
        hold_template=watch.hold_template,
        session=watch.session,
        notify=watch.notify or defaults.notify or "",
    )


def _cross_validate(
    raw: RawConfig,
    watches: List[ResolvedWatch],
    config_dir: Path,
    environ: Dict[str, str],
) -> List[str]:
    problems: List[str] = []

    names = [w.name for w in watches]
    for name in {n for n in names if names.count(n) > 1}:
        problems.append(f"duplicate watch name {name!r}")

    referenced_sinks = set()
    for w in watches:
        if not w.notify:
            problems.append(
                f"watch {w.name!r} has no notify sink and defaults.notify is unset"
            )
            continue
        if w.notify not in raw.notifications:
            problems.append(
                f"watch {w.name!r} references notify sink {w.notify!r} "
                f"which is not defined under notifications:"
            )
            continue
        referenced_sinks.add(w.notify)

    if raw.defaults.notify and raw.defaults.notify not in raw.notifications:
        problems.append(
            f"defaults.notify {raw.defaults.notify!r} is not defined under notifications:"
        )

    for sink_name in sorted(referenced_sinks):
        sink = raw.notifications[sink_name]
        for key in sink.required_env_keys():
            if not key:
                problems.append(
                    f"sink {sink_name!r} (type {sink.type}) is missing its *_env key(s)"
                )
            elif not environ.get(key):
                problems.append(
                    f"sink {sink_name!r} needs environment variable {key} "
                    f"(set it in .env)"
                )

    for w in watches:
        if w.polling_interval < POLLING_INTERVAL_FLOOR:
            problems.append(
                f"watch {w.name!r}: polling_interval {w.polling_interval} is below "
                f"the hard floor of {POLLING_INTERVAL_FLOOR} minutes"
            )
        if not w.auto_hold:
            continue
        # auto_hold prerequisites (§6 rules): captured template + resolvable session
        if not w.hold_template:
            problems.append(
                f"watch {w.name!r} has auto_hold: true but no hold_template "
                f"(capture one per §4 of the README)"
            )
        else:
            template_path = (config_dir / w.hold_template).resolve()
            if not template_path.is_file():
                problems.append(
                    f"watch {w.name!r}: hold_template {w.hold_template!r} not found "
                    f"at {template_path}"
                )
        if not w.session:
            problems.append(
                f"watch {w.name!r} has auto_hold: true but no session name"
            )
        else:
            for key in session_env_keys(w.session).values():
                if not environ.get(key):
                    problems.append(
                        f"watch {w.name!r}: session {w.session!r} needs environment "
                        f"variable {key} (set it in .env)"
                    )

    return problems


def load_config(
    config_path: str | Path,
    env_path: str | Path | None = None,
    environ: Dict[str, str] | None = None,
) -> AppConfig:
    """
    Load and validate config.yaml. ``environ`` is injectable for tests;
    by default .env (if present next to the config) is loaded into os.environ.
    """
    config_path = Path(config_path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")

    if environ is None:
        load_dotenv(env_path or config_path.parent / ".env")
        environ = dict(os.environ)

    try:
        data = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config file must be a YAML mapping")

    try:
        raw = RawConfig(**data)
    except Exception as exc:  # pydantic ValidationError → readable message
        raise ConfigError(f"invalid config:\n{exc}") from exc

    watches = [_resolve_watch(w, raw.defaults) for w in raw.watches]
    problems = _cross_validate(raw, watches, config_path.parent, environ)
    if problems:
        raise ConfigError(
            "invalid config:\n" + "\n".join(f"  - {p}" for p in problems)
        )

    return AppConfig(
        provider=raw.provider,
        defaults=raw.defaults,
        notifications=raw.notifications,
        watches=watches,
        receiver=raw.receiver,
    )
