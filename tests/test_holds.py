import datetime
import json

import httpx

from campwatch.holds import (
    REDACTED,
    HoldTemplate,
    build_hold_request,
    build_placeholder_values,
    execute_hold,
    redact,
)
from campwatch.sessions import SessionMaterial
from tests.conftest import HOLD_TEMPLATE


def make_values(app_config):
    watch = app_config.watch_by_name("kanaskat-palmer-aug")
    session = SessionMaterial(name="wa_primary", cookie="cookie-secret", csrf="csrf-secret")
    return build_placeholder_values(
        watch=watch,
        session=session,
        campsite_id=987,
        start_date=datetime.date(2026, 8, 14),
        end_date=datetime.date(2026, 8, 15),
    )


def template():
    return HoldTemplate(
        url=HOLD_TEMPLATE["url"],
        method=HOLD_TEMPLATE["method"],
        headers=HOLD_TEMPLATE["headers"],
        json_body=HOLD_TEMPLATE["json"],
    )


def test_injection_types_and_strings(app_config):
    request = build_hold_request(template(), make_values(app_config))
    # exact-placeholder values keep their type
    assert request.json_body["resourceId"] == 987
    assert request.json_body["mapId"] == 222
    # dates as ISO strings
    assert request.json_body["startDate"] == "2026-08-14"
    assert request.json_body["endDate"] == "2026-08-15"
    # embedded placeholders substituted textually
    assert request.json_body["note"] == "site 987 for 1 night(s)"
    assert request.headers["Cookie"] == "cookie-secret"
    assert request.headers["RequestVerificationToken"] == "csrf-secret"


def test_redaction_masks_session_material(app_config):
    values = make_values(app_config)
    request = build_hold_request(template(), values)
    safe = redact(request, values)
    dumped = json.dumps(safe.dict())
    assert "cookie-secret" not in dumped
    assert "csrf-secret" not in dumped
    assert REDACTED in dumped
    # non-secret values untouched
    assert safe.json_body["resourceId"] == 987


def test_dry_run_does_not_send(app_config):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200)

    outcome = execute_hold(
        template(), make_values(app_config), dry_run=True,
        transport=httpx.MockTransport(handler),
    )
    assert outcome.status == "dry_run"
    assert calls == []


def test_armed_hold_sends_exactly_once(app_config):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"cartId": "xyz"})

    outcome = execute_hold(
        template(), make_values(app_config), dry_run=False,
        transport=httpx.MockTransport(handler),
    )
    assert outcome.held
    assert outcome.http_status == 200
    assert len(calls) == 1
    sent = json.loads(calls[0].read())
    assert sent["resourceId"] == 987


def test_failed_hold_no_retry_and_stale_session_hint(app_config):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, text="unauthorized")

    outcome = execute_hold(
        template(), make_values(app_config), dry_run=False,
        transport=httpx.MockTransport(handler),
    )
    assert outcome.status == "failed"
    assert len(calls) == 1  # guardrail: single attempt, never retried
    assert "stale" in outcome.detail


def test_network_error_is_reported_not_retried(app_config):
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ConnectError("boom")

    outcome = execute_hold(
        template(), make_values(app_config), dry_run=False,
        transport=httpx.MockTransport(handler),
    )
    assert outcome.status == "error"
    assert len(attempts) == 1
