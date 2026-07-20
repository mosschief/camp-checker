import httpx
import pytest

from campwatch.notify import NotificationMessage, Notifier, NotifyError
from tests.conftest import FULL_ENV


MESSAGE = NotificationMessage(
    title="🏕️ Campsite OPEN — book now",
    body="Site A42 at Kanaskat — 2026-08-14 → 2026-08-15 (1 night)",
    url="https://washington.goingtocamp.com/create-booking",
    watch="kanaskat-palmer-aug",
    status="open",
)


def make_notifier(app_config, handler, environ=None):
    return Notifier(
        app_config.notifications,
        environ=dict(FULL_ENV) if environ is None else environ,
        transport=httpx.MockTransport(handler),
    )


def test_webhook_sink(app_config):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    make_notifier(app_config, handler).send("home_assistant", MESSAGE)
    assert str(seen[0].url) == FULL_ENV["HA_WEBHOOK_URL"]
    import json
    body = json.loads(seen[0].read())
    assert body["title"] == MESSAGE.title
    assert body["url"] == MESSAGE.url
    assert body["status"] == "open"


def test_pushover_sink(app_config):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    make_notifier(app_config, handler).send("phone_pushover", MESSAGE)
    assert "api.pushover.net" in str(seen[0].url)
    body = seen[0].read().decode()
    assert "po-token" in body and "po-user" in body


def test_ntfy_sink_defaults_to_ntfy_sh(app_config):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    make_notifier(app_config, handler).send("ntfy_homelab", MESSAGE)
    assert str(seen[0].url) == "https://ntfy.sh"
    import json
    body = json.loads(seen[0].read())
    assert body["topic"] == "campwatch-test"
    assert body["click"] == MESSAGE.url
    assert body["title"] == MESSAGE.title  # UTF-8 emoji survives JSON publish


def test_missing_env_raises(app_config):
    env = dict(FULL_ENV)
    env["HA_WEBHOOK_URL"] = ""
    notifier = make_notifier(app_config, lambda r: httpx.Response(200), environ=env)
    with pytest.raises(NotifyError, match="HA_WEBHOOK_URL"):
        notifier.send("home_assistant", MESSAGE)


def test_unknown_sink_raises(app_config):
    notifier = make_notifier(app_config, lambda r: httpx.Response(200))
    with pytest.raises(NotifyError, match="not defined"):
        notifier.send("nope", MESSAGE)
