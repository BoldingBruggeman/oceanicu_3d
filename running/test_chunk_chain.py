"""Tests for chunk_chain.py's branching logic.

No existing pytest convention exists anywhere in this repo (confirmed via
a repo-wide search before writing this file) -- these tests follow the
style already used in ocean-data/ocean-post's own suites instead (plain
pytest, no fixtures framework beyond pytest's own).

chain() takes its experiment_tracking module (`rt`) and its chunk runner
(`run_chunk`) as injectable keyword arguments specifically so these tests
can supply fakes -- no real SQLite registry, no real chunk_runner.py
subprocess, no real sleep. See chunk_chain.chain()'s own docstring.

Run with:  python3 -m pytest test_chunk_chain.py -v
(requires pytest; not run automatically by anything in this repo today)
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Union

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chunk_chain  # noqa: E402


class FakeConn:
    """Placeholder -- the fake rt below never actually touches it, it's
    just the object chain() passes through to rt's own functions."""


class FakeRT:
    """Minimal stand-in for experiment_tracking, configured per-test with
    just the state that test needs. Every function chain() actually calls
    on `rt` is implemented; nothing else is (an AttributeError on a
    function chain() doesn't use would mean chain() changed and this fake
    needs updating, not an oversight here)."""

    def __init__(self, experiments: dict, delay_remaining: Union[float, list] = 0.0,
                 next_queued: Optional[str] = None):
        self.experiments = experiments
        self.delay_remaining_queue = (
            delay_remaining if isinstance(delay_remaining, list) else [delay_remaining]
        )
        self.next_queued = next_queued
        self.delay_calls = 0
        self.connect_calls = 0

    @contextmanager
    def connect(self, db):
        self.connect_calls += 1
        yield FakeConn()

    def get_chunk_delay_remaining(self, conn) -> float:
        self.delay_calls += 1
        if self.delay_calls - 1 < len(self.delay_remaining_queue):
            return self.delay_remaining_queue[self.delay_calls - 1]
        return 0.0

    def get_experiment(self, conn, experiment_id):
        # Mimic a real sqlite3.Row: every column is always present (None
        # if unset), never a missing key -- a plain dict literal in a
        # test fixture that omits a column must behave the same way, or
        # chain()'s own `experiment["chunk_delay_seconds"]`-style access
        # (which never does .get()) would KeyError here but not for real.
        exp = self.experiments.get(experiment_id)
        if exp is None:
            return None
        return {"status": None, "paused": False, "chunk_delay_seconds": 0,
                "experiment_root": None, **exp}

    def is_paused(self, conn, experiment_id, experiment_root=None) -> bool:
        exp = self.experiments.get(experiment_id)
        return bool(exp and exp.get("paused"))

    def next_experiment_to_start(self, conn):
        return self.next_queued


def make_run_chunk(*codes):
    """Returns a callable usable as chain()'s `run_chunk=` -- yields each
    code in *codes* in turn, raising if called more times than given
    (a test calling it more than expected is a bug in that test, not
    something to paper over with a repeating default)."""
    codes_iter = iter(codes)

    def _run_chunk(experiment_id, db, python=None):
        try:
            return next(codes_iter)
        except StopIteration:
            raise AssertionError(
                f"run_chunk called more times than expected (experiment_id={experiment_id!r})"
            )
    return _run_chunk


@pytest.fixture(autouse=True)
def _reset_shutdown_flag():
    chunk_chain._shutdown_requested = False
    yield
    chunk_chain._shutdown_requested = False


def test_chunk_fails_stops_with_exit_2():
    rt = FakeRT(experiments={"E1": {"status": "running"}})
    run_chunk = make_run_chunk(2)

    result = chunk_chain.chain("E1", None, rt=rt, run_chunk=run_chunk)

    assert result == 2


def test_chunk_succeeds_not_paused_loops_then_eventually_fails_to_stop_test():
    # exit 0 (not paused) -> loop on same experiment -> exit 2 to end the test
    rt = FakeRT(experiments={"E1": {"status": "running", "paused": False}})
    run_chunk = make_run_chunk(0, 2)

    result = chunk_chain.chain("E1", None, rt=rt, run_chunk=run_chunk)

    assert result == 2


def test_chunk_succeeds_but_now_paused_stops_cleanly():
    rt = FakeRT(experiments={"E1": {"status": "running", "paused": True}})
    run_chunk = make_run_chunk(0)

    result = chunk_chain.chain("E1", None, rt=rt, run_chunk=run_chunk)

    assert result == 0


def test_exit_1_complete_picks_up_next_queued_experiment():
    rt = FakeRT(
        experiments={
            "E1": {"status": "complete"},
            "E2": {"status": "running", "paused": False},
        },
        next_queued="E2",
    )
    # E1 finishes (exit 1, complete) -> picks up E2 -> E2's chunk fails (exit 2) to stop the test
    run_chunk = make_run_chunk(1, 2)

    result = chunk_chain.chain("E1", None, rt=rt, run_chunk=run_chunk)

    assert result == 2


def test_exit_1_complete_with_warnings_also_picks_up_next():
    rt = FakeRT(
        experiments={
            "E1": {"status": "complete_with_warnings"},
            "E2": {"status": "running", "paused": False},
        },
        next_queued="E2",
    )
    run_chunk = make_run_chunk(1, 2)

    result = chunk_chain.chain("E1", None, rt=rt, run_chunk=run_chunk)

    assert result == 2


def test_exit_1_complete_with_nothing_queued_stops_cleanly():
    rt = FakeRT(experiments={"E1": {"status": "complete"}}, next_queued=None)
    run_chunk = make_run_chunk(1)

    result = chunk_chain.chain("E1", None, rt=rt, run_chunk=run_chunk)

    assert result == 0


def test_exit_1_not_complete_means_paused_stops_cleanly():
    # exit 1 (nothing to do) but status isn't complete/complete_with_warnings
    # -> chain() treats this as "paused", per chunk_runner.py's own exit-code
    # contract (0=ran a chunk, 1=nothing to do [stop_date reached OR paused],
    # 2=chunk failed).
    rt = FakeRT(experiments={"E1": {"status": "paused"}})
    run_chunk = make_run_chunk(1)

    result = chunk_chain.chain("E1", None, rt=rt, run_chunk=run_chunk)

    assert result == 0


def test_shutdown_signal_before_first_chunk_stops_without_running_one():
    chunk_chain._shutdown_requested = True
    rt = FakeRT(experiments={"E1": {"status": "running"}})

    def _run_chunk(*a, **k):
        raise AssertionError("run_chunk must not be called once shutdown is requested")

    result = chunk_chain.chain("E1", None, rt=rt, run_chunk=_run_chunk)

    assert result == 0


def test_delay_all_is_waited_out_before_each_chunk(monkeypatch):
    # First check: 5s remaining: should poll and wait. Second check: 0 ->
    # proceed. Patch _wait_or_shutdown to avoid a real sleep and to prove
    # it was actually invoked with the remaining delay.
    waited = []
    monkeypatch.setattr(chunk_chain, "_wait_or_shutdown", lambda secs: waited.append(secs) or False)

    rt = FakeRT(experiments={"E1": {"status": "running"}}, delay_remaining=[5.0, 0.0])
    run_chunk = make_run_chunk(2)  # stop the test after one chunk

    result = chunk_chain.chain("E1", None, rt=rt, run_chunk=run_chunk)

    assert result == 2
    assert waited == [5.0]


def test_per_experiment_chunk_delay_is_waited_out(monkeypatch):
    waited = []
    monkeypatch.setattr(chunk_chain, "_wait_or_shutdown", lambda secs: waited.append(secs) or False)

    rt = FakeRT(experiments={"E1": {"status": "running", "chunk_delay_seconds": 30}})
    run_chunk = make_run_chunk(2)

    result = chunk_chain.chain("E1", None, rt=rt, run_chunk=run_chunk)

    assert result == 2
    assert waited == [30]


def test_run_chunk_once_builds_expected_command(monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 0

    def fake_run(cmd, cwd=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return FakeCompleted()

    monkeypatch.setattr(chunk_chain.subprocess, "run", fake_run)

    code = chunk_chain.run_chunk_once("NSe/CMEMS/v01E", "/tmp/registry.sqlite", python="/usr/bin/python3")

    assert code == 0
    assert captured["cmd"] == [
        "/usr/bin/python3",
        str(chunk_chain.SCRIPT_DIR / "chunk_runner.py"),
        "--experiment-id", "NSe/CMEMS/v01E",
        "--db", "/tmp/registry.sqlite",
    ]
    assert captured["cwd"] == str(chunk_chain.SCRIPT_DIR)


def test_run_chunk_once_omits_db_flag_when_none(monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 1

    def fake_run(cmd, cwd=None):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(chunk_chain.subprocess, "run", fake_run)

    code = chunk_chain.run_chunk_once("E1", None, python="/usr/bin/python3")

    assert code == 1
    assert "--db" not in captured["cmd"]
