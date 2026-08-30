#!/usr/bin/env python3
"""Non-SLURM stand-in for run_chunk.slurm -- for testing on a machine
with no SLURM at all (this one). Runs chunks for a registered experiment_id one
at a time via chunk_runner.py, exactly like production does, then either
loops back for that experiment's next chunk or, once it completes, picks up the
highest-priority not_started experiment in the queue -- the same self-chaining
logic as run_chunk.slurm's own bash, just as a plain foreground loop
instead of a SLURM resubmission.

On a real SLURM machine, use the REAL ../run_chunk.slurm instead (via
`sbatch --export=...`) -- see this folder's README.md. This script only
exists because this machine has no sbatch/srun to test against.

Run one of these per "worker slot" you want to simulate -- e.g. launch
two, each starting from a different --experiment-id, to mimic two concurrent
SLURM allocations both able to pull from the same priority queue once
their own experiment finishes.

Note on stopping: Ctrl-C kills whatever chunk is currently running
immediately (same as a real `scancel` would) -- it is NOT a graceful
"finish this chunk, then stop" like the DB pause mechanism is. To stop
cleanly between chunks instead, use the real mechanism from another
terminal:
    python ../oceanicu_experiments.py pause --experiment-id <experiment_id> --db <db>
which this loop's own next iteration will notice and exit on, the same
way run_chunk.slurm's own resubmission check does.

`oceanicu_experiments.py delay-all --seconds N` (per user, 2026-08-28: "the HPC
must be used for something else for a while") is honored here too, the
same live-checked DELAY_ALL sentinel run_chunk.slurm uses -- checked
fresh right before starting the NEXT chunk of the same experiment OR the next
queued experiment, never while a chunk is actually running, in short polls so a
delay changed WHILE this is already waiting takes effect immediately
rather than only on the next hand-off. Not a pause/resume substitute
(that's the different, existing mechanism above, which stops resubmission
indefinitely until a human resumes); this waits out the remainder then
proceeds automatically. From another terminal, while this loop is
running:
    python ../oceanicu_experiments.py delay-all --db <db> --seconds 300
    python ../oceanicu_experiments.py delay-all --db <db> --clear

Usage
-----
    python run_chunk_local.py --db test_registry.sqlite --experiment-id fake/long/CNRM-ESM2-1/ssp126
    python run_chunk_local.py --db test_registry.sqlite      # start from the queue itself
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
sys.path.insert(0, str(SCRIPTS_DIR))
import experiment_tracking as rt  # noqa: E402

CHUNK_RUNNER = SCRIPTS_DIR / "chunk_runner.py"


def _wait_for_delay_all(db: str) -> None:
    """Mirrors run_chunk.slurm's own _wait_for_delay_all bash function --
    poll rather than one fixed sleep, so a delay that's shortened,
    extended, or cleared while already waiting takes effect right away."""
    while True:
        with rt.connect(db) as conn:
            remaining = rt.get_chunk_delay_remaining(conn)
        if remaining <= 0:
            return
        poll = min(remaining, 60)
        print(f"  DELAY_ALL active: {remaining:.0f}s remaining -- waiting "
              f"(re-checking in {poll:.0f}s)...", flush=True)
        time.sleep(poll)


def _wait_for_experiment_chunk_delay(db: str, experiment_id: str) -> None:
    """Persistent, per-experiment pacing (oceanicu_experiments.py set-chunk-delay / the
    experiment's own chunk_delay_seconds column, default 0) -- mirrors
    run_chunk.slurm's own _wait_for_experiment_chunk_delay bash function.
    Different from DELAY_ALL: an ongoing setting for ONE specific experiment,
    not a global one-shot timed pause."""
    with rt.connect(db) as conn:
        experiment = rt.get_experiment(conn, experiment_id)
    delay = (experiment["chunk_delay_seconds"] if experiment else 0) or 0
    if delay > 0:
        print(f"  {experiment_id}: chunk_delay_seconds={delay} -- waiting before starting...", flush=True)
        time.sleep(delay)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, help="registry path (local file or ssh://host/path)")
    p.add_argument("--experiment-id", default=None,
                    help="experiment to work on first; if omitted, picks the highest-priority "
                         "not_started experiment from the queue")
    args = p.parse_args()

    experiment_id = args.experiment_id
    if experiment_id is None:
        with rt.connect(args.db) as conn:
            experiment_id = rt.next_experiment_to_start(conn)
        if experiment_id is None:
            print("No --experiment-id given and the queue is empty (no not_started, unpaused experiments).")
            return 1
        print(f"Starting from the queue: {experiment_id}")

    first_job = True
    while True:
        if first_job:
            first_job = False
        else:
            _wait_for_delay_all(args.db)
            _wait_for_experiment_chunk_delay(args.db, experiment_id)

        print(f"\n=== {experiment_id}: running next chunk ===", flush=True)
        result = subprocess.run(
            [sys.executable, str(CHUNK_RUNNER), "--db", args.db, "--experiment-id", experiment_id],
        )
        rc = result.returncode

        if rc == 2:
            print(f"{experiment_id}: chunk FAILED. Not resubmitting automatically -- "
                  f"'oceanicu_experiments.py rerun --experiment-id {experiment_id}' to redo it, or fix and "
                  f"restart this loop once ready.")
            return 2

        if rc == 1:
            # "nothing to do" -- either this experiment just reached stop_date, or
            # it's paused. Either way this worker is done with THIS experiment;
            # see which, then try to pick up the queue.
            with rt.connect(args.db) as conn:
                experiment = rt.get_experiment(conn, experiment_id)
                status = experiment["status"] if experiment else None
                paused = rt.is_paused(conn, experiment_id, experiment["experiment_root"]) if experiment else False

            if paused:
                print(f"{experiment_id}: paused -- stopping this worker. "
                      f"'oceanicu_experiments.py resume --experiment-id {experiment_id}' then restart this loop "
                      f"to continue.")
                return 0

            print(f"{experiment_id}: reached stop_date (status={status}).")
            with rt.connect(args.db) as conn:
                next_id = rt.next_experiment_to_start(conn)
            if next_id is None:
                print("No queued (not_started, unpaused) experiment waiting -- nothing to pick up. "
                      "Stopping.")
                return 0
            print(f"Picking up next queued experiment: {next_id}")
            experiment_id = next_id
            continue

        # rc == 0: chunk finished cleanly -- loop straight back for the next
        # one, same experiment_id. If that next chunk_runner.py call itself finds
        # the experiment has just reached stop_date or been paused meanwhile, it
        # returns 1 and the branch above handles it then, not here.
        continue


if __name__ == "__main__":
    sys.exit(main())
