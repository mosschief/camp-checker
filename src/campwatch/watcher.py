"""
Watcher entrypoint: reads config.yaml, generates one camply yaml-config per
watch entry, and supervises one `camply campsites --yaml-config <file>`
process per watch. Crashed processes restart with exponential backoff (camply
already rate-limits its own polling; the backoff only guards against crash
loops, never against the polling floor).

The supervisor also hot-reloads: when config.yaml changes (e.g. the dashboard
adds or removes a watch), it reconciles the running camply processes —
starting new watches, stopping removed ones, and restarting any whose
search parameters changed — without a container restart.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import yaml

from campwatch.camply_gen import camply_search_dict
from campwatch.config import AppConfig, ConfigError, build_config, load_config, read_raw

logger = logging.getLogger(__name__)

RESTART_BACKOFF_INITIAL = 10  # seconds
RESTART_BACKOFF_MAX = 600


@dataclass
class _WatchProc:
    signature: str
    path: Path
    proc: Optional[subprocess.Popen] = None
    backoff: float = RESTART_BACKOFF_INITIAL
    last_start: float = 0.0


class CamplySupervisor:
    def __init__(self, config: AppConfig, runtime_dir: Path, config_path: Path):
        self.config = config
        self.runtime_dir = runtime_dir
        self.config_path = Path(config_path)
        self._state: Dict[str, _WatchProc] = {}
        self._last_mtime = _safe_mtime(self.config_path)
        self._stop = threading.Event()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    def _camply_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        # CAMPWATCH_WEBHOOK_URL overrides config (set by the combined
        # single-container entrypoint, where the receiver is on localhost)
        env["WEBHOOK_URL"] = (
            os.environ.get("CAMPWATCH_WEBHOOK_URL") or self.config.receiver.webhook_url
        )
        env.setdefault("WEBHOOK_HEADERS", '{"Content-Type": "application/json"}')
        return env

    def _signature(self, watch) -> str:
        return json.dumps(camply_search_dict(self.config, watch), sort_keys=True)

    def _write_yaml(self, watch) -> Path:
        path = self.runtime_dir / f"{watch.name}.camply.yaml"
        path.write_text(yaml.safe_dump(camply_search_dict(self.config, watch), sort_keys=False))
        return path

    def _spawn(self, name: str, yaml_path: Path) -> subprocess.Popen:
        logger.info("[%s] starting camply (%s)", name, yaml_path.name)
        return subprocess.Popen(
            [sys.executable, "-m", "camply", "campsites", "--yaml-config", str(yaml_path)],
            env=self._camply_env(),
            stdout=None,  # inherit → structured container stdout
            stderr=None,
        )

    def _terminate(self, name: str, entry: _WatchProc) -> None:
        if entry.proc and entry.proc.poll() is None:
            logger.info("[%s] stopping camply", name)
            entry.proc.terminate()
            try:
                entry.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                entry.proc.kill()

    def reconcile(self) -> None:
        """Bring running camply processes in line with self.config."""
        desired = {w.name: w for w in self.config.watches}

        for name in list(self._state):
            if name not in desired:
                self._terminate(name, self._state.pop(name))
                logger.info("[%s] watch removed", name)

        for name, watch in desired.items():
            signature = self._signature(watch)
            entry = self._state.get(name)
            if entry is None:
                path = self._write_yaml(watch)
                self._state[name] = _WatchProc(
                    signature=signature, path=path,
                    proc=self._spawn(name, path), last_start=time.monotonic(),
                )
            elif entry.signature != signature:
                logger.info("[%s] watch changed — restarting camply", name)
                self._terminate(name, entry)
                path = self._write_yaml(watch)
                entry.signature = signature
                entry.path = path
                entry.backoff = RESTART_BACKOFF_INITIAL
                entry.proc = self._spawn(name, path)
                entry.last_start = time.monotonic()

    def _reload_if_changed(self) -> None:
        mtime = _safe_mtime(self.config_path)
        if mtime == self._last_mtime:
            return
        self._last_mtime = mtime
        try:
            config = build_config(read_raw(self.config_path), self.config_path.parent, dict(os.environ))
        except ConfigError as exc:
            logger.error("config reload rejected, keeping current watches: %s", exc)
            return
        self.config = config
        logger.info("config.yaml changed — reconciling watches")
        self.reconcile()

    def run(self) -> int:
        self.reconcile()
        try:
            while not self._stop.is_set():
                self._reload_if_changed()
                for name, entry in list(self._state.items()):
                    proc = entry.proc
                    if proc is None or proc.poll() is None:
                        continue
                    ran_for = time.monotonic() - entry.last_start
                    if ran_for > 300:  # ran fine for a while → reset backoff
                        entry.backoff = RESTART_BACKOFF_INITIAL
                    logger.warning(
                        "[%s] camply exited with code %s after %.0fs; restarting in %.0fs",
                        name, proc.returncode, ran_for, entry.backoff,
                    )
                    if self._stop.wait(entry.backoff):
                        break
                    entry.backoff = min(entry.backoff * 2, RESTART_BACKOFF_MAX)
                    entry.proc = self._spawn(name, entry.path)
                    entry.last_start = time.monotonic()
                self._stop.wait(3)
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        self._stop.set()
        for name, entry in self._state.items():
            self._terminate(name, entry)


def _safe_mtime(path: Path) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


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

    supervisor = CamplySupervisor(config, runtime_dir, config_path)

    def _handle_signal(signum, frame):
        logger.info("received signal %s — shutting down", signum)
        supervisor.shutdown()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
