#!/usr/bin/env python
"""chunk_chain.py -- self-chaining, non-SLURM wrapper around chunk_runner.py.

Does what run_chunk.slurm's self-resubmission does, for a host with no job
scheduler at all (a plain workstation, a cloud VM, orca) -- runs chunks of
a tracked experiment back-to-back by subprocess-invoking chunk_runner.py
once per chunk, in a single long-running Python loop rather than
resubmitting a fresh OS process via sbatch each time. run_chunk.slurm
resubmits specifically to get a fresh SLURM walltime allocation per chunk
-- that reason doesn't apply here (no scheduler, no walltime to renew), so
a plain loop in one process is the natural fit, not a stripped-down
compromise.

Each experiment's own `launcher`/`np` (see `oceanicu_experiments.py add
--launcher mpiexec --np N`, or `set-launcher`/`set-np` to change later)
already controls HOW its chunks run -- 'mpiexec' is what a non-SLURM host
needs its queued experiments registered with. This script never touches
that choice; it only replaces run_chunk.slurm's OUTER loop (the
SLURM-specific parts: #SBATCH, sbatch self-resubmission, $SLURM_* vars)
with a plain Python one, mirroring its exact decision logic (proven,
copied faithfully, not reinvented):

    chunk exit 0 (success)        -> check paused, else loop (same experiment)
    chunk exit 1 (nothing to do)  -> complete: pick up next queued experiment,
                                      or stop if none queued
                                      paused: stop (resume by hand)
    chunk exit 2 (chunk failed)   -> stop; do not auto-retry (see
                                      `oceanicu_experiments.py rerun`)

Explicitly NOT replicated: run_chunk.slurm's NaN-monitoring log-tail.
chunk_runner.py's own finish_chunk() call passes nan_detected=False
unconditionally today -- it's not a real, working check yet in the
reference implementation either, so there is nothing functioning to
mirror here; building one would be inventing a capability the source this
is based on doesn't actually have, not matching it. Likewise the optional
health-check watchdog (OCEANICU_HEALTH_CHECK) is skipped, for the same
reason it defaults off in run_chunk.slurm: sstat job accounting was found
unreliable on the real cluster (see that script's own comment) -- on hold
pending a real signal, nothing here to copy yet.

Usage:
    python3 chunk_chain.py --experiment-id NSe/CMEMS/v01E [--db PATH]
    # or in the background:
    nohup python3 chunk_chain.py --experiment-id ... > chunk_chain.log 2>&1 &

--db / OCEANICU_EXPERIMENT_DB: same resolution as chunk_runner.py/
experiment_tracking.py -- no hardcoded default (see connect()'s own
docstring for why).

Stops cleanly on SIGINT/SIGTERM -- checked once per loop iteration
(between chunks, never mid-chunk; the running chunk_runner.py subprocess
always finishes or is left for a human to investigate, never killed
out from under it).
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent

_shutdown_requested = False


def _handle_shutdown_signal(signum, _frame) -> None:
    global _shutdown_requested
    _shutdown_requested = True
    print(f"\nchunk_chain: received signal {signum} -- will stop after the "
          f"current chunk finishes (not mid-chunk).", file=sys.stderr)


def run_chunk_once(experiment_id: str, db: Optional[str], python: str = sys.executable) -> int:
    """Subprocess-invoke chunk_runner.py for exactly one chunk.

    Same invocation shape run_chunk.slurm already uses
    (`python chunk_runner.py --experiment-id ...`), minus --slurm-job-id
    (nothing to pass -- there's no SLURM job here). Returns chunk_runner.
    py's own exit code (0 success / 1 nothing-to-do / 2 chunk failed).
    """
    cmd = [python, str(SCRIPT_DIR / "chunk_runner.py"), "--experiment-id", experiment_id]
    if db:
        cmd += ["--db", db]
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    return result.returncode


def chain(experiment_id: str, db: Optional[str], *, rt=None, run_chunk=run_chunk_once) -> int:
    """Run *experiment_id* to completion (or until paused/failed/stopped),
    then whatever's next in the queue, exactly like run_chunk.slurm's own
    self-resubmission chain. Returns a process exit code: 0 = stopped
    cleanly (ran out of queued work, got paused, or a shutdown signal was
    received), 2 = a chunk failed.

    *rt* / *run_chunk* are injectable (default: the real
    experiment_tracking module / run_chunk_once above) purely so this
    function's branching logic can be unit-tested with fakes, without a
    real SQLite registry or a real chunk_runner.py subprocess -- see
    test_chunk_chain.py.
    """
    if rt is None:
        import experiment_tracking as rt  # type: ignore[no-redef]

    current = experiment_id
    while not _shutdown_requested:
        with rt.connect(db) as conn:
            _wait_for_delay_all(rt, conn)
            _wait_for_experiment_chunk_delay(rt, conn, current)
        if _shutdown_requested:
            print("chunk_chain: stopping before starting another chunk (signal received).")
            return 0

        print(f"chunk_chain: {current}: submitting next chunk")
        exit_code = run_chunk(current, db)

        if exit_code not in (0, 1):
            print(f"chunk_chain: {current}: chunk failed (exit {exit_code}) -- "
                  f"not resubmitting automatically.", file=sys.stderr)
            print(f"Investigate, then either "
                  f"'oceanicu_experiments.py rerun --experiment-id {current}' to redo it, "
                  f"or re-run this script once fixed.", file=sys.stderr)
            return 2

        with rt.connect(db) as conn:
            if exit_code == 1:
                experiment = rt.get_experiment(conn, current)
                status = experiment["status"] if experiment else None
                if status in ("complete", "complete_with_warnings"):
                    print(f"chunk_chain: {current}: reached stop_date (status={status}).")
                    next_id = rt.next_experiment_to_start(conn)
                    if not next_id:
                        print("No queued (not_started, unpaused) experiment waiting -- "
                              "nothing to pick up. Stopping.")
                        return 0
                    print(f"chunk_chain: picking up next queued experiment: {next_id}")
                    current = next_id
                    continue
                # Not complete but nothing to do -> paused.
                print(f"chunk_chain: {current}: paused -- not resubmitting. "
                      f"'oceanicu_experiments.py resume --experiment-id {current}' to continue.")
                return 0

            # exit_code == 0: chunk succeeded -- check paused before looping
            # on the SAME experiment (mirrors run_chunk.slurm's own check,
            # which runs after every successful chunk, not just at
            # completion).
            experiment = rt.get_experiment(conn, current)
            experiment_root = experiment["experiment_root"] if experiment else None
            if rt.is_paused(conn, current, experiment_root):
                print(f"chunk_chain: {current}: paused -- not resubmitting. "
                      f"'oceanicu_experiments.py resume --experiment-id {current}' to continue.")
                return 0
        # loop continues with the same experiment (current unchanged)

    print("chunk_chain: stopping (signal received).")
    return 0


def _wait_for_delay_all(rt, conn) -> None:
    """Global, one-shot pacing (oceanicu_experiments.py delay-all) --
    mirrors run_chunk.slurm's own _wait_for_delay_all bash function:
    polls in short bursts (capped at 60s) so a delay that's shortened,
    extended, or cleared while already waiting takes effect right away.
    No-op when nothing is set.
    """
    while True:
        remaining = rt.get_chunk_delay_remaining(conn)
        if remaining <= 0:
            return
        poll = min(remaining, 60)
        print(f"  DELAY_ALL active: {remaining:.0f}s remaining -- "
              f"waiting (re-checking in {poll:.0f}s)...")
        if _wait_or_shutdown(poll):
            return


def _wait_for_experiment_chunk_delay(rt, conn, experiment_id: str) -> None:
    """Persistent, per-experiment pacing (oceanicu_experiments.py
    set-chunk-delay / the experiment's own chunk_delay_seconds column) --
    mirrors run_chunk.slurm's own _wait_for_experiment_chunk_delay.
    """
    experiment = rt.get_experiment(conn, experiment_id)
    delay = (experiment["chunk_delay_seconds"] if experiment else 0) or 0
    if delay > 0:
        print(f"  {experiment_id}: chunk_delay_seconds={delay} -- waiting before submitting...")
        _wait_or_shutdown(delay)


def _wait_or_shutdown(seconds: float) -> bool:
    """Sleep up to *seconds*, waking early if a shutdown signal arrives.
    Returns True if a shutdown was requested during the wait."""
    import time
    elapsed = 0.0
    step = 1.0
    while elapsed < seconds:
        if _shutdown_requested:
            return True
        time.sleep(min(step, seconds - elapsed))
        elapsed += step
    return _shutdown_requested


def main() -> int:
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--experiment-id", required=True,
                   help="Starting experiment -- same registry ID chunk_runner.py/"
                        "oceanicu_experiments.py use")
    p.add_argument("--db", default=None,
                   help="SQLite registry path (default: $OCEANICU_EXPERIMENT_DB)")
    args = p.parse_args()

    return chain(args.experiment_id, args.db)


if __name__ == "__main__":
    sys.exit(main())
