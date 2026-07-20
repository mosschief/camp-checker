"""
Hold execution: load a captured hold request template, inject live values,
and fire it EXACTLY ONCE per detected opening.

The hold endpoint is undocumented and differs per provider host, so it is
never guessed — it comes verbatim from a request the user captured in
DevTools (see README §capture). Templates use {{PLACEHOLDER}} tokens:

    {{SESSION_COOKIE}}  {{CSRF_TOKEN}}                     from the session
    {{CAMPSITE_ID}} {{RESOURCE_LOCATION_ID}} {{MAP_ID}}    from watch/event
    {{START_DATE}} {{END_DATE}} {{NIGHTS}}                 from the event

A JSON value that is exactly one placeholder (e.g. "resourceId": "{{CAMPSITE_ID}}")
is replaced with the typed value (int stays int); placeholders embedded in a
longer string are substituted textually.

Guardrails honored here: single attempt, no retries, dry_run logs the fully
built (secret-redacted) request instead of sending.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from pydantic import BaseModel

from campwatch.config import ResolvedWatch
from campwatch.sessions import SessionMaterial

logger = logging.getLogger(__name__)

HOLD_TIMEOUT_SECONDS = 20.0
REDACTED = "***redacted***"
_SECRET_PLACEHOLDERS = ("{{SESSION_COOKIE}}", "{{CSRF_TOKEN}}")


class HoldTemplateError(Exception):
    """The captured template file is missing or malformed."""


class HoldTemplate(BaseModel):
    url: str
    method: str = "POST"
    headers: Dict[str, str] = {}
    json_body: Optional[Any] = None

    @classmethod
    def load(cls, path: Path) -> "HoldTemplate":
        if not path.is_file():
            raise HoldTemplateError(f"hold template not found: {path}")
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise HoldTemplateError(f"hold template {path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or "url" not in data:
            raise HoldTemplateError(f"hold template {path} must be an object with a 'url'")
        return cls(
            url=data["url"],
            method=data.get("method", "POST"),
            headers=data.get("headers", {}),
            json_body=data.get("json", data.get("body")),
        )


class HoldOutcome(BaseModel):
    status: str  # "held" | "failed" | "dry_run" | "error"
    http_status: Optional[int] = None
    detail: str = ""

    @property
    def held(self) -> bool:
        return self.status == "held"


def build_placeholder_values(
    watch: ResolvedWatch,
    session: SessionMaterial,
    campsite_id: Any,
    start_date: datetime.date,
    end_date: datetime.date,
) -> Dict[str, Any]:
    return {
        "SESSION_COOKIE": session.cookie,
        "CSRF_TOKEN": session.csrf,
        "CAMPSITE_ID": campsite_id,
        "RESOURCE_ID": campsite_id,  # alias; captures name this differently
        "RESOURCE_LOCATION_ID": watch.resource_location_id or watch.campground_id,
        "MAP_ID": watch.map_id,
        "CAMPGROUND_ID": watch.campground_id,
        "START_DATE": start_date.isoformat(),
        "END_DATE": end_date.isoformat(),
        "NIGHTS": watch.nights,
    }


def _substitute(node: Any, values: Dict[str, Any]) -> Any:
    if isinstance(node, dict):
        return {k: _substitute(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, values) for v in node]
    if isinstance(node, str):
        for key, value in values.items():
            token = "{{" + key + "}}"
            if node == token:
                return value  # exact match keeps the captured type (int stays int)
            if token in node:
                node = node.replace(token, "" if value is None else str(value))
        return node
    return node


def build_hold_request(template: HoldTemplate, values: Dict[str, Any]) -> HoldTemplate:
    return HoldTemplate(
        url=_substitute(template.url, values),
        method=template.method,
        headers=_substitute(template.headers, values),
        json_body=_substitute(template.json_body, values),
    )


def redact(request: HoldTemplate, secrets: Dict[str, Any]) -> HoldTemplate:
    """Copy of the built request with session material masked, for safe logging."""
    secret_values = [str(v) for k, v in secrets.items() if k in ("SESSION_COOKIE", "CSRF_TOKEN") and v]

    def scrub(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: scrub(v) for k, v in node.items()}
        if isinstance(node, list):
            return [scrub(v) for v in node]
        if isinstance(node, str):
            for secret in secret_values:
                node = node.replace(secret, REDACTED)
            return node
        return node

    return HoldTemplate(
        url=scrub(request.url),
        method=request.method,
        headers=scrub(request.headers),
        json_body=scrub(request.json_body),
    )


def execute_hold(
    template: HoldTemplate,
    values: Dict[str, Any],
    dry_run: bool,
    transport: Optional[httpx.BaseTransport] = None,
) -> HoldOutcome:
    """
    Build and (unless dry_run) send the hold request. ONE attempt, no retries —
    aggressive holding risks the user's session/IP and the platform's ToS.
    """
    request = build_hold_request(template, values)
    safe = redact(request, values)
    logger.info(
        "hold request built: %s %s headers=%s body=%s",
        safe.method,
        safe.url,
        json.dumps(safe.headers),
        json.dumps(safe.json_body),
    )

    if dry_run:
        logger.info("dry_run is enabled — hold NOT sent")
        return HoldOutcome(status="dry_run", detail="dry_run enabled; request logged, not sent")

    try:
        with httpx.Client(timeout=HOLD_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.request(
                request.method,
                request.url,
                headers=request.headers,
                json=request.json_body,
            )
    except httpx.HTTPError as exc:
        logger.error("hold request errored (no retry): %s", exc)
        return HoldOutcome(status="error", detail=f"request error: {exc}")

    body_snippet = response.text[:500]
    if response.is_success:
        logger.info("hold succeeded: HTTP %s", response.status_code)
        return HoldOutcome(status="held", http_status=response.status_code, detail=body_snippet)

    # Stale session / already-taken / anything else: report, never retry.
    logger.warning("hold failed: HTTP %s %s", response.status_code, body_snippet)
    detail = body_snippet
    if response.status_code in (401, 403):
        detail = f"session likely stale (HTTP {response.status_code}) — refresh it per README. {body_snippet}"
    elif response.status_code == 409:
        detail = f"site likely already taken (HTTP 409). {body_snippet}"
    return HoldOutcome(status="failed", http_status=response.status_code, detail=detail)
