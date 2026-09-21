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

Chunk boundary math (annual/monthly/daily x multiplier) is the same idea
as the earlier run_chunks.py prototype, calendar-aware via cftime.

Everything for a chunk lives together in ONE directory (logs, 2d/3d
output, AND the restart file it saves) -- <experiment_root>/NNN_<start>_
<stop>/. The next chunk's --load-restart simply points at the previous
chunk's own save_restart path.

Exit codes: 0 = chunk completed cleanly; 1 = nothing to do (already at
stop_date, or paused) -- tracked mode only; 2 = the chunk itself failed
(non-zero exit from the driver script).
"""

from __future__ import annotations

import argparse
import hashlib
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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


def _advance_date(start, kind: str, multiplier: int):
    if kind == "daily":
        return start + timedelta(days=multiplier)
    if kind == "monthly":
        yy, mm = start.year, start.month
        mm = mm - 1 + multiplier
        yy += mm // 12
        mm = mm % 12 + 1
        return start.replace(year=yy, month=mm)
    # annual (default): every chunk boundary should land on a real
    # calendar-year edge (Jan 1) once things get going. If *start* isn't
    # already Jan 1 (e.g. initial_date 2010-01-02, or a spin-up beginning
    # in December), the multiplier is ignored for this one call --
    # instead of a near-full-length chunk permanently offset by whatever
    # day-of-year start happened to land on (the real bug this replaces:
    # start.replace(year=start.year+multiplier) preserves start's own
    # month/day, and the old code only checked stop.month != 1, so a
    # start that already happened to be in January -- just not day 1 --
    # never got caught at all, e.g. 2010-01-02 -> 2020-01-02, forever
    # offset by one day, confirmed against a real chunk 0 row), this call
    # returns a SHORT realignment chunk stopping at the very next Jan 1.
    # Every following call then starts from a clean Jan 1, so the
    # multiplier applies normally from then on -- no bookkeeping needed
    # beyond this function always being handed whatever the previous
    # chunk's own stop was.
    if start.month != 1 or start.day != 1:
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
    # No config path on the command line: driver scripts generated by
    # pygetm_config.codegen's --dump-python have the config baked in as a
    # "literal, standalone reproduction" (see the generated script's own
    # docstring) -- they take zero positional args, only --start/--stop/
    # --dry-run/--load-restart/--save-restart/--data-roots-file. The experiment's
    # `config` field (still stored in the DB, still shown by
    # oceanicu_experiments.py show) is for reproducibility/regeneration record-
    # keeping only, not a runtime input to the driver.
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
        # Makes the command self-contained regardless of whether the
        # calling shell already ran `conda activate` -- the tracked/SLURM
        # path relies on run_chunk.slurm having activated it once up
        # front (unaffected, conda_env stays unset there), but standalone
        # testing from an arbitrary shell shouldn't have to remember to.
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

    # Whichever account is actually executing this chunk_runner.py process
    # (the SLURM job's own account on the production machine, normally) --
    # not necessarily who originally ran `oceanicu_experiments.py add`, which is
    # exactly the point: the history log should show who/what did each
    # thing, and a chunk executing is a different actor from whoever
    # registered the experiment.
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

        # experiment_root itself may be relative (registered from a workstation
        # that doesn't know exactly where this experiment's output will land on
        # THIS machine) -- resolve it against this machine's own
        # OCEANICU_EXPERIMENT_ROOT_BASE once, here, before anything below builds
        # a path from it. See experiment_tracking.resolve_experiment_root.
        experiment["experiment_root"] = rt.resolve_experiment_root(experiment["experiment_root"])

        # script/config are commonly registered as bare filenames (living
        # in the experiment's own folder, alongside its own generated driver
        # script -- see EXPERIMENT_TRACKING.md), but the actual chunk subprocess
        # always runs with cwd=chunk_dir, several levels below experiment_root.
        # Resolve them against experiment_root here, once, so a bare filename
        # still works regardless of where this script itself lives or
        # what the process's cwd ends up being.
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

        # Lock: refuse to start a new chunk while one is already recorded
        # as running for this experiment_id -- guards against an accidental
        # double-submit racing the currently-running chunk (two processes
        # both computing the same next chunk_index and colliding). If the
        # recorded chunk's SLURM job is confirmed gone (or old enough that
        # squeue being unavailable can't excuse it any longer), it's
        # treated as crashed/orphaned rather than a live lock -- marked
        # failed and left for a human 'rerun' rather than silently
        # retried, same as any other failure.
        running = rt.get_running_chunk(conn, args.experiment_id)
        if running is not None:
            alive = rt.is_slurm_job_running(running["slurm_job_id"])
            stale_by_age = False
            if running["start_time"]:
                started = datetime.fromisoformat(running["start_time"])
                stale_by_age = (datetime.now(timezone.utc) - started) > timedelta(days=4)

            if alive:
                print(f"{args.experiment_id}: chunk {running['chunk_index']} is already running "
                      f"(SLURM job {running['slurm_job_id']}) -- not starting another.",
                      file=sys.stderr)
                return 1
            if alive is None and not stale_by_age:
                print(f"{args.experiment_id}: chunk {running['chunk_index']} is marked running and "
                      f"its SLURM job status can't be confirmed (squeue unavailable) but it "
                      f"isn't old enough yet to treat as orphaned -- not starting another.",
                      file=sys.stderr)
                return 1

            print(f"{args.experiment_id}: chunk {running['chunk_index']} was marked running but its "
                  f"SLURM job is no longer active -- treating as crashed/orphaned.",
                  file=sys.stderr)
            rt.finish_chunk(conn, experiment_id=args.experiment_id, chunk_index=running["chunk_index"],
                             exit_code=-1, nan_detected=False, user=user)
            print(f"Marked failed. Investigate, then "
                  f"'oceanicu_experiments.py rerun --experiment-id {args.experiment_id} --from-current' to redo it.",
                  file=sys.stderr)
            return 2

        calendar = "noleap" if "CMIP6" in experiment["script"] or "CMIP6" in experiment["config"] else "standard"
        stop_date = _parse_date(experiment["stop_date"], calendar)

        start_str = rt.next_chunk_start(conn, args.experiment_id, experiment["initial_date"])
        start = _parse_date(start_str, calendar)
        if start >= stop_date:
            print(f"{args.experiment_id}: already reached stop_date ({experiment['stop_date']}) -- nothing to do.")
            rt.recompute_experiment_status(conn, args.experiment_id)
            return 1

        stop = _advance_date(start, experiment["chunk_kind"], experiment["chunk_multiplier"])
        if stop > stop_date:
            stop = stop_date

        chunk_index = rt.next_chunk_index(conn, args.experiment_id)

        # Consistency check: next_chunk_index (MAX(chunk_index)+1 over ALL
        # rows) and next_chunk_start (MAX(stop) WHERE status='done') are two
        # independent queries -- nothing else keeps them in agreement. If a
        # row sits at chunk_index-1 that ISN'T 'done' (e.g. a stale 'running'
        # row left behind by a crash that never reached finish_chunk, never
        # cleaned up), next_chunk_index still counts it (this chunk gets the
        # right NUMBER), but next_chunk_start falls back to whatever chunk
        # actually finished last, which can be further back (this chunk gets
        # the WRONG start date) -- a real, reported bug: chunk_dir ends up
        # numbered e.g. "002_" but with "001_"'s own date range. Fail loudly
        # here, before chunk_dir is built from these mismatched values,
        # rather than silently creating a corrupted-looking directory and
        # then failing confusingly later when load_restart doesn't match.
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

        # Archive a pre-existing dir aside instead of silently overwriting
        # it -- happens on a rerun (experiment_tracking.rerun_from only rewinds
        # the DB, it never deletes files) or after a crash that never
        # reached finish_chunk(). A previous attempt's logs are worth
        # keeping for diagnosis, not clobbering.
        if chunk_dir.exists():
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            archived = chunk_dir.with_name(chunk_dir.name + f".attempt-{stamp}")
            print(f"{chunk_dir} already exists -- archiving aside as {archived.name}")
            chunk_dir.rename(archived)
        chunk_dir.mkdir(parents=True)

        load_restart = None
        load_restart_time = None
        if chunk_index > 0:
            load_restart = rt.get_done_chunk_restart(conn, args.experiment_id, chunk_index - 1)
            if load_restart is None:
                print(f"ERROR: chunk {chunk_index} has no completed predecessor to load a restart from.",
                      file=sys.stderr)
                return 2
            # Pending, one-shot request (see experiments.next_load_restart_time's
            # own schema comment) -- e.g. from `rerun --restart-time`, to resume
            # from a specific snapshot in a restart file that holds more than
            # one, instead of the file's own default (its last snapshot).
            # start_chunk consumes and clears it below once this chunk's row
            # is actually inserted.
            load_restart_time = experiment.get("next_load_restart_time")

        setup_name = Path(experiment["config"]).stem
        save_restart = str(chunk_dir / f"restart_{setup_name}_{stop.strftime('%Y%m%d')}.nc")

        print(f"{args.experiment_id}  chunk {chunk_index:03d}  {start} -> {stop}")
        if args.dry_run:
            _run_one(
                script=experiment["script"],
                start_iso=start.strftime("%Y-%m-%dT%H:%M:%S"), stop_iso=stop.strftime("%Y-%m-%dT%H:%M:%S"),
                save_restart=save_restart, load_restart=load_restart, data_roots_file=experiment["data_roots_file"],
                chunk_dir=chunk_dir, launcher=experiment["launcher"] or "srun", np=experiment["np"], dry_run=True,
                fabm=experiment.get("fabm"), load_restart_time=load_restart_time,
            )
            chunk_dir.rmdir()  # dry-experiment shouldn't leave an empty dir behind
            return 0

        # The running-chunk check above and this insert aren't wrapped in
        # one atomic transaction, so two processes starting at the exact
        # same instant could both pass the check and then collide here
        # (experiment_id, chunk_index) is the primary key. Rare in practice (a
        # new chunk only ever starts via one sbatch resubmission at a
        # time), but fail cleanly rather than crash if it ever happens --
        # whichever process loses just backs off, the winner proceeds.
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
            chunk_dir.rmdir()
            return 1

        # Pointer file so a wrapper (e.g. run_chunk.slurm) that needs this
        # chunk's log path to tail it live doesn't have to guess or parse
        # stdout -- it can just wait for this file and read it. Written
        # only AFTER start_chunk() actually wins the DB race (not before
        # it, as this used to be): the double-submit race above means two
        # processes could each write this same experiment-wide pointer
        # file for their OWN chunk_dir before either one knows who wins --
        # whoever wrote last would leave the pointer aimed at the LOSER's
        # chunk_dir, which then gets rmdir()'d a few lines up, leaving the
        # wrapper tailing a directory that no longer exists even though
        # the winner's chunk is the one actually running.
        (experiment_root / ".current_chunk_dir").write_text(str(chunk_dir) + "\n")

    # DB connection closed while the (potentially long-running) simulation
    # executes, so it isn't held open across the whole chunk.
    result = _run_one(
        script=experiment["script"],
        start_iso=start.strftime("%Y-%m-%dT%H:%M:%S"), stop_iso=stop.strftime("%Y-%m-%dT%H:%M:%S"),
        save_restart=save_restart, load_restart=load_restart, data_roots_file=experiment["data_roots_file"],
        chunk_dir=chunk_dir, launcher=experiment["launcher"] or "srun", np=experiment["np"], dry_run=False,
        fabm=experiment.get("fabm"), load_restart_time=load_restart_time,
    )
    assert result is not None  # dry_run=False above always returns a CompletedProcess

    with rt.connect(args.db) as conn:
        rt.finish_chunk(
            conn, experiment_id=args.experiment_id, chunk_index=chunk_index,
            exit_code=result.returncode, nan_detected=False, user=user,
        )

    return 0 if result.returncode == 0 else 2


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
        try:
            return _main_tracked(args)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
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
