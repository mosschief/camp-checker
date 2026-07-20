import json
from pathlib import Path

import pytest

from campwatch.config import load_config

VALID_CONFIG = """
provider:
  name: GoingToCamp
  host: washington.goingtocamp.com
  rec_area_id: 3
defaults:
  nights: 1
  polling_interval: 5
  auto_hold: false
  dry_run: true
  notify: home_assistant
notifications:
  home_assistant:
    type: webhook
    url_env: HA_WEBHOOK_URL
  phone_pushover:
    type: pushover
    token_env: PUSHOVER_PUSH_TOKEN
    user_env: PUSHOVER_PUSH_USER
  ntfy_homelab:
    type: ntfy
    topic_env: NTFY_TOPIC
watches:
  - name: kanaskat-palmer-aug
    campground_id: 111
    map_id: 222
    resource_location_id: 111
    start_date: 2026-08-14
    end_date: 2026-08-16
    nights: 1
    auto_hold: true
    hold_template: holds/kanaskat.json
    session: wa_primary
    notify: home_assistant
  - name: deception-pass-backup
    campground_id: 333
    start_date: 2026-08-14
    end_date: 2026-08-16
    auto_hold: false
    notify: phone_pushover
"""

HOLD_TEMPLATE = {
    "url": "https://washington.goingtocamp.com/api/hold",
    "method": "POST",
    "headers": {
        "Content-Type": "application/json",
        "Cookie": "{{SESSION_COOKIE}}",
        "RequestVerificationToken": "{{CSRF_TOKEN}}",
    },
    "json": {
        "resourceId": "{{CAMPSITE_ID}}",
        "mapId": "{{MAP_ID}}",
        "startDate": "{{START_DATE}}",
        "endDate": "{{END_DATE}}",
        "note": "site {{CAMPSITE_ID}} for {{NIGHTS}} night(s)",
    },
}

FULL_ENV = {
    "HA_WEBHOOK_URL": "http://ha.local:8123/api/webhook/abc",
    "PUSHOVER_PUSH_TOKEN": "po-token",
    "PUSHOVER_PUSH_USER": "po-user",
    "NTFY_TOPIC": "campwatch-test",
    "SESSION_WA_PRIMARY_COOKIE": "cookie-value-secret",
    "SESSION_WA_PRIMARY_CSRF": "csrf-value-secret",
}


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / "config.yaml").write_text(VALID_CONFIG)
    holds = tmp_path / "holds"
    holds.mkdir()
    (holds / "kanaskat.json").write_text(json.dumps(HOLD_TEMPLATE))
    return tmp_path


@pytest.fixture
def app_config(config_dir: Path):
    return load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))


def camply_webhook_payload(
    facility_id: int = 111,
    campsite_id: int = 987,
    booking_date: str = "2026-08-14T00:00:00",
    booking_end_date: str = "2026-08-15T00:00:00",
) -> dict:
    """Mirror of camply's WebhookBody / AvailableCampsite JSON shape."""
    return {
        "campsites": [
            {
                "campsite_id": campsite_id,
                "booking_date": booking_date,
                "booking_end_date": booking_end_date,
                "booking_nights": 1,
                "campsite_site_name": "Site A42",
                "campsite_loop_name": None,
                "campsite_type": None,
                "campsite_occupancy": [0, 8],
                "campsite_use_type": None,
                "availability_status": "Available",
                "recreation_area": "Washington State Parks",
                "recreation_area_id": 3,
                "facility_name": "Kanaskat-Palmer State Park",
                "facility_id": facility_id,
                "booking_url": "https://washington.goingtocamp.com/create-booking",
                "permitted_equipment": None,
                "campsite_attributes": None,
            }
        ],
        "timestamp": "2026-07-20T10:00:00+00:00",
    }
