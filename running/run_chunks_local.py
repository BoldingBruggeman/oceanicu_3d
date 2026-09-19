#!/usr/bin/env python
"""Loop over chunks of a simulation using the SAME chunk_runner.py/
oceanicu_experiments.py the real HPC uses, but driven by a plain Python
loop instead of SLURM self-resubmission -- for test runs on bb-server1
(or any other non-SLURM machine).

Run this FROM the experiment's own directory (the one holding its
generated_*.py / generated_*_config.yaml pair) -- experiment-id and
experiment-root are derived from that directory (relative to
$OCEANICU_EXPERIMENT_ROOT_BASE), not passed on the command line.

Usage:
    cd $OCEANICU_EXPERIMENT_ROOT_BASE/NSe/CMIP6_raw/run01/3
    run-chunks-local --start 2014-01-01 --stop 2016-01-01
    run-chunks-local --start 2014-01-01 --stop 2016-01-01 --chunk-kind monthly --chunk-multiplier 3 --np 20

Registers into a THROWAWAY /tmp/ registry, never the real HPC one --
oceanicu_experiments.py add refuses a direct add anywhere else (bb-server1's
mirror sits at the same DB path as the authoritative HPC registry; see
EXPERIMENT_TRACKING.md "Set up an experiment"). Re-running this against an
already-registered experiment-id just resumes the loop --
--start/--stop/--chunk-kind/--chunk-multiplier are ignored that time.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

RUNNING_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_ROOTS_FILE = "/data/OceanICU/oceanicu_3d/experiments/NSe/bb-server1_data_roots.yaml"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--stop", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--chunk-kind", default=None, choices=["annual", "monthly", "daily"],
                    help="default: whatever oceanicu_experiments.py add itself defaults to")
    p.add_argument("--chunk-multiplier", type=int, default=None,
                    help="default: whatever oceanicu_experiments.py add itself defaults to")
    p.add_argument("--np", type=int, default=30)
    p.add_argument("--data-roots-file", default=DEFAULT_DATA_ROOTS_FILE)
    args = p.parse_args()

    experiment_root = Path.cwd()

    root_base = os.environ.get("OCEANICU_EXPERIMENT_ROOT_BASE")
    if not root_base:
        p.error("OCEANICU_EXPERIMENT_ROOT_BASE must be set (see running/experiment_defaults.yaml / kb.bash)")
    try:
        experiment_id = str(experiment_root.relative_to(Path(root_base).resolve()))
    except ValueError:
        p.error(f"current directory {experiment_root} is not under OCEANICU_EXPERIMENT_ROOT_BASE ({root_base})")

    scripts = sorted(
        f for f in experiment_root.glob("generated_*.py") if not f.name.endswith("_utils.py")
    )
    if not scripts:
        p.error(f"no generated_*.py found in {experiment_root} (run this from the experiment's own directory)")
    script = scripts[0]
    config = script.with_name(script.stem + "_config.yaml")
    if not config.exists():
        p.error(f"expected companion config {config} next to {script.name}, not found")

    # Never the real registry -- see module docstring. oceanicu_experiments.py's
    # own add-refusal check exempts /tmp/ paths specifically for this.
    db_path = os.environ.get("OCEANICU_EXPERIMENT_DB", "/tmp/oceanicu_local_registry.sqlite")
    # Resolved, not a plain string prefix check -- "/tmp/../data/real.sqlite"
    # satisfies str.startswith("/tmp/") while actually opening a path
    # outside /tmp, defeating the one purpose this check has (never touch
    # the real HPC registry from a local test run).
    if not Path(db_path).resolve().is_relative_to("/tmp"):
        p.error(
            f"OCEANICU_EXPERIMENT_DB must be a /tmp/ scratch path for local test runs "
            f"(got {db_path!r}, resolves to {Path(db_path).resolve()}) -- never point this "
            f"at the real HPC registry from here."
        )
    os.environ["OCEANICU_EXPERIMENT_DB"] = db_path

    sys.path.insert(0, str(RUNNING_DIR))
    import experiment_tracking as rt

    with rt.connect(db_path) as conn:
        already_registered = rt.get_experiment(conn, experiment_id) is not None

    if not already_registered:
        print(f"Registering {experiment_id!r} (root: {experiment_root}) in local test registry {db_path} ...")
        add_cmd = [
            sys.executable, str(RUNNING_DIR / "oceanicu_experiments.py"), "add",
            "--experiment-id", experiment_id,
            "--experiment-root", str(experiment_root),
            "--script", script.name,
            "--config", config.name,
            "--initial-date", args.start,
            "--stop-date", args.stop,
            "--data-roots-file", args.data_roots_file,
            "--np", str(args.np),
            "--launcher", "mpiexec",
            "--db", db_path,
        ]
        if args.chunk_kind:
            add_cmd += ["--chunk-kind", args.chunk_kind]
        if args.chunk_multiplier is not None:
            add_cmd += ["--chunk-multiplier", str(args.chunk_multiplier)]
        result = subprocess.run(add_cmd)
        if result.returncode != 0:
            return result.returncode
    else:
        print(
            f"{experiment_id!r} already registered in {db_path} -- resuming "
            "(ignoring any --start/--stop/--chunk-kind/--chunk-multiplier given now)."
        )

    print(f"Looping chunk_runner.py for {experiment_id!r} (Ctrl-C to stop; re-run this to resume) ...")
    while True:
        result = subprocess.run([
            sys.executable, str(RUNNING_DIR / "chunk_runner.py"),
            "--experiment-id", experiment_id,
            "--db", db_path,
        ])
        if result.returncode == 1:
            print(f"{experiment_id!r}: nothing to do (already complete or paused). Stopping.")
            return 0
        if result.returncode != 0:
            print(
                f"{experiment_id!r}: chunk failed (exit {result.returncode}). "
                "Stopping -- fix the issue, then re-run this to resume.",
                file=sys.stderr,
            )
            return result.returncode


if __name__ == "__main__":
    sys.exit(main())
