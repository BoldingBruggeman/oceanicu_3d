"""An ssh:// registry path: a read-only local snapshot, fetched with one ssh call.

A stand-in `ssh` on PATH runs the remote command locally (drops the host argument).
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import experiment_tracking as rt  # noqa: E402


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ssh = bindir / "ssh"
    ssh.write_text(f'#!/bin/sh\nshift\nexec "{sys.executable}" -I -\n')   # "ssh HOST python3" -> local python
    ssh.chmod(ssh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{__import__('os').environ['PATH']}")
    monkeypatch.setattr(rt, "_snapshots", {})


@pytest.fixture
def registry(tmp_path):
    db = tmp_path / "remote" / "submission_registry.sqlite"
    with rt.connect(str(db)) as conn:
        rt.add_experiment(conn, experiment_id="NSe/test/run01", experiment_root="NSe/test/run01",
                          script="s.py", config="c.yaml", initial_date="2010-01-01",
                          stop_date="2011-01-01")
    (db.parent / "PAUSE_ALL").touch()                      # next to the real registry
    return db


def test_snapshot_reads(fake_ssh, registry):
    with rt.connect(f"ssh://somehost{registry}") as conn:
        rows = rt.list_experiments(conn)
        assert [r["experiment_id"] for r in rows] == ["NSe/test/run01"]
        assert rt.get_chunk_delay_remaining(conn) == 0.0
        assert not rt.pause_all_sentinel_path(conn).exists()   # the snapshot has no sentinels
        assert rt.pause_all_sentinel_path(conn).parent != registry.parent


def test_snapshot_refuses_writes(fake_ssh, registry):
    with pytest.raises(RuntimeError, match="read-only snapshot"):
        with rt.connect(f"ssh://somehost{registry}") as conn:
            rt.set_priority(conn, "NSe/test/run01", 5)
    with rt.connect(str(registry)) as conn:                  # the real one is untouched
        assert rt.get_experiment(conn, "NSe/test/run01")["priority"] == 0


def test_snapshot_missing_registry(fake_ssh, tmp_path):
    with pytest.raises(RuntimeError, match="no registry"):
        with rt.connect(f"ssh://somehost{tmp_path}/none.sqlite"):
            pass
    assert rt.fetch_snapshot(rt._parse_db_spec(f"ssh://h{tmp_path}/none.sqlite"), tmp_path / "x") is False
