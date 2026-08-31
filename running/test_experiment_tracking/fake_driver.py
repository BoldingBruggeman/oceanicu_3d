#!/usr/bin/env python3
"""Stand-in for a pygetm_config.codegen --dump-python driver script, for
testing oceanicu_experiments.py / chunk_runner.py's tracking machinery without a
real pyGETM simulation. Accepts the exact CLI surface chunk_runner.py
builds for a real driver (--start/--stop/--save-restart/--load-restart/
--data-roots-file/--fabm/--no-fabm/--dry-run) but instead of simulating
anything, sleeps for a while (default: a random 5-10 minutes, long enough
to interact with `oceanicu_experiments.py list/show/pause/...` while a chunk is
"running") then writes a placeholder restart file and exits 0.

Failure testing: if a `FAIL_NEXT_CHUNK` file exists in this chunk's own
experiment_root (the directory directly above the chunk's own), it's
deleted and this chunk exits 1 instead of succeeding -- lets you test
`rerun` and `list --status failed` on demand, without waiting for a real
failure:

    touch <experiment_root>/FAIL_NEXT_CHUNK

Speed override for developing/debugging the test harness itself (real
interactive testing should leave this unset, for the true 5-10 min feel):

    FAKE_CHUNK_SECONDS=5 python fake_driver.py --start ... --stop ... --save-restart ...
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
import time
from pathlib import Path

# chunk_runner.py names each chunk directory NNN_<start>_<stop> (e.g.
# 000_20200101_20200102), directly under experiment_root -- no "chunks/"
# parent anymore. Matching this pattern is how we tell a real tracked
# invocation (cwd = a genuine chunk dir, one level below experiment_root)
# apart from a standalone/ad-hoc one (cwd = anything else, treated as
# experiment_root itself for the sentinel check below).
_CHUNK_DIR_RE = re.compile(r"^\d{3}_\d{8}_\d{8}$")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True)
    p.add_argument("--stop", required=True)
    p.add_argument("--save-restart", required=True)
    p.add_argument("--load-restart", default=None)
    p.add_argument("--data-roots-file", default=None)
    p.add_argument("--fabm", nargs="?", const="on", default=None, metavar="PATH")
    p.add_argument("--no-fabm", dest="fabm", action="store_const", const="off")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    print(f"[fake_driver] {args.start} -> {args.stop}", flush=True)
    if args.load_restart:
        print(f"[fake_driver] loading restart: {args.load_restart}", flush=True)
    if args.fabm:
        print(f"[fake_driver] fabm={args.fabm}", flush=True)

    if args.dry_run:
        print("[fake_driver] --dry-run: not sleeping, not writing a restart file.", flush=True)
        return 0

    # chunk_runner.py always invokes the driver with cwd=chunk_dir, and
    # chunk_dir is always experiment_root/<NNN_start_stop> in tracked mode --
    # so experiment_root is one level up from here, when that shape is
    # present (detected via _CHUNK_DIR_RE, not a fixed parent name).
    # Standalone/ad-hoc invocations (cwd doesn't look like a real chunk
    # dir) just treat the cwd itself as experiment_root for the sentinel
    # check.
    chunk_dir = Path.cwd()
    experiment_root = chunk_dir.parent if _CHUNK_DIR_RE.match(chunk_dir.name) else chunk_dir

    fail_sentinel = experiment_root / "FAIL_NEXT_CHUNK"
    if fail_sentinel.exists():
        fail_sentinel.unlink()
        print("[fake_driver] FAIL_NEXT_CHUNK sentinel found -- simulating a failed chunk.",
              file=sys.stderr)
        return 1

    seconds = float(os.environ.get("FAKE_CHUNK_SECONDS", random.uniform(300, 600)))
    print(f"[fake_driver] sleeping {seconds:.0f}s to simulate a real chunk...", flush=True)
    time.sleep(seconds)

    Path(args.save_restart).write_text("fake restart file -- not a real NetCDF, just a placeholder\n")
    print(f"[fake_driver] wrote {args.save_restart}", flush=True)
    print("[fake_driver] done.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
