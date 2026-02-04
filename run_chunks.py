#!/usr/bin/env python

"""Run multiple time chunks

This script handles all book keeping when running a series of
sequential simulations to make one full simulation.

The individual simulations are done using the subprocess.run methodi
build up the command to execute by extending a tuple - command -
with provided information.

A whole-loop is used to loop over the necessary number of simulations
keeping track of start- and stop-time for the individial chunks.

The book keeping does:
    1) Handle start and stop times for the individual chunks
    2) Handle output and restart files with proper naming i.e.
    files are not overwritten and named in a traceable way.
    3) If a experiment is specified copy configuration file for
    future reference.

The script relies on environment variables for specifying the
simulation period - all with format %Y-%m-%d:

    1) SIMULATION_INITIAL_TIME
    2) SIMULATION_START_TIME
    if None set to SIMULATION_START_TIME
    3) SIMULATION_STOP_TIME

Further configuration is done via mandatory and optional commandline
parameters.

The script is intended to be used inside a script e.g. a script for
a queueing system

"""

import sys
import os
from pathlib import Path
import yaml
import shutil
import itertools
import subprocess
import argparse
import cftime
from datetime import timedelta


def _get_yaml_value(yaml_path, dotted_key, default=None):
    with Path(yaml_path).open() as f:
        data = yaml.safe_load(f) or {}
    for part in dotted_key.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return default
    return data


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fairly generic run-script wrapper for pyGETM setups",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "script",
        type=Path,
        help="Name of the Python run-script",
    )
    p.add_argument(
        "setup",
        type=str,
        help="Setup configuration YAML-file",
    )
    p.add_argument(
        "base_outdir",
        type=Path,
        help="Folder where output files will be saved - possible appended with exp.",
    )
    p.add_argument(
        "np",
        type=int,
        default=1,
        help="Number of cores to use",
    )
    p.add_argument(
        "--exp",
        help="Experiment identifier - if present will be added to base_outdir",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--hourly_chunks",
        action="store_true",
        help="Do the total simulation in hourly chunks - with optional multiplyer",
    )
    g.add_argument(
        "--daily_chunks",
        action="store_true",
        help="Do the total simulation in daily chunks - with optional multiplyer",
    )
    g.add_argument(
        "--monthly_chunks",
        action="store_true",
        help="Do the total simulation in monthly chunks - with optional multiplyer",
    )
    p.add_argument(
        "--chunk_multiplier",
        type=int,
        default=1,
        help="Multiplier for chunk size - default is annual chunks",
    )
    p.add_argument(
        "--dryrun",
        action="store_true",
        help="Only print commands to the screen - do not execute.",
    )

    return p.parse_args()


def main():
    args = parse_args()

    annual_chunks = True

    # Get the setup identifier from the YAML file
    _setup = _get_yaml_value(args.setup, "setup")
    _ = _get_yaml_value(args.setup, "meteo.source")
    calendar = "noleap" if _ == "CMIP6" else "standard"

    # if args.hourly_chunks or args.daily_chunks or args.monthly_chunks:
    if args.daily_chunks or args.monthly_chunks:
        annual_chunks = False

    _ = os.getenv("SIMULATION_START_DATE", None)
    if _:
        initial_date = cftime.datetime.strptime(_, "%Y-%m-%d", calendar=calendar)
    else:
        print("Env variable SIMULATION_INITIAL_DATE must be set with format - %Y-%m-%d")
        sys.exit(1)

    # used to start from a different data than initial_date - restart file must be available
    _ = os.getenv("SIMULATION_START_DATE", None)
    if _:
        start_date = cftime.datetime.strptime(_, "%Y-%m-%d", calendar=calendar)
    else:
        start_date = initial_date

    _ = os.getenv("SIMULATION_STOP_DATE", None)
    if _:
        stop_date = cftime.datetime.strptime(_, "%Y-%m-%d", calendar=calendar)
    else:
        print("Env variable SIMULATION_STOP_DATE must be set with format - %Y-%m-%d")
        sys.exit(1)

    start = start_date

    print(f"Total simulation from {start_date} to {stop_date}:")

    if args.exp:
        base_outdir = args.base_outdir / f"{args.exp}"
    else:
        base_outdir = args.base_outdir

    i = 1
    while start < stop_date:
        command = [
            "mpiexec",
            "-n",
            str(args.np),
            "python",
            str(args.script),
            str(args.setup),
            # "--dryrun",
        ]

        if args.hourly_chunks:
            print("hourly_chunks not implemented yet")
            sys.exit(1)
            # stop = start + timedelta(hours=args.chunk_multiplier)
        elif args.daily_chunks:
            stop = start + timedelta(days=args.chunk_multiplier)
        elif args.monthly_chunks:
            yy = start.year
            mm = start.month
            dd = start.day
            mm = mm - 1 + args.chunk_multiplier  # zero‑based month index
            yy = yy + mm // 12
            mm = mm % 12 + 1
            stop = start.replace(year=yy, month=mm)
        else:
            stop = start.replace(year=start.year + args.chunk_multiplier)

        if stop > stop_date:
            stop = stop_date

        x = start.strftime("%Y-%m-%d %H:%M:%S")
        y = stop.strftime("%Y-%m-%d %H:%M:%S")
        print(f"Simulation chunk #: {i:03d} - from {start} to {stop}")

        command.extend([x, y])

        x = start.strftime("%Y%m%d")
        y = stop.strftime("%Y%m%d")

        if start == start_date:
            print("Initial simulation")
            restart_in = None
        else:
            restart_in = Path(f"restart_{_setup}_{x}.nc")
            command.extend(["--load_restart", str(restart_in)])

        restart_out = Path(f"restart_{_setup}_{y}.nc")
        command.extend(["--save_restart", str(restart_out)])

        if annual_chunks:
            x = start.strftime("%Y")
        if args.monthly_chunks:
            x = start.strftime("%Y/%m")
        if args.daily_chunks:
            x = start.strftime("%Y/%m%d")
        # if args.daily_chunks:
        #     x = start.strftime("%Y/%m%dT%H")
        #
        outdir = base_outdir / f"{x}"
        command.extend(["--output_dir", str(outdir)])
        if not args.dryrun:
            if outdir.is_dir():
                print(
                    f"Folder {str(outdir)} already exists - move/delete and run again"
                )
                sys.exit(1)
            else:
                outdir.mkdir(parents=True)

        if args.dryrun:
            print(command)
            print(" ".join(command))
        else:
            subprocess.run(command)

        start = stop

        # clean up
        if not args.dryrun:
            files_to_move = list(
                itertools.chain.from_iterable(
                    Path(".").glob(pattern) for pattern in ["*.log", "*.prof"]
                )
            )
            for f in files_to_move:
                shutil.move(str(f), str(outdir))

            if restart_in:
                shutil.move(str(restart_in), str(outdir))

        i = i + 1

    # For future reference
    if args.exp and not args.dryrun:
        print(f"Copying {sys.argv[0]}, {args.script} and {args.setup} to {base_outdir}")
        shutil.copy(sys.argv[0], base_outdir)
        shutil.copy(args.script, base_outdir)
        shutil.copy(args.setup, base_outdir)
        # print(f"Copying ./lib to {base_outdir}")
        # shutil.copytree("lib", base_outdir)

    print(f"All - {i} - simulation chunks completed!")


if __name__ == "__main__":
    main()
