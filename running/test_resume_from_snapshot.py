"""Tests for `rerun --from-snapshot`: restart_snapshots.py's completeness check, and
chunk_runner.py continuing an interrupted chunk from its newest complete snapshot.

Run with:  python3 -m pytest test_resume_from_snapshot.py -v   (needs netCDF4)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

netCDF4 = pytest.importorskip("netCDF4")
import cftime  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import experiment_tracking as rt  # noqa: E402
import restart_snapshots  # noqa: E402

EXP = "T/test/snap01"


def _write_restart(path: Path, years: list[int], *, partial_last: bool = False, unwritten_last: bool = False) -> None:
    """A pyGETM-like restart: yearly snapshots on 1 January, land (x == 0) masked.
    partial_last: the last record's `temp` only half written (killed mid-write);
    unwritten_last: its time written but no field data at all."""
    with netCDF4.Dataset(path, "w") as nc:
        nc.createDimension("time", None)
        nc.createDimension("z", 2)
        nc.createDimension("y", 3)
        nc.createDimension("x", 4)
        t = nc.createVariable("time", "f8", ("time",))
        t.units = "seconds since 2010-01-01 00:00:00"
        t.calendar = "standard"
        temp = nc.createVariable("temp", "f8", ("time", "z", "y", "x"), fill_value=-9999.0)
        zeta = nc.createVariable("zeta", "f8", ("time", "y", "x"), fill_value=-9999.0)
        nc.createVariable("H", "f8", ("y", "x"))[:] = 10.0
        for i, year in enumerate(years):
            t[i] = netCDF4.date2num(cftime.datetime(year, 1, 1, calendar="standard"), t.units, t.calendar)
            last = i == len(years) - 1
            if last and unwritten_last:
                continue
            field = np.ma.masked_all((2, 3, 4))
            field[:, :, 1:] = 10.0 + i
            if last and partial_last:
                field[1, :, :] = np.ma.masked
            temp[i] = field
            surface = np.ma.masked_all((3, 4))
            surface[:, 1:] = 0.1 * i
            zeta[i] = surface


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OCEANICU_DB_BACKUP_EVERY_N_WRITES", "0")
    db = tmp_path / "registry.sqlite"
    root = tmp_path / "exp"
    root.mkdir()
    for name in ("generated_t.py", "generated_t_config.yaml", "gotm.yaml"):
        (root / name).write_text("")
    with rt.connect(db) as conn:
        rt.add_experiment(
            conn, experiment_id=EXP, experiment_root=str(root),
            script="generated_t.py", config="generated_t_config.yaml",
            initial_date="2010-01-01", stop_date="2030-01-01", chunk_multiplier=5, launcher="mpiexec",
        )
    return db, root


def _start_chunk0_interrupted(db: Path, root: Path, **restart_kwargs) -> Path:
    """Chunk 0 (2010 -> 2015) recorded as running, its restart holding 2011..2014."""
    chunk_dir = root / "000_20100101_20150101"
    chunk_dir.mkdir()
    restart = chunk_dir / "restart_generated_t_config_20150101.nc"
    if restart_kwargs.pop("missing", False) is False:
        _write_restart(restart, [2011, 2012, 2013, 2014], **restart_kwargs)
    with rt.connect(db) as conn:
        rt.start_chunk(conn, experiment_id=EXP, chunk_index=0, start="2010-01-01", stop="2015-01-01",
                       chunk_dir=str(chunk_dir), load_restart=None, save_restart=str(restart))
    return restart


def _run(db, *extra, path=None):
    env = dict(os.environ, OCEANICU_DB_BACKUP_EVERY_N_WRITES="0")
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        [sys.executable, str(HERE / "chunk_runner.py"), "--db", str(db), "--experiment-id", EXP, *extra],
        capture_output=True, text=True, env=env,
    )


def _no_launcher(tmp_path) -> str:
    empty = tmp_path / "bin"
    empty.mkdir(exist_ok=True)
    return str(empty)


# --- restart_snapshots ---------------------------------------------------------------

def test_half_written_last_snapshot_is_incomplete(tmp_path):
    p = tmp_path / "r.nc"
    _write_restart(p, [2011, 2012, 2013], partial_last=True)
    ins = restart_snapshots.inspect_restart(p)
    assert [s.complete for s in ins.snapshots] == [True, True, False]
    assert "temp" in ins.snapshots[-1].reason
    snap, _ = restart_snapshots.last_complete_snapshot(ins)
    assert snap.date == "2012-01-01" and snap.iso == "2012-01-01T00:00:00"


def test_unwritten_last_snapshot_is_incomplete(tmp_path):
    p = tmp_path / "r.nc"
    _write_restart(p, [2011, 2012], unwritten_last=True)
    snap, _ = restart_snapshots.last_complete_snapshot(restart_snapshots.inspect_restart(p))
    assert snap.date == "2011-01-01"


def test_all_complete_and_requested(tmp_path):
    p = tmp_path / "r.nc"
    _write_restart(p, [2011, 2012, 2013])
    ins = restart_snapshots.inspect_restart(p)
    assert all(s.complete for s in ins.snapshots)
    assert restart_snapshots.last_complete_snapshot(ins)[0].date == "2013-01-01"
    assert restart_snapshots.last_complete_snapshot(ins, "2012-01-01")[0].date == "2012-01-01"
    snap, reason = restart_snapshots.last_complete_snapshot(ins, "2015-01-01")
    assert snap is None and "not in" in reason


def test_unreadable_file(tmp_path):
    p = tmp_path / "r.nc"
    p.write_bytes(b"\x89HDF\r\n\x1a\n" + b"\0" * 100)  # a truncated HDF5 file
    ins = restart_snapshots.inspect_restart(p)
    assert not ins.readable
    assert restart_snapshots.last_complete_snapshot(ins)[0] is None


# --- registry + chunk_runner ------------------------------------------------------------

def test_rerun_from_snapshot_keeps_the_row(env):
    db, root = env
    _start_chunk0_interrupted(db, root)
    with rt.connect(db) as conn:
        n = rt.rerun_from(conn, EXP, force=True, cancel_jobs=False, from_snapshot=True)
        row = rt.get_chunk(conn, EXP, 0)
        exp = rt.get_experiment(conn, EXP)
    assert n == 0
    assert row["status"] == "failed" and row["stop"] == "2015-01-01"
    assert exp["resume_from_snapshot"] == 1


def test_rerun_from_snapshot_refuses_a_finished_chunk(env):
    db, root = env
    _start_chunk0_interrupted(db, root)
    with rt.connect(db) as conn:
        rt.finish_chunk(conn, experiment_id=EXP, chunk_index=0, exit_code=0)
        with pytest.raises(ValueError, match="finished normally"):
            rt.rerun_from(conn, EXP, force=True, cancel_jobs=False, from_snapshot=True)


def test_chunk_runner_continues_from_last_complete_snapshot(env, tmp_path):
    db, root = env
    restart = _start_chunk0_interrupted(db, root, partial_last=True)
    (root / "000_20100101_20150101" / "nse_2d.nc").write_bytes(b"garbage")
    with rt.connect(db) as conn:
        rt.rerun_from(conn, EXP, force=True, cancel_jobs=False, from_snapshot=True)

    r = _run(db, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "continuing from its snapshot 2013-01-01T00:00:00" in r.stdout
    assert "nse_2d.nc: unreadable" in r.stdout
    with rt.connect(db) as conn:  # a dry run changes nothing
        assert rt.get_chunk(conn, EXP, 0)["status"] == "failed"
        assert rt.get_experiment(conn, EXP)["resume_from_snapshot"] == 1

    r = _run(db, path=_no_launcher(tmp_path))  # the launch itself fails; the registry steps are what count
    assert "continuing from its snapshot 2013-01-01T00:00:00" in r.stdout, r.stderr
    with rt.connect(db) as conn:
        c0, c1 = rt.get_chunk(conn, EXP, 0), rt.get_chunk(conn, EXP, 1)
        exp = rt.get_experiment(conn, EXP)
        events = [h["event"] for h in rt.list_history(conn, EXP)]
    assert c0["status"] == "done" and c0["stop"] == "2013-01-01"
    assert (c1["start"], c1["stop"]) == ("2013-01-01", "2018-01-01")
    assert c1["load_restart"] == str(restart)
    assert c1["load_restart_time"] == "2013-01-01T00:00:00"
    assert c1["chunk_dir"].endswith("001_20130101_20180101")
    assert exp["resume_from_snapshot"] == 0 and exp["next_load_restart_time"] is None
    assert "resumed_from_snapshot" in events


def test_chunk_runner_uses_the_requested_snapshot(env, tmp_path):
    db, root = env
    _start_chunk0_interrupted(db, root)
    with rt.connect(db) as conn:
        rt.rerun_from(conn, EXP, force=True, cancel_jobs=False, from_snapshot=True, restart_time="2012-01-01T00:00:00")
    _run(db, path=_no_launcher(tmp_path))
    with rt.connect(db) as conn:
        c1 = rt.get_chunk(conn, EXP, 1)
    assert c1["start"] == "2012-01-01" and c1["load_restart_time"] == "2012-01-01T00:00:00"


def test_chunk_runner_redoes_the_chunk_without_a_usable_snapshot(env, tmp_path):
    db, root = env
    _start_chunk0_interrupted(db, root, missing=True)
    with rt.connect(db) as conn:
        rt.rerun_from(conn, EXP, force=True, cancel_jobs=False, from_snapshot=True)
    r = _run(db, path=_no_launcher(tmp_path))
    assert "redoing the chunk from its start" in r.stdout, r.stderr
    with rt.connect(db) as conn:
        c0 = rt.get_chunk(conn, EXP, 0)
        exp = rt.get_experiment(conn, EXP)
    assert (c0["start"], c0["stop"]) == ("2010-01-01", "2015-01-01")
    assert c0["load_restart"] is None
    assert exp["resume_from_snapshot"] == 0


def test_cli_queue_round_trip(env, tmp_path):
    """--from-snapshot survives the queue: stored as a bool, replayed as a bare flag."""
    import yaml

    q = tmp_path / "queue.yaml"
    r = subprocess.run([sys.executable, str(HERE / "oceanicu_experiments.py"), "--queue", str(q), "rerun",
                        "--experiment-id", EXP, "--from-snapshot", "--force"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    entry = yaml.safe_load(q.read_text())["commands"][0]
    assert entry["args"]["from_snapshot"] is True
    import get_commands_and_update_registry as g

    assert "--from-snapshot" in g._args_dict_to_cli(entry["args"])
