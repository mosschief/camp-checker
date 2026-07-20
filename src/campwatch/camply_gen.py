"""
Translate config.yaml watch entries into camply --yaml-config files.

camply's YamlSearchFile model (camply/containers/search_model.py) describes
exactly one search per file, so each watch entry becomes its own yaml file
and its own camply process. The webhook notifier reads its target URL from
the WEBHOOK_URL environment variable, which the watcher entrypoint sets from
receiver.webhook_url.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import yaml

from campwatch.config import AppConfig, ResolvedWatch


def camply_search_dict(config: AppConfig, watch: ResolvedWatch) -> Dict:
    return {
        "provider": config.provider.name,
        "recreation_area": config.provider.rec_area_id,
        "campgrounds": watch.campground_id,
        "start_date": watch.start_date.isoformat(),
        "end_date": watch.end_date.isoformat(),
        "nights": watch.nights,
        "continuous": True,
        "polling_interval": watch.polling_interval,
        "notifications": "webhook",
        # keep searching after the first hit — later cancellations matter too
        "search_forever": True,
        "notify_first_try": False,
    }


def write_camply_configs(config: AppConfig, output_dir: Path) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for watch in config.watches:
        path = output_dir / f"{watch.name}.camply.yaml"
        path.write_text(
            yaml.safe_dump(camply_search_dict(config, watch), sort_keys=False)
        )
        paths.append(path)
    return paths
