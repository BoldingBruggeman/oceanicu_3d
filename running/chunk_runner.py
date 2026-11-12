#!/usr/bin/env python
"""chunk_runner.py -- run exactly ONE chunk of a simulation.

Two modes:

  Tracked (production):
      chunk_runner.py --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01

  Standalone (testing -- NO database interaction at all, nothing looked
  up or written; for trying out a script/date-range by hand before it's
  registered). No --config: pygetm_config.codegen --dump-python scripts
  have their config baked in, not read from a separate file at runtime.
      chunk_runner.py --script generated_nse_cmip6.py \\
          --start 2015-01-01T00:00:00 --stop 2016-01-01T00:00:00 \\
          --save-restart restart_setup_20160101.nc [--load-restart ...] \\
          [--chunk-dir DIR] [--np 4] [--launcher srun|mpiexec]

In tracked mode, everything about the experiment (script, dates, chunk
size) comes from its `experiments` row in experiment_tracking.py's SQLite registry --
looked up by --experiment-id. Intended to be called once per SLURM job (see
run_chunk.slurm), which self-resubmits for the next chunk after this one
finishes; this script itself only ever does one.

Chunk boundary math (annual/monthly/daily x multiplier) is calendar-aware
via cftime, on the companion config's runtime.calendar.

Everything for a chunk lives together in ONE directory (logs, 2d/3d
output, AND the restart file it saves) -- <experiment_root>/NNN_<start>_
<stop>/. The next chunk's --load-restart simply points at the previous
chunk's own save_restart path.

Exit codes: 0 = chunk completed; 1 = nothing to do (complete, paused, or
another chunk running) -- tracked mode only; 2 = failure (the driver script
failed, or chunk_runner itself raised).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import socket
import sqlite3
import subprocess
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def _sha256_of(path: Optional[str]) -> Optional[str]:
    """Content hash of *path*, or None if it's unset/unreadable -- used to
    let experiment_tracking.start_chunk detect a real edit to the driver script/
    config between chunk attempts (see its own docstring), rather than the
    DB only ever recording an unchanging path string. Failing quietly
    (missing file, permissions) rather than raising: this is a nice-to-
    have audit signal, not something that should ever block a chunk from
    starting."""
    if not path:
        return None
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _parse_date(s: str, calendar: str):
    import cftime
    return cftime.datetime.strptime(s, "%Y-%m-%d", calendar=calendar)


def _experiment_calendar(config_path: str) -> str:
    """runtime.calendar of the experiment's companion *_config.yaml (the
    generated script runs on that calendar); pyGETM's default otherwise."""
    import yaml

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except OSError as exc:
        raise RuntimeError(f"can't read {config_path} for runtime.calendar: {exc}") from exc
    return (config.get("runtime") or {}).get("calendar") or "standard"


def _advance_date(start, kind: str, multiplier: int, is_first_chunk: bool = False):
    if kind == "daily":
        return start + timedelta(days=multiplier)
    if kind == "monthly":
        yy, mm = start.year, start.month
        mm = mm - 1 + multiplier
        yy += mm // 12
        mm = mm % 12 + 1
        return start.replace(year=yy, month=mm)
    # annual: only the first chunk (is_first_chunk, start == initial_date)
    # realigns to the next 1 January; later chunks keep whatever offset
    # they inherited and advance by the full multiplier.
    if is_first_chunk and (start.month != 1 or start.day != 1):
        return start.replace(year=start.year + 1, month=1, day=1)
    return start.replace(year=start.year + multiplier)


def _launch_prefix(launcher: str, np: int) -> list[str]:
    if launcher == "srun":
        return ["srun", "--mpi=pmi2", "-n", str(np)]
    if launcher == "mpiexec":
        return ["mpiexec", "-n", str(np)]
    raise ValueError(f"unknown launcher {launcher!r} (expected 'srun' or 'mpiexec')")


def _run_one(
    *, script: str, start_iso: str, stop_iso: str, save_restart: str,
    load_restart: Optional[str], data_roots_file: Optional[str], chunk_dir: Path,
    launcher: str, np: int, dry_run: bool, conda_env: Optional[str] = None,
    fabm: Optional[str] = None, load_restart_time: Optional[str] = None,
) -> subprocess.CompletedProcess | None:
    # No config argument: generated scripts have the config baked in.
    cmd = [
        *_launch_prefix(launcher, np),
        "python", script,
        "--start", start_iso,
        "--stop", stop_iso,
        "--save-restart", save_restart,
    ]
    if load_restart:
        cmd.extend(["--load-restart", load_restart])
        if load_restart_time:
            cmd.extend(["--load-restart-time", load_restart_time])
    if data_roots_file:
        cmd.extend(["--data-roots-file", data_roots_file])
    # 'off'/'on'/None sentinels match experiment_tracking.add_experiment's own `fabm`
    # column convention exactly -- see its docstring/oceanicu_experiments.py's
    # --fabm help. None: no override, script runs with its own baked-in
    # FABM setting untouched (no flag emitted at all).
    if fabm == "off":
        cmd.append("--no-fabm")
    elif fabm == "on":
        cmd.append("--fabm")
    elif fabm:
        cmd.extend(["--fabm", fabm])

    if conda_env:
        # For standalone testing from a shell without the env activated.
        cmd = ["conda", "run", "-n", conda_env, "--no-capture-output", *cmd]

    print("  cwd:", chunk_dir)
    print("  cmd:", " ".join(cmd))
    if dry_run:
        return None
    return subprocess.run(cmd, cwd=str(chunk_dir))


def _main_standalone(args: argparse.Namespace) -> int:
    """No database interaction whatsoever -- nothing looked up, nothing
    written. For trying a script/config/date-range by hand before it's
    registered (or just outside the tracked-production workflow entirely).
    """
    if args.extended_dry_run:
        import check_inputs

        report = check_inputs.check_generated_script(
            args.script, args.start, args.stop,
            data_roots_file=args.data_roots_file, load_restart=args.load_restart,
            fabm=args.fabm if args.fabm not in (None, "off", "on") else None,
        )
        print(f"[standalone, untracked]  extended dry-run over {args.start} -> {args.stop}")
        print(check_inputs.format_report(report))
        return 0 if report.ok else 2

    chunk_dir = Path(args.chunk_dir) if args.chunk_dir else Path.cwd()
    chunk_dir.mkdir(parents=True, exist_ok=True)

    print(f"[standalone, untracked]  {args.start} -> {args.stop}")
    result = _run_one(
        script=args.script, start_iso=args.start, stop_iso=args.stop,
        save_restart=args.save_restart, load_restart=args.load_restart,
        data_roots_file=args.data_roots_file, chunk_dir=chunk_dir,
        launcher=args.launcher, np=args.np, dry_run=args.dry_run, conda_env=args.conda_env,
        fabm=args.fabm, load_restart_time=args.load_restart_time,
    )
    if result is None:
        return 0
    return 0 if result.returncode == 0 else 2


def _main_tracked(args: argparse.Namespace) -> int:
    import experiment_tracking as rt

    # The account running this chunk, for the history log.
    user = rt._current_user()
    # Hostname of whichever machine is actually issuing this submission --
    # normally the production machine's own sbatch self-resubmission, but
    # chunk_runner.py can be invoked by hand for testing too, so this
    # records reality per chunk rather than assuming.
    submitted_host = socket.gethostname()

    with rt.connect(args.db) as conn:
        experiment = rt.get_experiment(conn, args.experiment_id)
        if experiment is None:
            print(f"ERROR: no such experiment_id in registry: {args.experiment_id!r}", file=sys.stderr)
            return 2
        experiment = dict(experiment)  # mutable regardless of Row (local) vs dict (RPC result)

        # experiment_root may be relative to this machine's OCEANICU_EXPERIMENT_ROOT_BASE.
        experiment["experiment_root"] = rt.resolve_experiment_root(experiment["experiment_root"])

        # Bare script/config names are relative to experiment_root (the
        # chunk runs with cwd=chunk_dir).
        experiment_root_path = Path(experiment["experiment_root"])
        for key in ("script", "config", "data_roots_file"):
            if experiment.get(key) and not Path(experiment[key]).is_absolute():
                experiment[key] = str(experiment_root_path / experiment[key])

        if args.extended_dry_run:
            import check_inputs

            chunk_index = rt.next_chunk_index(conn, args.experiment_id)
            report = check_inputs.check_generated_script(
                experiment["script"], experiment["initial_date"], experiment["stop_date"],
                data_roots_file=experiment.get("data_roots_file"),
                load_restart="n/a" if chunk_index > 0 else None,
                fabm=experiment.get("fabm") if experiment.get("fabm") not in (None, "off", "on") else None,
            )
            print(f"{args.experiment_id}: extended dry-run over "
                  f"{experiment['initial_date']} -> {experiment['stop_date']}")
            print(check_inputs.format_report(report))
            return 0 if report.ok else 2

        if rt.is_paused(conn, args.experiment_id, experiment["experiment_root"]):
            print(f"{args.experiment_id}: paused (control or PAUSE sentinel) -- not starting a new chunk.")
            return 1

        # Lock: no new chunk while one is recorded as running, unless its
        # job is gone (or too old to excuse an unavailable squeue) -- then it
        # is marked failed (orphaned) and left for a human rerun.
        running = rt.get_running_chunk(conn, args.experiment_id)
        if running is not None:
            state = rt.chunk_orphan_state(running)
            if state == "alive":
                print(f"{args.experiment_id}: chunk {running['chunk_index']} is already running "
                      f"(SLURM job {running['slurm_job_id']}) -- not starting another.",
                      file=sys.stderr)
                return 1
            if state == "unknown":
                print(f"{args.experiment_id}: chunk {running['chunk_index']} is marked running and "
                      f"its SLURM job status can't be confirmed (squeue unavailable) but it "
                      f"isn't old enough yet to treat as orphaned -- not starting another.",
                      file=sys.stderr)
                return 1

            print(f"{args.experiment_id}: chunk {running['chunk_index']} was marked running but its "
                  f"SLURM job is no longer active -- treating as crashed/orphaned.",
                  file=sys.stderr)
            rt.finish_chunk(conn, experiment_id=args.experiment_id, chunk_index=running["chunk_index"],
                             exit_code=rt.EXIT_CODE_ORPHANED, nan_detected=False, user=user)
            print(f"Marked failed. Investigate, then "
                  f"'oceanicu_experiments.py rerun --experiment-id {args.experiment_id} --from-current' to redo it.",
                  file=sys.stderr)
            return 2

        calendar = _experiment_calendar(experiment["config"])
        stop_date = _parse_date(experiment["stop_date"], calendar)

        start_str = rt.next_chunk_start(conn, args.experiment_id, experiment["initial_date"])
        start = _parse_date(start_str, calendar)
        if start >= stop_date:
            print(f"{args.experiment_id}: already reached stop_date ({experiment['stop_date']}) -- nothing to do.")
            rt.recompute_experiment_status(conn, args.experiment_id)
            return 1

        # next_chunk_start returns initial_date only before any chunk ran.
        is_first_chunk = start_str == experiment["initial_date"]
        stop = _advance_date(start, experiment["chunk_kind"], experiment["chunk_multiplier"], is_first_chunk)
        if stop > stop_date:
            stop = stop_date

        chunk_index = rt.next_chunk_index(conn, args.experiment_id)

        # next_chunk_index (all rows) and next_chunk_start (done rows) are
        # independent queries; a non-done row at chunk_index-1 would number
        # this chunk right but date it from an earlier chunk. Refuse.
        if chunk_index > 0:
            predecessor = rt.get_chunk(conn, args.experiment_id, chunk_index - 1)
            if predecessor is None:
                print(
                    f"ERROR: chunk {chunk_index} would start at {start_str} (from next_chunk_start), "
                    f"but chunk {chunk_index - 1} -- which its own directory name implies came right "
                    f"before it -- doesn't exist in the registry at all. Inspect "
                    f"'oceanicu-experiments show --experiment-id {args.experiment_id}' and fix the "
                    f"chunk history by hand before retrying.",
                    file=sys.stderr,
                )
                return 2
            if predecessor["status"] != "done" or predecessor["stop"] != start_str:
                print(
                    f"ERROR: chunk {chunk_index} would be numbered right after chunk "
                    f"{chunk_index - 1}, but chunk {chunk_index - 1}'s own row is "
                    f"status={predecessor['status']!r} stop={predecessor['stop']!r} -- not the "
                    f"'done' row ending at {start_str!r} that next_chunk_start actually used to "
                    f"compute this chunk's start date. Building chunk {chunk_index}'s directory "
                    f"now would number it {chunk_index} but date-range it from whatever chunk "
                    f"really finished last, not from {chunk_index - 1} -- the exact mismatch "
                    f"reported before (e.g. '002_' built with '001_'s dates).\n"
                    f"Remedy: drop the bad row and everything after it, then retry --\n"
                    f"  oceanicu-experiments rerun --experiment-id {args.experiment_id} "
                    f"--from-chunk {chunk_index - 1}"
                    + (" --force" if predecessor["status"] == "running" else ""),
                    file=sys.stderr,
                )
                return 2

        experiment_root = Path(experiment["experiment_root"])
        chunk_name = f"{chunk_index:03d}_{start.strftime('%Y%m%d')}_{stop.strftime('%Y%m%d')}"
        chunk_dir = experiment_root / chunk_name

        load_restart = None
        load_restart_time = None
        if chunk_index > 0:
            load_restart = rt.get_done_chunk_restart(conn, args.experiment_id, chunk_index - 1)
            if load_restart is None:
                print(f"ERROR: chunk {chunk_index} has no completed predecessor to load a restart from.",
                      file=sys.stderr)
                return 2
            # One-shot request (e.g. `rerun --restart-time`); start_chunk clears it.
            load_restart_time = experiment.get("next_load_restart_time")

        setup_name = Path(experiment["config"]).stem
        save_restart = str(chunk_dir / f"restart_{setup_name}_{stop.strftime('%Y%m%d')}.nc")
        run_kwargs = dict(
            script=experiment["script"],
            start_iso=start.strftime("%Y-%m-%dT%H:%M:%S"), stop_iso=stop.strftime("%Y-%m-%dT%H:%M:%S"),
            save_restart=save_restart, load_restart=load_restart, data_roots_file=experiment["data_roots_file"],
            chunk_dir=chunk_dir, launcher=experiment["launcher"] or "srun", np=experiment["np"],
            fabm=experiment.get("fabm"), load_restart_time=load_restart_time,
        )

        print(f"{args.experiment_id}  chunk {chunk_index:03d}  {start} -> {stop}")
        if args.dry_run:
            # Before any filesystem change: a dry run must leave the experiment untouched.
            _run_one(**run_kwargs, dry_run=True)
            return 0

        # The running-chunk check above and this insert aren't one transaction;
        # (experiment_id, chunk_index) is the primary key, so a second process
        # racing this one loses here and backs off before touching any files.
        try:
            rt.start_chunk(
                conn, experiment_id=args.experiment_id, chunk_index=chunk_index,
                start=start.strftime("%Y-%m-%d"), stop=stop.strftime("%Y-%m-%d"),
                chunk_dir=str(chunk_dir), load_restart=load_restart, save_restart=save_restart,
                slurm_job_id=args.slurm_job_id, user=user,
                script_sha256=_sha256_of(experiment["script"]), config_sha256=_sha256_of(experiment["config"]),
                submitted_host=submitted_host, load_restart_time=load_restart_time,
            )
        except sqlite3.IntegrityError:
            print(f"{args.experiment_id}: chunk {chunk_index} was just claimed by another process "
                  f"-- backing off.", file=sys.stderr)
            return 1

    # From here on the chunk row exists: any failure must be recorded on it,
    # or it stays 'running' until the orphan reaper finds it days later.
    try:
        _prepare_chunk_dir(chunk_dir, experiment_root)
        result = _run_one(**run_kwargs, dry_run=False)
        assert result is not None  # dry_run=False always returns a CompletedProcess
        exit_code = result.returncode
    except Exception:
        traceback.print_exc()
        exit_code = rt.EXIT_CODE_RUNNER_ERROR

    with rt.connect(args.db) as conn:
        rt.finish_chunk(
            conn, experiment_id=args.experiment_id, chunk_index=chunk_index,
            exit_code=exit_code, nan_detected=False, user=user,
        )

    return 0 if exit_code == 0 else 2


def _prepare_chunk_dir(chunk_dir: Path, experiment_root: Path) -> None:
    """Create chunk_dir, archiving a previous attempt's dir aside (a rerun only
    rewinds the DB), and copy gotm.yaml/fabm*.yaml into it: the generated
    script opens them relative to its cwd, which is chunk_dir."""
    if chunk_dir.exists():
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        archived = chunk_dir.with_name(chunk_dir.name + f".attempt-{stamp}")
        print(f"{chunk_dir} already exists -- archiving aside as {archived.name}")
        chunk_dir.rename(archived)
    chunk_dir.mkdir(parents=True)
    for name in ("gotm.yaml", *[p.name for p in experiment_root.glob("fabm*.yaml")]):
        src = experiment_root / name
        if src.is_file():
            shutil.copy2(src, chunk_dir / name)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--experiment-id", default=None, help="tracked mode: look everything up from the registry")
    p.add_argument("--db", default=None, help="tracked mode: override the SQLite registry path")
    p.add_argument("--slurm-job-id", default=None)
    p.add_argument("--dry-run", action="store_true", help="print the command, don't execute")
    p.add_argument("--extended-dry-run", action="store_true",
                    help="like --dry-run, but also checks that every input file for the WHOLE "
                         "run (not just the next chunk) exists, is readable, and covers the "
                         "requested period, and reports where output will be written")

    standalone = p.add_argument_group(
        "standalone mode (no --experiment-id; NO database interaction at all)")
    standalone.add_argument("--script")
    standalone.add_argument("--start", metavar="ISO8601")
    standalone.add_argument("--stop", metavar="ISO8601")
    standalone.add_argument("--save-restart", metavar="PATH")
    standalone.add_argument("--load-restart", default=None, metavar="PATH")
    standalone.add_argument("--load-restart-time", default=None, metavar="ISO8601",
                             help="which internal snapshot of --load-restart to resume from, "
                                  "for a file holding more than one; default: its last snapshot")
    standalone.add_argument("--data-roots-file", default=None)
    standalone.add_argument("--chunk-dir", default=None, help="default: current directory")
    standalone.add_argument("--np", type=int, default=1)
    standalone.add_argument("--launcher", default="srun", choices=["srun", "mpiexec"])
    standalone.add_argument("--fabm", nargs="?", const="on", default=None, metavar="PATH",
                             help="override the driver's FABM state (bare --fabm reuses its "
                                  "configured path; --fabm PATH forces a specific one; "
                                  "default: don't override)")
    standalone.add_argument("--no-fabm", dest="fabm", action="store_const", const="off",
                             help="force FABM off, regardless of the driver's own setting")
    standalone.add_argument("--conda-env", default=None, metavar="NAME",
                             help="wrap the command in 'conda run -n NAME' -- for testing "
                                  "from a shell that hasn't already activated it")

    args = p.parse_args()

    if args.experiment_id:
        # Exit 1 means "nothing to do" to every wrapper (run_chunk.slurm,
        # chunk_chain.py, run_chunks_local.py) -- Python's own exit status for
        # an uncaught exception is also 1, so every error must map to 2.
        try:
            return _main_tracked(args)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        except Exception:
            traceback.print_exc()
            return 2

    required = ["script", "start", "stop"]
    if not args.extended_dry_run:
        required.append("save_restart")
    missing = [f"--{name.replace('_', '-')}" for name in required if getattr(args, name) is None]
    if missing:
        p.error(f"standalone mode (no --experiment-id given) requires {', '.join(missing)}")
    return _main_standalone(args)


if __name__ == "__main__":
    sys.exit(main())
