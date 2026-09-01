#!/usr/bin/env python
"""reap_orphaned_chunks.py -- periodic watchdog: finds every chunk still
marked 'running' across ALL experiments, checks whether its SLURM job is
actually still alive (experiment_tracking.is_slurm_job_running), and
marks genuinely-dead ones as failed. Same criteria chunk_runner.py's own
start-of-chunk lock/orphan check already uses -- this is just PROACTIVE
instead of LAZY: that check only ever runs the next time someone tries
to start a new chunk for that same experiment, so a dead job just sits
there showing status=running indefinitely if nobody happens to retry it.

Confirmed hitting this for real in production, 2026-09-01: a job died
(`srun: error: ... task 0: Killed`, consistent with an OOM-kill of the
whole job's cgroup at once) before chunk_runner.py itself ever got the
chance to run -- taking it out along with the MPI ranks it was waiting
on, before it could record chunk_failed. The registry silently kept
showing the chunk as running, with zero new history entries, until a
human happened to notice and manually re-run something.

Run this on the LOGIN NODE ONLY -- needs squeue, same requirement as
chunk_runner.py's own liveness check (never on the relay: squeue only
exists on a machine that's actually part of the SLURM cluster). See
setup_experiment_tracking.sh's own printed cron guidance for the
crontab line.

Usage: OCEANICU_EXPERIMENT_DB=/path/submission_registry.sqlite reap_orphaned_chunks.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import experiment_tracking as rt  # noqa: E402


def _ts() -> str:
    """Local time with UTC offset, matching the $(date -Is) convention
    already used across every other script in this pipeline's own log
    output, so all these log files show directly-comparable timestamps."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def main() -> int:
    db = os.environ.get("OCEANICU_EXPERIMENT_DB")
    if not db:
        print(f"{_ts()}: ERROR: OCEANICU_EXPERIMENT_DB must be set.", file=sys.stderr)
        return 1

    reaped = 0
    with rt.connect(db) as conn:
        running = rt.list_running_chunks(conn)
        checked = len(running)
        for row in running:
            experiment_id = row["experiment_id"]
            chunk_index = row["chunk_index"]

            # Exact same liveness/staleness criteria as chunk_runner.py's
            # own start-of-chunk orphan check -- see is_slurm_job_running's
            # own docstring for why this lives in experiment_tracking.py
            # as shared logic rather than being duplicated here.
            alive = rt.is_slurm_job_running(row["slurm_job_id"])
            stale_by_age = False
            if row["start_time"]:
                started = datetime.fromisoformat(row["start_time"])
                stale_by_age = (datetime.now(timezone.utc) - started) > timedelta(days=4)

            if alive:
                continue  # genuinely still running -- leave it alone
            if alive is None and not stale_by_age:
                # squeue unavailable/inconclusive, and not old enough yet
                # to assume orphaned -- same conservative fallback
                # chunk_runner.py's own check uses, so a merely-slow
                # squeue or a brief network blip never causes a false
                # "orphaned" reap.
                continue

            print(f"{_ts()}: {experiment_id}: chunk {chunk_index} (SLURM job "
                  f"{row['slurm_job_id']!r}) marked running but its job is no "
                  f"longer active -- marking failed.")
            rt.finish_chunk(conn, experiment_id=experiment_id, chunk_index=chunk_index,
                             exit_code=-1, nan_detected=False, user="reap_orphaned_chunks")
            reaped += 1

    if reaped:
        print(f"{_ts()}: reaped {reaped} orphaned chunk(s) (out of {checked} checked). "
              f"Investigate, then 'oceanicu-experiments rerun --experiment-id ... "
              f"--from-current' to redo each.")
    else:
        print(f"{_ts()}: nothing to reap ({checked} running chunk(s) checked, all still "
              f"alive or inconclusive).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
