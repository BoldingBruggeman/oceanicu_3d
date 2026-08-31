#!/usr/bin/env python3
"""Create a scratch experiment-tracking registry and register a handful of FAKE
simulations against it, for interactively testing oceanicu_experiments.py's
command-line options (list/show/pause/resume/rerun/set-priority/
set-stop-date/chunk-size/remove/--dry-run) without touching the real
production registry and without running any real pyGETM simulation.

Two ways to actually execute the fake chunks afterwards -- see this
folder's README.md for the full walkthrough of both:

  - No SLURM (e.g. this machine): --launcher mpiexec (the default here),
    driven by this folder's own run_chunk_local.py, which calls
    chunk_runner.py directly in a loop instead of sbatch-ing
    run_chunk.slurm.
  - Real SLURM machine: --launcher srun (the real production default),
    driven by the REAL ../run_chunk.slurm via `sbatch --export=...` --
    same registry, same fake_driver.py as --script, no new script needed
    for that path.

**Remember the DB itself should live on the relay**, not on whichever
machine happens to run this setup script, if the machine(s) actually
executing chunks (in particular a separate SLURM cluster) can't reach
this one directly -- see EXPERIMENT_TRACKING.md's "Working across machines".
Pass --db explicitly for that, e.g.:
    --db ssh://oceanicu-relay/abs/path/to/test_registry.sqlite
    --db ssh://bb-server1/data/OceanICU/oceanicu_3d/experiments/test_experiment_tracking/test_registry.sqlite
experiment_tracking.connect() already handles ssh:// specs transparently
(RemoteConn) -- this script needs no special-casing for that itself.

Everything this creates ON THIS machine (experiment_root dirs under experiments/, the
local test_registry.sqlite if --db is left at its default) lives under
THIS folder -- nothing outside it, and the real production registry is
never opened.

Usage
-----
    python setup_fake_experiments.py                      # local DB, mpiexec (no SLURM here)
    python setup_fake_experiments.py --reset               # wipe and recreate from scratch
    python setup_fake_experiments.py --launcher srun \\
        --db ssh://oceanicu-relay/abs/path/test_registry.sqlite
                                                     # for a real SLURM machine + relay DB

Then, from this same folder:
    export OCEANICU_EXPERIMENT_DB=<same --db value as above>
    python ../oceanicu_experiments.py list
    python run_chunk_local.py --db "$OCEANICU_EXPERIMENT_DB" --experiment-id fake/quick/run01   # no-SLURM path
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
sys.path.insert(0, str(SCRIPTS_DIR))
import experiment_tracking as rt  # noqa: E402

DEFAULT_DB_PATH = HERE / "test_registry.sqlite"
EXPERIMENTS_DIR = HERE / "experiments"
FAKE_DRIVER = HERE / "fake_driver.py"

# (experiment_id, chunk_kind, chunk_multiplier, initial_date, stop_date, priority, notes)
FAKE_EXPERIMENTS = [
    ("fake/quick/run01", "daily", 1, "2015-01-01", "2015-01-02", 10,
     "Single ~1-day chunk -- reaches 'complete' after just one fake chunk; "
     "good for watching the queue pick up the next not_started experiment."),
    ("fake/quick/run02", "daily", 1, "2015-01-01", "2015-01-02", 5,
     "Same shape as run01, lower priority -- demonstrates priority "
     "ordering in the not_started queue."),
    ("fake/long/CNRM-ESM2-1/ssp126", "annual", 5, "2015-01-01", "2100-01-01", 0,
     "Many 5-year chunks -- stays in_progress for a long time; good for "
     "pause/resume/set-stop-date/chunk-size/rerun testing."),
    ("fake/long/GFDL-ESM4/ssp370", "monthly", 6, "2015-01-01", "2100-01-01", 0,
     "Many 6-month chunks -- different chunk-kind from the experiment above."),
    ("fake/notstarted/spare", "annual", 10, "2015-01-01", "2050-01-01", -5,
     "Registered but won't start in this session unless you launch a "
     "second run_chunk_local.py worker or point one at it directly -- "
     "lowest priority, good for 'list --status not_started' and 'remove'."),
]


def _is_remote(db: str) -> bool:
    return db.startswith("ssh://")


def _running_chunks(db: str) -> list[str]:
    """experiment_ids with a chunk currently marked 'running' in *db* -- i.e. some
    worker (yours, or someone else's in another terminal -- this folder's
    own README explicitly invites starting one there) is plausibly mid-
    sleep against files --reset is about to delete out from under it.
    Real incident, 2026-08-28: a --reset while an old run_chunk_local.py
    worker was still asleep on a chunk wiped its chunk_dir before it woke
    up to write its restart file -- FileNotFoundError, not a bug in
    fake_driver.py, just an unguarded rm of live state. Best-effort: a
    stale 'running' row nobody's actually touching any more (a worker
    that itself died without calling finish_chunk) will also block a
    plain --reset -- use --force to override once you've actually
    confirmed nothing is running via `ps`/pgrep yourself."""
    if not Path(db).exists():
        return []
    running = []
    with rt.connect(db) as conn:
        for experiment in rt.list_experiments(conn):
            if rt.get_running_chunk(conn, experiment["experiment_id"]) is not None:
                running.append(experiment["experiment_id"])
    return running


def reset(db: str, force: bool = False) -> None:
    if not _is_remote(db) and not force:
        running = _running_chunks(db)
        if running:
            print(f"Refusing to reset -- these experiment(s) have a chunk currently marked "
                  f"'running' (a worker may still be asleep against files this would "
                  f"delete): {', '.join(running)}")
            print("Stop that worker first (Ctrl-C, or let it finish), or pass --force "
                  "if you've confirmed via `ps`/pgrep that nothing is actually running.")
            raise SystemExit(1)

    if _is_remote(db):
        print(f"{db} is remote (ssh://) -- not deleting anything there automatically. "
              f"Remove the file on that host yourself if you really want a clean slate "
              f"(it's just a SQLite file + its -wal/-shm siblings).")
    else:
        for suffix in ("", "-wal", "-shm"):
            Path(db + suffix).unlink(missing_ok=True)
        print(f"Removed {db} (+ -wal/-shm)")
    if EXPERIMENTS_DIR.exists():
        shutil.rmtree(EXPERIMENTS_DIR)
        print(f"Removed {EXPERIMENTS_DIR}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DEFAULT_DB_PATH),
                    help="registry path -- a local file (default: test_registry.sqlite in this "
                         "folder) or ssh://host/abs/path for a relay-hosted registry (see this "
                         "script's own docstring on 'Remember the DB should live on the relay')")
    p.add_argument("--launcher", default="mpiexec", choices=["mpiexec", "srun"],
                    help="mpiexec (default): no SLURM needed, driven by this folder's own "
                         "run_chunk_local.py. srun: real production default, for a real SLURM "
                         "machine driven by the real ../run_chunk.slurm instead.")
    p.add_argument("--reset", action="store_true", help="wipe any existing test DB/experiment dirs first")
    p.add_argument("--force", action="store_true",
                    help="with --reset: wipe even if a chunk is currently marked 'running' "
                         "(only after confirming yourself, e.g. via `ps`/pgrep, that nothing "
                         "is actually mid-chunk against these files -- see reset()'s own "
                         "docstring for the real incident this guards against)")
    p.add_argument("--fake-driver-path", default=str(FAKE_DRIVER),
                    help="path to fake_driver.py as seen by the machine that will EXECUTE "
                         "chunks -- only needs overriding if that's a different machine than "
                         "the one running this setup script (e.g. a remote SLURM cluster), in "
                         "which case copy fake_driver.py there first and point this at it.")
    args = p.parse_args()

    if args.reset:
        reset(args.db, force=args.force)

    if not _is_remote(args.db) and Path(args.db).exists():
        print(f"{args.db} already exists -- refusing to add duplicate rows. "
              f"Pass --reset to start over.")
        return 1

    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)

    with rt.connect(args.db) as conn:
        for experiment_id, chunk_kind, chunk_multiplier, initial_date, stop_date, priority, notes in FAKE_EXPERIMENTS:
            experiment_root = EXPERIMENTS_DIR / experiment_id.replace("/", "_")
            experiment_root.mkdir(parents=True, exist_ok=True)
            config_path = experiment_root / "fake_config.yaml"
            config_path.write_text(f"# fake config for {experiment_id}\nname: {experiment_id}\n")

            rt.add_experiment(
                conn,
                experiment_id=experiment_id,
                experiment_root=str(experiment_root),
                script=args.fake_driver_path,
                config=str(config_path),
                initial_date=initial_date,
                stop_date=stop_date,
                chunk_kind=chunk_kind,
                chunk_multiplier=chunk_multiplier,
                np=1,
                launcher=args.launcher,
                priority=priority,
                notes=notes,
            )
            print(f"added {experiment_id!r} (priority={priority}, {chunk_multiplier} {chunk_kind} "
                  f"chunk(s) per step, {initial_date} -> {stop_date})")

    print()
    print(f"Registry created at {args.db}")
    print("Point oceanicu_experiments.py at it with:")
    print(f"  export OCEANICU_EXPERIMENT_DB={args.db}")
    print(f"  python {SCRIPTS_DIR / 'oceanicu_experiments.py'} list")
    print()
    if args.launcher == "mpiexec":
        print("Nothing is actually executing yet -- start a local worker loop to make")
        print("chunks progress in real time (see README.md for the full walkthrough):")
        print(f"  python {HERE / 'run_chunk_local.py'} --db {args.db} --experiment-id fake/quick/run01")
    else:
        print("Nothing is actually executing yet -- launch the REAL run_chunk.slurm against")
        print("one of these fake experiments (see README.md for the full walkthrough):")
        print(f"  sbatch --export=EXPERIMENT_ID='fake/quick/run01',OCEANICU_EXPERIMENT_DB='{args.db}' "
              f"{SCRIPTS_DIR / 'run_chunk.slurm'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
