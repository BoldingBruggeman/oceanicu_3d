#!/usr/bin/env python
"""health_check.py -- periodic CPU-stall watchdog for a chunk actually
running RIGHT NOW on SLURM. Invoked repeatedly (every ~10 min, see
run_chunk.slurm's own background loop) while chunk_runner.py's srun is
still alive.

**ON HOLD as of 2026-09-02, OFF by default** (OCEANICU_HEALTH_CHECK=1
in run_chunk.slurm to enable anyway) -- confirmed against the REAL
cluster that sstat's job accounting is unreliable here: TotalCPU isn't
even a valid --format field, and AveCPU (which is) returns garbage
(e.g. "213503982334-14:25:51") for every step of a real, healthy
192-task job, repeatably. Nothing was ever wrongly killed by this --
a failed/garbage sstat read always skips the cycle, never counts as a
stall -- but every cycle silently no-opped instead of monitoring
anything. See EXPERIMENT_TRACKING.md's "Monitoring a running chunk"
for the full story and what a replacement signal needs to account for
(log-growth was proposed, but has its own unresolved false-positive
risk -- legitimate report-line gaps vary a lot by domain/report.days,
plus scyllapfs write-visibility lag). The structure below (looks up
the running chunk itself, fail-safe on any bad reading, overwrite-not-
history-spam heartbeat) is still the right shape to build on -- only
_get_total_cpu_seconds's actual signal needs replacing.

Motivation: the old approach (tail the GETM log for a literal "nan"
string, run_chunk.slurm's OCEANICU_NAN_CHECK) only ever proves ONE
specific failure mode -- a numerical blow-up that actually prints "nan"
somewhere -- and needed a real production fix for a fork-per-line
slowdown along the way. It also told us nothing about the other common
failure mode this was actually meant to catch: an MPI job that's
silently HUNG (one rank diverged/crashed internally on a NaN, the
others are stuck forever in a blocking collective waiting for it) --
SLURM itself doesn't care, the job just sits there consuming its
walltime allocation for nothing, and the registry DB never learns
anything is wrong until the walltime itself finally expires. Replaced
entirely (2026-09-02, per user) rather than kept alongside.

This instead watches the job's own CUMULATIVE CPU time via `sstat`
(SLURM's own live-job-stats tool -- no log access needed at all): if it
hasn't increased across TWO consecutive checks (a real, sustained
stall, not just one slow interval doing something legitimately
CPU-light like a big I/O flush), the job is genuinely not making
progress and gets scancelled. A single non-increasing reading is only
ever logged as a warning, not acted on -- avoids killing a perfectly
healthy run over one slow interval.

Known limitation, flagged rather than hidden: not every real MPI hang
shows up as near-zero CPU growth -- some MPI implementations/
collectives busy-poll while "waiting" (spinning at ~100% CPU doing no
useful work), which this can't distinguish from genuine progress. It
catches the blocking-wait style of hang, not every conceivable one.

Every check -- healthy or warning -- OVERWRITES chunks.last_health_check
(experiment_tracking.update_chunk_health_check) rather than adding a new
history row each time (a multi-day chunk checked every ~10 min would
otherwise flood history with hundreds of routine entries; only the
LATEST status matters day-to-day). A genuine state change -- the
watchdog actually killing a stalled job -- DOES get a real, permanent
history entry (experiment_tracking.record_health_check), on top of
finish_chunk's own chunk_failed/run_failed. Either way, the registry DB
directly reflects whether a run is still alive and making progress
("the DB do not reflect if e.g. there has been a blow up", user,
2026-09-02) without anyone needing to check squeue or tail a log by
hand.

Looks up the CURRENTLY RUNNING chunk for --experiment-id itself (the
same rt.get_running_chunk primitive `oceanicu-experiments kill` already
uses) rather than being handed chunk_index/slurm_job_id/chunk_dir by
the caller -- always matches the DB's own source of truth, and keeps
run_chunk.slurm's own invocation trivial (just --experiment-id, called
on a timer). Per-chunk stall state (previous TotalCPU reading, how many
consecutive non-increasing checks so far) persists in a small JSON file
inside the chunk's own directory -- survives fine across repeated
invocations of this script for the same chunk, cleaned up once the
chunk finishes (successfully or via this script's own kill).

Usage (called in a loop, not by a human):
    OCEANICU_EXPERIMENT_DB=... python health_check.py --experiment-id ...

Run this ONLY on a machine that's actually part of the SLURM cluster
(needs sstat/scancel) -- same constraint as reap_orphaned_chunks.py /
oceanicu-experiments kill, never on the relay.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import experiment_tracking as rt  # noqa: E402

STATE_FILENAME = ".health_check_state.json"
STALL_LIMIT = 2  # consecutive non-increasing TotalCPU readings before killing


def _ts() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_slurm_duration(text: str) -> "int | None":
    """Parse sstat's own duration format into whole seconds. Handles
    'D-HH:MM:SS', 'HH:MM:SS', 'MM:SS', and a bare integer (seconds) --
    sstat's exact format varies a bit by SLURM version/config. Returns
    None (never raises) on anything unrecognised, so a formatting
    surprise skips this cycle rather than ever being misread as a
    stall (a False stall reading is the dangerous direction -- it can
    kill a healthy job -- so any parse doubt defaults to doing
    nothing)."""
    text = text.strip()
    if not text:
        return None
    # Some sstat configurations append fractional seconds (e.g.
    # "00:01:23.456") -- truncate to whole seconds, not a parse failure.
    if "." in text:
        text = text.split(".", 1)[0]
    days = 0
    if "-" in text:
        day_part, _, text = text.partition("-")
        if not day_part.isdigit():
            return None
        days = int(day_part)
    parts = text.split(":")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    if len(nums) == 3:
        hours, minutes, seconds = nums
    elif len(nums) == 2:
        hours = 0
        minutes, seconds = nums
    elif len(nums) == 1:
        hours = minutes = 0
        seconds = nums[0]
    else:
        return None
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _get_total_cpu_seconds(slurm_job_id: str) -> "int | None":
    try:
        result = subprocess.run(
            ["sstat", "-j", str(slurm_job_id), "--format=TotalCPU", "--noheader", "--parsable2"],
            capture_output=True, text=True, timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"{_ts()}: health-check: sstat unavailable/timed out ({exc}) -- skipping this cycle.")
        return None
    if result.returncode != 0 or not result.stdout.strip():
        print(f"{_ts()}: health-check: sstat gave no usable output for job {slurm_job_id} "
              f"(rc={result.returncode}) -- skipping this cycle.")
        return None
    # One line per job step (main step + often an "extern" step) -- the
    # first real line is the main step's own aggregate TotalCPU.
    line = result.stdout.strip().splitlines()[0]
    seconds = _parse_slurm_duration(line)
    if seconds is None:
        print(f"{_ts()}: health-check: couldn't parse sstat TotalCPU output {line!r} -- skipping this cycle.")
    return seconds


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--db", default=None, help="falls back to OCEANICU_EXPERIMENT_DB if omitted")
    args = p.parse_args()

    with rt.connect(args.db) as conn:
        running = rt.get_running_chunk(conn, args.experiment_id)
        if running is None:
            print(f"{_ts()}: health-check: no chunk currently marked running for "
                  f"{args.experiment_id!r} -- nothing to check.")
            return 0

        chunk_index = running["chunk_index"]
        slurm_job_id = running["slurm_job_id"]
        chunk_dir = running["chunk_dir"]

        if not slurm_job_id:
            print(f"{_ts()}: health-check: chunk {chunk_index} of {args.experiment_id!r} has no "
                  f"recorded slurm_job_id -- skipping.")
            return 0

        cpu_seconds = _get_total_cpu_seconds(slurm_job_id)
        if cpu_seconds is None:
            return 0

        state_path = Path(chunk_dir) / STATE_FILENAME if chunk_dir else None
        prev_cpu_seconds = None
        stall_count = 0
        if state_path is not None and state_path.is_file():
            try:
                state = json.loads(state_path.read_text())
                prev_cpu_seconds = state.get("prev_cpu_seconds")
                stall_count = int(state.get("stall_count", 0))
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass  # corrupt/unreadable state -- treat as first-ever check, harmless

        if prev_cpu_seconds is not None and cpu_seconds <= prev_cpu_seconds:
            stall_count += 1
            if stall_count >= STALL_LIMIT:
                detail = (f"TotalCPU stalled at {cpu_seconds}s across {stall_count} consecutive "
                          f"checks (job {slurm_job_id}) -- scancelling")
                print(f"{_ts()}: health-check: {detail}")
                rt.record_health_check(conn, args.experiment_id, chunk_index, detail, user="health_check")
                ok, msg = rt.cancel_slurm_job(slurm_job_id)
                print(f"{_ts()}: health-check: scancel {slurm_job_id}: {'ok' if ok else 'FAILED'} ({msg})")
                rt.finish_chunk(conn, experiment_id=args.experiment_id, chunk_index=chunk_index,
                                 exit_code=-1, nan_detected=False, user="health_check")
                if state_path is not None:
                    state_path.unlink(missing_ok=True)
                return 0
            detail = (f"checked on {_ts()} -- WARNING: TotalCPU unchanged at {cpu_seconds}s "
                      f"(stall {stall_count}/{STALL_LIMIT}), job {slurm_job_id}")
        else:
            stall_count = 0
            detail = f"checked on {_ts()} -- TotalCPU={cpu_seconds}s, job {slurm_job_id} OK"

        print(f"{_ts()}: health-check: {detail}")
        # Overwrite, not a new history row every cycle -- see
        # update_chunk_health_check's own docstring for why (a multi-day
        # chunk checked every ~10 min would otherwise flood history with
        # hundreds of routine entries; only the LATEST status matters).
        rt.update_chunk_health_check(conn, args.experiment_id, chunk_index, detail)

        if state_path is not None:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"prev_cpu_seconds": cpu_seconds, "stall_count": stall_count}))

    return 0


if __name__ == "__main__":
    sys.exit(main())
