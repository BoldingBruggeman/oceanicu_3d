"""Tests for chunk_runner.py's tracked mode and the registry calls it relies on.

Each test uses its own throwaway registry under pytest's tmp_path; chunk_runner
is run as a subprocess, exactly as run_chunk.slurm / chunk_chain.py run it.

Run with:  python3 -m pytest test_chunk_runner.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import experiment_tracking as rt  # noqa: E402

EXP = "T/test/run01"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OCEANICU_DB_BACKUP_EVERY_N_WRITES", "0")
    db = tmp_path / "registry.sqlite"
    root = tmp_path / "exp"
    root.mkdir()
    (root / "generated_t.py").write_text("")
    (root / "generated_t_config.yaml").write_text("")
    (root / "gotm.yaml").write_text("")
    with rt.connect(db) as conn:
        rt.add_experiment(
            conn, experiment_id=EXP, experiment_root=str(root),
            script="generated_t.py", config="generated_t_config.yaml",
            initial_date="2010-01-01", stop_date="2012-01-01", launcher="mpiexec",
        )
    return db, root


def _run(db, *extra, path=None):
    env = dict(os.environ, OCEANICU_DB_BACKUP_EVERY_N_WRITES="0")
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        [sys.executable, str(HERE / "chunk_runner.py"), "--db", str(db), "--experiment-id", EXP, *extra],
        capture_output=True, text=True, env=env,
    )


def _chunk_dirs(root):
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def test_dry_run_leaves_experiment_untouched(env):
    db, root = env
    (root / "000_20100101_20110101").mkdir()  # a previous attempt, e.g. before a rerun
    r = _run(db, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "cmd:" in r.stdout
    assert _chunk_dirs(root) == ["000_20100101_20110101"]  # not archived, nothing created
    with rt.connect(db) as conn:
        assert rt.get_chunk(conn, EXP, 0) is None


def test_launch_failure_is_recorded_and_exits_2(env, tmp_path):
    """No launcher on PATH: the chunk row must be marked failed (not left
    'running'), and the exit code must not be 1 ('nothing to do')."""
    db, root = env
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    r = _run(db, path=str(empty_bin))
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "FileNotFoundError" in r.stderr
    with rt.connect(db) as conn:
        row = rt.get_chunk(conn, EXP, 0)
    assert row["status"] == "failed"
    assert row["exit_code"] == rt.EXIT_CODE_RUNNER_ERROR
    assert (root / "000_20100101_20110101" / "gotm.yaml").is_file()


def test_unexpected_exception_exits_2(env):
    """A malformed stop date (only reachable by editing the DB) raises
    ValueError inside chunk_runner -- that must be exit 2, not 1."""
    db, _ = env
    with rt.connect(db) as conn:
        conn.execute("UPDATE experiments SET stop_date = '2012-01-01T00:00:00' WHERE experiment_id = ?", (EXP,))
        conn.commit()
    r = _run(db, "--dry-run")
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "ValueError" in r.stderr


def test_restart_time_rejected_for_chunk_0(env):
    db, _ = env
    with rt.connect(db) as conn:
        rt.start_chunk(conn, experiment_id=EXP, chunk_index=0, start="2010-01-01", stop="2011-01-01",
                       chunk_dir="x", load_restart=None, save_restart="r.nc")
        with pytest.raises(ValueError, match="chunk 0"):
            rt.rerun_from(conn, EXP, chunk_index=0, force=True, restart_time="2010-06-01", cancel_jobs=False)


def test_pending_restart_time_cleared_by_any_chunk_start(env):
    db, _ = env
    with rt.connect(db) as conn:
        conn.execute("UPDATE experiments SET next_load_restart_time = '2010-06-01' WHERE experiment_id = ?",
                     (EXP,))
        conn.commit()
        rt.start_chunk(conn, experiment_id=EXP, chunk_index=0, start="2010-01-01", stop="2011-01-01",
                       chunk_dir="x", load_restart=None, save_restart="r.nc")
        assert rt.get_experiment(conn, EXP)["next_load_restart_time"] is None


@pytest.mark.parametrize("bad", ["2017-01-01T00:00:00", "2017-1-1", "20170101"])
def test_cli_rejects_non_yyyy_mm_dd_dates(env, bad):
    db, _ = env
    r = subprocess.run(
        [sys.executable, str(HERE / "oceanicu_experiments.py"), "--db", str(db),
         "set-stop-date", "--experiment-id", EXP, "--stop-date", bad],
        capture_output=True, text=True,
    )
    assert r.returncode == 2 and "YYYY-MM-DD" in r.stderr


@pytest.mark.parametrize("cmd", [["pull-code"], ["stage", "--experiment-root", "/nonexistent", "--source-dir", "/nonexistent"]])
def test_dry_run_refuses_commands_acting_outside_the_registry(env, cmd):
    db, _ = env
    r = subprocess.run(
        [sys.executable, str(HERE / "oceanicu_experiments.py"), "--db", str(db), "--dry-run", *cmd],
        capture_output=True, text=True,
    )
    assert r.returncode == 1 and "doesn't work for" in r.stderr
