"""
Webhook receiver: FastAPI service that camply POSTs to when it finds an
opening.

Flow per detected campsite (§7 of the brief):
  1. match the payload to a watch entry (facility id + booking date)
  2. idempotency lock on (watch, campsite, date) — duplicate hooks are dropped
  3. if the watch has auto_hold: build the hold from its captured template +
     session and send it ONCE (log-only when dry_run)
  4. route a notification to the watch's configured sink:
     "HELD — pay now" vs "OPEN — book now"

camply's payload shape (WebhookBody in camply/containers/data_containers.py):
  {"campsites": [{campsite_id, booking_date, booking_end_date, booking_nights,
                  campsite_site_name, facility_id, facility_name, booking_url,
                  ...}], "timestamp": "..."}
For GoingToCamp, facility_id is the resource_location_id.
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from pydantic import BaseModel, validator

from campwatch.config import AppConfig, ConfigError, ResolvedWatch, load_config
from campwatch.holds import (
    HoldOutcome,
    HoldTemplate,
    HoldTemplateError,
    build_placeholder_values,
    execute_hold,
)
from campwatch.notify import NotificationMessage, Notifier, NotifyError
from campwatch.sessions import EnvSessionProvider, SessionError, SessionProvider

logger = logging.getLogger(__name__)


class CampsiteEvent(BaseModel):
    """The slice of camply's AvailableCampsite the receiver acts on."""

    campsite_id: Any
    campsite_site_name: str = ""
    facility_id: int
    facility_name: str = ""
    booking_date: datetime.date
    booking_end_date: datetime.date
    booking_nights: int = 1
    booking_url: str = ""

    @validator("booking_date", "booking_end_date", pre=True)
    def _datetime_to_date(cls, v):
        # camply serializes these as datetimes ("2026-08-14T00:00:00")
        if isinstance(v, str) and "T" in v:
            return datetime.datetime.fromisoformat(v).date()
        if isinstance(v, datetime.datetime):
            return v.date()
        return v


def parse_camply_payload(payload: Any) -> List[CampsiteEvent]:
    """Accept camply's WebhookBody ({"campsites": [...]}) or a bare list."""
    if isinstance(payload, dict):
        campsites = payload.get("campsites", [])
    elif isinstance(payload, list):
        campsites = payload
    else:
        campsites = []
    events = []
    for item in campsites:
        try:
            events.append(CampsiteEvent(**item))
        except Exception as exc:
            logger.warning("skipping unparseable campsite entry %r: %s", item, exc)
    return events


class IdempotencyStore:
    """
    In-memory lock keyed on (watch, campsite, date) with a TTL, so duplicate
    hooks for the same opening fire a hold/notification only once. State is
    per-process: a receiver restart clears it, which errs on the side of
    re-notifying rather than silently missing an opening.
    """

    def __init__(self, ttl_minutes: int):
        self._ttl = ttl_minutes * 60
        self._seen: Dict[str, float] = {}
        self._lock = threading.Lock()

    def first_sighting(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._seen = {k: t for k, t in self._seen.items() if now - t < self._ttl}
            if key in self._seen:
                return False
            self._seen[key] = now
            return True


class EventResult(BaseModel):
    key: str
    watch: Optional[str]
    action: str  # "held" | "hold_dry_run" | "hold_failed" | "notified_open" | "duplicate" | "unmatched"
    detail: str = ""


class ReceiverService:
    def __init__(
        self,
        config: AppConfig,
        config_dir: Path,
        notifier: Optional[Notifier] = None,
        sessions: Optional[SessionProvider] = None,
    ):
        self.config = config
        self.config_dir = config_dir
        self.notifier = notifier or Notifier(config.notifications)
        self.sessions = sessions or EnvSessionProvider()
        self.dedupe = IdempotencyStore(config.receiver.dedupe_ttl_minutes)
        self.recent: List[EventResult] = []
        self.started_at = datetime.datetime.now(datetime.timezone.utc)
        self.last_webhook_at: Optional[datetime.datetime] = None

    # -- event handling ----------------------------------------------------

    def handle_payload(self, payload: Any) -> List[EventResult]:
        self.last_webhook_at = datetime.datetime.now(datetime.timezone.utc)
        results = [self._handle_event(e) for e in parse_camply_payload(payload)]
        self.recent = (self.recent + results)[-100:]
        return results

    def _handle_event(self, event: CampsiteEvent) -> EventResult:
        watch = self.config.match_event(event.facility_id, event.booking_date)
        key = (
            f"{watch.name if watch else 'unmatched'}"
            f":{event.facility_id}:{event.campsite_id}:{event.booking_date.isoformat()}"
        )

        if not self.dedupe.first_sighting(key):
            logger.info("duplicate hook for %s — ignored", key)
            return EventResult(key=key, watch=watch.name if watch else None, action="duplicate")

        if watch is None:
            # Unexpected (camply only searches configured watches), but never
            # drop a real opening silently — route to the default sink if set.
            logger.warning(
                "no watch matches facility=%s date=%s", event.facility_id, event.booking_date
            )
            fallback = self.config.defaults.notify
            if fallback:
                self._notify_safe(fallback, self._open_message(None, event))
            return EventResult(key=key, watch=None, action="unmatched")

        logger.info(
            "opening matched watch %r: site %s (%s) on %s",
            watch.name, event.campsite_site_name, event.campsite_id, event.booking_date,
        )

        if not watch.auto_hold:
            self._notify_safe(watch.notify, self._open_message(watch, event))
            return EventResult(key=key, watch=watch.name, action="notified_open")

        outcome = self._attempt_hold(watch, event)
        self._notify_safe(watch.notify, self._hold_message(watch, event, outcome))
        action = {
            "held": "held",
            "dry_run": "hold_dry_run",
        }.get(outcome.status, "hold_failed")
        return EventResult(key=key, watch=watch.name, action=action, detail=outcome.detail)

    def _attempt_hold(self, watch: ResolvedWatch, event: CampsiteEvent) -> HoldOutcome:
        """Single hold attempt; any failure degrades to a normal notification."""
        try:
            template = HoldTemplate.load((self.config_dir / watch.hold_template).resolve())
            session = self.sessions.get_session(watch.session)
        except (HoldTemplateError, SessionError) as exc:
            logger.error("hold prerequisites missing for %r: %s", watch.name, exc)
            return HoldOutcome(status="error", detail=str(exc))
        values = build_placeholder_values(
            watch=watch,
            session=session,
            campsite_id=event.campsite_id,
            start_date=event.booking_date,
            end_date=event.booking_end_date,
        )
        return execute_hold(template, values, dry_run=watch.dry_run)

    # -- messages ----------------------------------------------------------

    def _link(self, event: CampsiteEvent) -> str:
        return event.booking_url or self.config.provider.base_url

    def _site_line(self, event: CampsiteEvent) -> str:
        nights = "night" if event.booking_nights == 1 else "nights"
        return (
            f"{event.campsite_site_name or event.campsite_id} at "
            f"{event.facility_name or event.facility_id} — "
            f"{event.booking_date} → {event.booking_end_date} "
            f"({event.booking_nights} {nights})"
        )

    def _open_message(self, watch: Optional[ResolvedWatch], event: CampsiteEvent) -> NotificationMessage:
        return NotificationMessage(
            title="🏕️ Campsite OPEN — book now",
            body=f"{self._site_line(event)}. Book it before someone else does.",
            url=self._link(event),
            watch=watch.name if watch else "unmatched",
            status="open",
        )

    def _hold_message(
        self, watch: ResolvedWatch, event: CampsiteEvent, outcome: HoldOutcome
    ) -> NotificationMessage:
        window = self.config.receiver.hold_window_minutes
        expires = (
            datetime.datetime.now().astimezone() + datetime.timedelta(minutes=window)
        ).strftime("%H:%M")
        if outcome.status == "held":
            return NotificationMessage(
                title="🔒 Campsite HELD — pay now",
                body=(
                    f"{self._site_line(event)}. Held to your account — complete "
                    f"payment within ~{window} min (expires ~{expires})."
                ),
                url=self._link(event),
                watch=watch.name,
                status="held",
            )
        if outcome.status == "dry_run":
            return NotificationMessage(
                title="🧪 [DRY RUN] Campsite open — hold NOT sent",
                body=(
                    f"{self._site_line(event)}. dry_run is enabled; the hold request "
                    f"was built and logged but not sent. Book manually."
                ),
                url=self._link(event),
                watch=watch.name,
                status="dry_run",
            )
        return NotificationMessage(
            title="⚠️ Campsite OPEN — hold FAILED, book manually",
            body=f"{self._site_line(event)}. Hold attempt failed ({outcome.detail[:200]}). Book manually NOW.",
            url=self._link(event),
            watch=watch.name,
            status="hold_failed",
        )

    def _notify_safe(self, sink_name: str, message: NotificationMessage) -> None:
        try:
            self.notifier.send(sink_name, message)
        except (NotifyError, Exception) as exc:  # a broken sink must not kill the event loop
            logger.error("notification via %r failed: %s", sink_name, exc)

    # -- status ------------------------------------------------------------

    def status(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "last_webhook_at": self.last_webhook_at.isoformat() if self.last_webhook_at else None,
            "watches": [
                {
                    "name": w.name,
                    "campground_id": w.campground_id,
                    "dates": f"{w.start_date} → {w.end_date}",
                    "nights": w.nights,
                    "auto_hold": w.auto_hold,
                    "dry_run": w.dry_run,
                    "notify": w.notify,
                }
                for w in self.config.watches
            ],
            "recent_events": [r.dict() for r in self.recent[-20:]],
        }


def create_app(service: Optional[ReceiverService] = None) -> FastAPI:
    if service is None:
        config_path = Path(os.environ.get("CAMPWATCH_CONFIG", "config.yaml"))
        config = load_config(config_path)
        service = ReceiverService(config, config_path.parent)

    app = FastAPI(title="campwatch receiver")
    app.state.service = service

    @app.post("/camply")
    async def camply_webhook(request: Request):
        payload = await request.json()
        results = service.handle_payload(payload)
        return {"results": [r.dict() for r in results]}

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/status")
    async def status():
        return service.status()

    return app


def main() -> int:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config_path = Path(os.environ.get("CAMPWATCH_CONFIG", "config.yaml"))
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 1
    armed = [w.name for w in config.watches if w.auto_hold and not w.dry_run]
    logger.info(
        "receiver starting: %d watch(es), auto-hold ARMED for %s",
        len(config.watches), armed or "none (dry-run/off)",
    )
    app = create_app(ReceiverService(config, config_path.parent))
    uvicorn.run(app, host="0.0.0.0", port=config.receiver.port)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
