"""Tests for reap_orphaned_chunks.py -- the periodic watchdog that marks a
'running' chunk as failed once its SLURM job is actually dead (see that
file's own docstring: a real production incident, 2026-09-01, where a
whole job's cgroup was OOM-killed before chunk_runner.py itself got the
chance to record chunk_failed, leaving the registry silently stuck showing
status=running forever).

Uses a REAL temporary SQLite registry (via experiment_tracking.connect) --
list_running_chunks/start_chunk/finish_chunk are themselves pure-SQLite,
no SLURM dependency, so faking them would just re-test the fakes. The ONE
real external dependency (squeue, via is_slurm_job_running) is monkeypatched
on the real experiment_tracking module, matching this repo's own established
convention (test_chunk_chain.py) of injecting fakes at the narrowest point
that's actually impure, not reimplementing pure logic as a mock.

Run with:  python3 -m pytest test_reap_orphaned_chunks.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import experiment_tracking as rt  # noqa: E402
import reap_orphaned_chunks  # noqa: E402


def _seed_chunk(conn, *, experiment_id: str, slurm_job_id: str, age_days: float = 0.0):
    """Register an experiment and start one chunk for it, status='running'.
    age_days backdates start_time (start_chunk itself always uses "now")."""
    rt.add_experiment(
        conn, experiment_id=experiment_id, experiment_root="/tmp/x", script="s.py",
        config="c.yaml", initial_date="2020-01-01", stop_date="2021-01-01",
    )
    rt.start_chunk(
        conn, experiment_id=experiment_id, chunk_index=0, start="2020-01-01", stop="2020-06-01",
        chunk_dir="/tmp/x/0", load_restart=None, save_restart="/tmp/x/0/restart.nc",
        slurm_job_id=slurm_job_id,
    )
    if age_days:
        backdated = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(timespec="seconds")
        conn.execute("UPDATE chunks SET start_time = ? WHERE experiment_id = ?", (backdated, experiment_id))
        conn.commit()


def test_alive_job_is_left_alone(tmp_path, monkeypatch, capsys):
    db = tmp_path / "registry.sqlite"
    with rt.connect(str(db)) as conn:
        _seed_chunk(conn, experiment_id="exp/alive", slurm_job_id="111")

    monkeypatch.setattr(rt, "is_slurm_job_running", lambda job_id: True)
    monkeypatch.setenv("OCEANICU_EXPERIMENT_DB", str(db))
    assert reap_orphaned_chunks.main() == 0

    with rt.connect(str(db)) as conn:
        row = rt.get_running_chunk(conn, "exp/alive")
        assert row is not None and row["status"] == "running"
    assert "nothing to reap" in capsys.readouterr().out


def test_confirmed_dead_job_is_reaped(tmp_path, monkeypatch, capsys):
    db = tmp_path / "registry.sqlite"
    with rt.connect(str(db)) as conn:
        _seed_chunk(conn, experiment_id="exp/dead", slurm_job_id="222")

    monkeypatch.setattr(rt, "is_slurm_job_running", lambda job_id: False)
    monkeypatch.setenv("OCEANICU_EXPERIMENT_DB", str(db))
    assert reap_orphaned_chunks.main() == 0

    with rt.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT status, exit_code FROM chunks WHERE experiment_id = ? AND chunk_index = 0",
            ("exp/dead",),
        ).fetchone()
        assert row["status"] == "failed"
        assert row["exit_code"] == rt.EXIT_CODE_ORPHANED
    assert "reaped 1 orphaned chunk" in capsys.readouterr().out


def test_inconclusive_and_fresh_is_left_alone(tmp_path, monkeypatch):
    """squeue unavailable/inconclusive (None) AND not old enough yet --
    the conservative fallback that stops a slow squeue or a brief network
    blip from causing a false reap."""
    db = tmp_path / "registry.sqlite"
    with rt.connect(str(db)) as conn:
        _seed_chunk(conn, experiment_id="exp/fresh", slurm_job_id="333", age_days=0.1)

    monkeypatch.setattr(rt, "is_slurm_job_running", lambda job_id: None)
    monkeypatch.setenv("OCEANICU_EXPERIMENT_DB", str(db))
    assert reap_orphaned_chunks.main() == 0

    with rt.connect(str(db)) as conn:
        row = rt.get_running_chunk(conn, "exp/fresh")
        assert row is not None and row["status"] == "running"


def test_inconclusive_but_stale_is_reaped(tmp_path, monkeypatch):
    """squeue inconclusive (None) but the chunk has been 'running' for
    more than 4 days -- old enough that leaving it alone would mean never
    reaping a job squeue can never resolve (e.g. one whose job id rolled
    out of squeue's own history)."""
    db = tmp_path / "registry.sqlite"
    with rt.connect(str(db)) as conn:
        _seed_chunk(conn, experiment_id="exp/stale", slurm_job_id="444", age_days=5.0)

    monkeypatch.setattr(rt, "is_slurm_job_running", lambda job_id: None)
    monkeypatch.setenv("OCEANICU_EXPERIMENT_DB", str(db))
    assert reap_orphaned_chunks.main() == 0

    with rt.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT status, exit_code FROM chunks WHERE experiment_id = ? AND chunk_index = 0",
            ("exp/stale",),
        ).fetchone()
        assert row["status"] == "failed"
        assert row["exit_code"] == rt.EXIT_CODE_ORPHANED


def test_multiple_experiments_counted_correctly(tmp_path, monkeypatch, capsys):
    db = tmp_path / "registry.sqlite"
    with rt.connect(str(db)) as conn:
        _seed_chunk(conn, experiment_id="exp/a", slurm_job_id="1")
        _seed_chunk(conn, experiment_id="exp/b", slurm_job_id="2")
        _seed_chunk(conn, experiment_id="exp/c", slurm_job_id="3")

    # a: alive: b, c: dead
    monkeypatch.setattr(rt, "is_slurm_job_running", lambda job_id: job_id == "1")
    monkeypatch.setenv("OCEANICU_EXPERIMENT_DB", str(db))
    assert reap_orphaned_chunks.main() == 0

    with rt.connect(str(db)) as conn:
        assert rt.get_running_chunk(conn, "exp/a")["status"] == "running"
        for exp in ("exp/b", "exp/c"):
            row = conn.execute(
                "SELECT status FROM chunks WHERE experiment_id = ? AND chunk_index = 0", (exp,),
            ).fetchone()
            assert row["status"] == "failed"
    assert "reaped 2 orphaned chunk(s) (out of 3 checked)" in capsys.readouterr().out


def test_missing_db_env_var_errors_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("OCEANICU_EXPERIMENT_DB", raising=False)
    assert reap_orphaned_chunks.main() == 1
    assert "OCEANICU_EXPERIMENT_DB must be set" in capsys.readouterr().err
