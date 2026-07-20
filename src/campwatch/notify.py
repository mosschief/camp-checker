"""
Notification dispatch. Sinks are named in config.yaml and referenced per
watch; secrets (URLs, tokens, topics) come from the environment via the
sink's ``*_env`` keys.

Supported sink types:
  webhook  — POST JSON (Home Assistant webhook automation)
  pushover — POST to the Pushover messages API
  ntfy     — POST to <server>/<topic> (server defaults to https://ntfy.sh)
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional

import httpx
from pydantic import BaseModel

from campwatch.config import NotificationSink

logger = logging.getLogger(__name__)

NOTIFY_TIMEOUT_SECONDS = 15.0
DEFAULT_NTFY_SERVER = "https://ntfy.sh"


class NotificationMessage(BaseModel):
    title: str
    body: str
    url: str
    watch: str
    status: str  # "held" | "open" | "hold_failed" | "dry_run"


class NotifyError(Exception):
    """A sink could not be dispatched."""


class Notifier:
    def __init__(
        self,
        sinks: Dict[str, NotificationSink],
        environ: Optional[Dict[str, str]] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self._sinks = sinks
        self._environ = environ if environ is not None else os.environ
        self._transport = transport

    def _env(self, key: Optional[str], sink_name: str) -> str:
        value = self._environ.get(key or "", "").strip()
        if not value:
            raise NotifyError(f"sink {sink_name!r}: environment variable {key!r} is not set")
        return value

    def send(self, sink_name: str, message: NotificationMessage) -> None:
        sink = self._sinks.get(sink_name)
        if sink is None:
            raise NotifyError(f"notification sink {sink_name!r} is not defined")
        with httpx.Client(timeout=NOTIFY_TIMEOUT_SECONDS, transport=self._transport) as client:
            if sink.type == "webhook":
                self._send_webhook(client, sink, sink_name, message)
            elif sink.type == "pushover":
                self._send_pushover(client, sink, sink_name, message)
            elif sink.type == "ntfy":
                self._send_ntfy(client, sink, sink_name, message)
            else:  # unreachable — config validation rejects unknown types
                raise NotifyError(f"sink {sink_name!r} has unknown type {sink.type!r}")
        logger.info("notification sent via %s (%s): %s", sink_name, sink.type, message.title)

    def _send_webhook(self, client, sink, name, message: NotificationMessage) -> None:
        url = self._env(sink.url_env, name)
        response = client.post(
            url,
            json={
                "title": message.title,
                "message": message.body,
                "url": message.url,
                "watch": message.watch,
                "status": message.status,
            },
        )
        response.raise_for_status()

    def _send_pushover(self, client, sink, name, message: NotificationMessage) -> None:
        response = client.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": self._env(sink.token_env, name),
                "user": self._env(sink.user_env, name),
                "title": message.title,
                "message": message.body,
                "url": message.url,
                "url_title": "Open booking page",
                "priority": 1 if message.status in ("held", "open") else 0,
            },
        )
        response.raise_for_status()

    def _send_ntfy(self, client, sink, name, message: NotificationMessage) -> None:
        server = DEFAULT_NTFY_SERVER
        if sink.server_env and self._environ.get(sink.server_env, "").strip():
            server = self._environ[sink.server_env].strip().rstrip("/")
        # JSON publishing endpoint: HTTP headers are latin-1 only, which would
        # mangle emoji titles; the JSON body is full UTF-8.
        response = client.post(
            server,
            json={
                "topic": self._env(sink.topic_env, name),
                "title": message.title,
                "message": message.body,
                "click": message.url,
                "priority": 4 if message.status in ("held", "open") else 3,
                "tags": ["tent"],
            },
        )
        response.raise_for_status()
