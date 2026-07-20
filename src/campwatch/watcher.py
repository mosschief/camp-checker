"""
Watcher entrypoint: reads config.yaml, generates one camply yaml-config per
watch entry, and supervises one `camply campsites --yaml-config <file>`
process per watch. Crashed processes restart with exponential backoff (camply
already rate-limits its own polling; the backoff only guards against crash
loops, never against the polling floor).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from campwatch.camply_gen import write_camply_configs
from campwatch.config import AppConfig, ConfigError, load_config

logger = logging.getLogger(__name__)

RESTART_BACKOFF_INITIAL = 10  # seconds
RESTART_BACKOFF_MAX = 600


class CamplySupervisor:
    def __init__(self, config: AppConfig, runtime_dir: Path):
        self.config = config
        self.runtime_dir = runtime_dir
        self._procs: Dict[str, subprocess.Popen] = {}
        self._stop = threading.Event()

    def _camply_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        # CAMPWATCH_WEBHOOK_URL overrides config (set by the combined
        # single-container entrypoint, where the receiver is on localhost)
        env["WEBHOOK_URL"] = (
            os.environ.get("CAMPWATCH_WEBHOOK_URL") or self.config.receiver.webhook_url
        )
        env.setdefault("WEBHOOK_HEADERS", '{"Content-Type": "application/json"}')
        return env

    def _spawn(self, name: str, yaml_path: Path) -> subprocess.Popen:
        logger.info("[%s] starting camply (%s)", name, yaml_path.name)
        return subprocess.Popen(
            [sys.executable, "-m", "camply", "campsites", "--yaml-config", str(yaml_path)],
            env=self._camply_env(),
            stdout=None,  # inherit → structured container stdout
            stderr=None,
        )

    def run(self) -> int:
        yaml_paths = write_camply_configs(self.config, self.runtime_dir)
        watches = {p.stem.removesuffix(".camply"): p for p in yaml_paths}
        backoff: Dict[str, float] = {name: RESTART_BACKOFF_INITIAL for name in watches}
        last_start: Dict[str, float] = {}

        for name, path in watches.items():
            self._procs[name] = self._spawn(name, path)
            last_start[name] = time.monotonic()

        try:
            while not self._stop.is_set():
                for name, path in watches.items():
                    proc = self._procs[name]
                    if proc.poll() is None:
                        continue
                    ran_for = time.monotonic() - last_start[name]
                    if ran_for > 300:  # ran fine for a while → reset backoff
                        backoff[name] = RESTART_BACKOFF_INITIAL
                    logger.warning(
                        "[%s] camply exited with code %s after %.0fs; restarting in %.0fs",
                        name, proc.returncode, ran_for, backoff[name],
                    )
                    if self._stop.wait(backoff[name]):
                        break
                    backoff[name] = min(backoff[name] * 2, RESTART_BACKOFF_MAX)
                    self._procs[name] = self._spawn(name, path)
                    last_start[name] = time.monotonic()
                self._stop.wait(5)
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        self._stop.set()
        for name, proc in self._procs.items():
            if proc.poll() is None:
                logger.info("[%s] stopping camply", name)
                proc.terminate()
        deadline = time.monotonic() + 15
        for proc in self._procs.values():
            remaining = max(0.1, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config_path = Path(os.environ.get("CAMPWATCH_CONFIG", "config.yaml"))
    runtime_dir = Path(os.environ.get("CAMPWATCH_RUNTIME_DIR", "runtime/camply"))
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 1
    logger.info(
        "watcher starting: provider %s (%s, rec area %s), %d watch(es)",
        config.provider.name, config.provider.host, config.provider.rec_area_id,
        len(config.watches),
    )
    for w in config.watches:
        logger.info(
            "  watch %r: campground %s, %s → %s, %s night(s), every %s min, auto_hold=%s dry_run=%s",
            w.name, w.campground_id, w.start_date, w.end_date,
            w.nights, w.polling_interval, w.auto_hold, w.dry_run,
        )

    supervisor = CamplySupervisor(config, runtime_dir)

    def _handle_signal(signum, frame):
        logger.info("received signal %s — shutting down", signum)
        supervisor.shutdown()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
