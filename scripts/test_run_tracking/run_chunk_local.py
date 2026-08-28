#!/usr/bin/env python3
"""Non-SLURM stand-in for run_chunk.slurm -- for testing on a machine
with no SLURM at all (this one). Runs chunks for a registered run_id one
at a time via chunk_runner.py, exactly like production does, then either
loops back for that run's next chunk or, once it completes, picks up the
highest-priority not_started run in the queue -- the same self-chaining
logic as run_chunk.slurm's own bash, just as a plain foreground loop
instead of a SLURM resubmission.

On a real SLURM machine, use the REAL ../run_chunk.slurm instead (via
`sbatch --export=...`) -- see this folder's README.md. This script only
exists because this machine has no sbatch/srun to test against.

Run one of these per "worker slot" you want to simulate -- e.g. launch
two, each starting from a different --run-id, to mimic two concurrent
SLURM allocations both able to pull from the same priority queue once
their own run finishes.

Note on stopping: Ctrl-C kills whatever chunk is currently running
immediately (same as a real `scancel` would) -- it is NOT a graceful
"finish this chunk, then stop" like the DB pause mechanism is. To stop
cleanly between chunks instead, use the real mechanism from another
terminal:
    python ../oceanicu_runs.py pause --run-id <run_id> --db <db>
which this loop's own next iteration will notice and exit on, the same
way run_chunk.slurm's own resubmission check does.

OCEANICU_CHUNK_DELAY_SECONDS (default 0, per user, 2026-08-28): pause
this many seconds right before starting the NEXT chunk of the same run OR
the next queued run -- never while a chunk is actually running, only at
the hand-off between one finishing and the next starting. A throttle, not
a pause/resume substitute (that's a different, existing mechanism -- see
above -- which stops resubmission entirely; this just paces it). Same env
var name and meaning as the real ../run_chunk.slurm's own delay.

Usage
-----
    python run_chunk_local.py --db test_registry.sqlite --run-id fake/long/CNRM-ESM2-1/ssp126
    python run_chunk_local.py --db test_registry.sqlite      # start from the queue itself
    OCEANICU_CHUNK_DELAY_SECONDS=30 python run_chunk_local.py --db test_registry.sqlite --run-id ...
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
sys.path.insert(0, str(SCRIPTS_DIR))
import run_tracking as rt  # noqa: E402

CHUNK_RUNNER = SCRIPTS_DIR / "chunk_runner.py"


def _chunk_delay_seconds() -> float:
    try:
        return float(os.environ.get("OCEANICU_CHUNK_DELAY_SECONDS", "0"))
    except ValueError:
        return 0.0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, help="registry path (local file or ssh://host/path)")
    p.add_argument("--run-id", default=None,
                    help="run to work on first; if omitted, picks the highest-priority "
                         "not_started run from the queue")
    args = p.parse_args()

    run_id = args.run_id
    if run_id is None:
        with rt.connect(args.db) as conn:
            run_id = rt.next_run_to_start(conn)
        if run_id is None:
            print("No --run-id given and the queue is empty (no not_started, unpaused runs).")
            return 1
        print(f"Starting from the queue: {run_id}")

    delay = _chunk_delay_seconds()
    first_job = True
    while True:
        if first_job:
            first_job = False
        elif delay > 0:
            print(f"(waiting {delay:g}s before starting the next chunk -- "
                  f"OCEANICU_CHUNK_DELAY_SECONDS)", flush=True)
            time.sleep(delay)

        print(f"\n=== {run_id}: running next chunk ===", flush=True)
        result = subprocess.run(
            [sys.executable, str(CHUNK_RUNNER), "--db", args.db, "--run-id", run_id],
        )
        rc = result.returncode

        if rc == 2:
            print(f"{run_id}: chunk FAILED. Not resubmitting automatically -- "
                  f"'oceanicu_runs.py rerun --run-id {run_id}' to redo it, or fix and "
                  f"restart this loop once ready.")
            return 2

        if rc == 1:
            # "nothing to do" -- either this run just reached stop_date, or
            # it's paused. Either way this worker is done with THIS run;
            # see which, then try to pick up the queue.
            with rt.connect(args.db) as conn:
                run = rt.get_run(conn, run_id)
                status = run["status"] if run else None
                paused = rt.is_paused(conn, run_id, run["run_root"]) if run else False

            if paused:
                print(f"{run_id}: paused -- stopping this worker. "
                      f"'oceanicu_runs.py resume --run-id {run_id}' then restart this loop "
                      f"to continue.")
                return 0

            print(f"{run_id}: reached stop_date (status={status}).")
            with rt.connect(args.db) as conn:
                next_id = rt.next_run_to_start(conn)
            if next_id is None:
                print("No queued (not_started, unpaused) run waiting -- nothing to pick up. "
                      "Stopping.")
                return 0
            print(f"Picking up next queued run: {next_id}")
            run_id = next_id
            continue

        # rc == 0: chunk finished cleanly -- loop straight back for the next
        # one, same run_id. If that next chunk_runner.py call itself finds
        # the run has just reached stop_date or been paused meanwhile, it
        # returns 1 and the branch above handles it then, not here.
        continue


if __name__ == "__main__":
    sys.exit(main())
