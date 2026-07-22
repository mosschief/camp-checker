"""Reconcile logic for the camply supervisor (no real camply processes)."""

from pathlib import Path

import pytest

from campwatch.config import load_config
from campwatch.watcher import CamplySupervisor
from tests.conftest import FULL_ENV


class FakeProc:
    def __init__(self):
        self._alive = True
        self.returncode = None
        self.terminated = False

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self.terminated = True
        self._alive = False
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._alive = False


@pytest.fixture
def supervisor(app_config, config_dir, tmp_path, monkeypatch):
    spawned = []

    sup = CamplySupervisor(app_config, tmp_path / "runtime", config_dir / "config.yaml")

    def fake_spawn(name, path):
        p = FakeProc()
        spawned.append((name, p))
        return p

    monkeypatch.setattr(sup, "_spawn", fake_spawn)
    return sup, spawned


def test_reconcile_starts_one_per_watch(supervisor):
    sup, spawned = supervisor
    sup.reconcile()
    assert set(sup._state.keys()) == {"kanaskat-palmer-aug", "deception-pass-backup"}
    assert len(spawned) == 2
    # each watch got a generated camply yaml
    for name in sup._state:
        assert sup._state[name].path.is_file()


def test_reconcile_stops_removed_watch(supervisor, config_dir):
    sup, _ = supervisor
    sup.reconcile()
    removed_proc = sup._state["deception-pass-backup"].proc

    from campwatch import store
    store.remove_watch(config_dir / "config.yaml", "deception-pass-backup", dict(FULL_ENV))
    sup.config = load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))
    sup.reconcile()

    assert "deception-pass-backup" not in sup._state
    assert removed_proc.terminated is True


def test_reconcile_restarts_changed_watch(supervisor, config_dir):
    sup, spawned = supervisor
    sup.reconcile()
    old_proc = sup._state["deception-pass-backup"].proc
    spawn_count_before = len(spawned)

    from campwatch import store
    store.upsert_watch(
        config_dir / "config.yaml",
        {"name": "deception-pass-backup", "campground_id": 333,
         "start_date": "2026-08-20", "end_date": "2026-08-25", "notify": "phone_pushover"},
        dict(FULL_ENV),
    )
    sup.config = load_config(config_dir / "config.yaml", environ=dict(FULL_ENV))
    sup.reconcile()

    assert old_proc.terminated is True                     # old process stopped
    assert len(spawned) == spawn_count_before + 1          # exactly one restart
    assert sup._state["deception-pass-backup"].proc is not old_proc


def test_reconcile_no_op_when_unchanged(supervisor):
    sup, spawned = supervisor
    sup.reconcile()
    sup.reconcile()  # nothing changed
    assert len(spawned) == 2  # no extra spawns
