"""
Single-container entrypoint (Unraid mode): runs the receiver and the watcher
in one container as supervised subprocesses. camply's webhook is pointed at
the in-container receiver on localhost, so config.receiver.webhook_url does
not need editing.

If either child dies it takes the container down with it — the container
restart policy (Unraid's default, or restart: unless-stopped) brings the
whole pair back in a consistent state.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, Optional

from campwatch.config import ConfigError, load_config

logger = logging.getLogger(__name__)

RECEIVER_STARTUP_TIMEOUT = 30  # seconds to wait for /healthz before starting camply


def _spawn(module: str, env: Dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", module], env=env, stdout=None, stderr=None)


def _wait_for_health(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def main() -> int:
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

    port = config.receiver.port
    env = dict(os.environ)
    # Both services live in this container, so camply posts to localhost
    # regardless of what receiver.webhook_url says (that setting is for the
    # split compose deployment). An explicit CAMPWATCH_WEBHOOK_URL still wins.
    env.setdefault("CAMPWATCH_WEBHOOK_URL", f"http://127.0.0.1:{port}/camply")

    logger.info("combined mode: starting receiver on port %s", port)
    receiver = _spawn("campwatch.receiver", env)
    watcher: Optional[subprocess.Popen] = None
    children = [receiver]

    stopping = False

    def _handle_signal(signum, frame):
        nonlocal stopping
        stopping = True
        logger.info("received signal %s — shutting down", signum)
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    exit_code = 0
    try:
        if not _wait_for_health(port, RECEIVER_STARTUP_TIMEOUT):
            logger.error("receiver did not become healthy within %ss", RECEIVER_STARTUP_TIMEOUT)
            return 1
        if stopping:
            return 0

        logger.info("receiver healthy — starting watcher")
        watcher = _spawn("campwatch.watcher", env)
        children.append(watcher)

        while not stopping:
            for child in children:
                if child.poll() is not None:
                    name = "receiver" if child is receiver else "watcher"
                    logger.error(
                        "%s exited with code %s — stopping container so the "
                        "restart policy can bring both services back",
                        name, child.returncode,
                    )
                    stopping = True
                    exit_code = child.returncode or 1
                    break
            else:
                time.sleep(2)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 15
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
