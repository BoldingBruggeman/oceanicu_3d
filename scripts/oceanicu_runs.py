#!/usr/bin/env python
"""oceanicu_runs.py -- manage the production-run tracking registry.

    oceanicu_runs.py add    --run-id ... --run-root ... --script ... --config ...
                             --initial-date 2015-01-01 --stop-date 2099-12-31
                             [--data-roots-file ...] [--chunk-kind annual]
                             [--chunk-multiplier 5] [--np 192] [--priority 0]
    oceanicu_runs.py remove --run-id ... [--force]
    oceanicu_runs.py list   [--status in_progress] [--like MPI-ESM1-2-HR]
    oceanicu_runs.py show   --run-id ...              # run + full chunk history
    oceanicu_runs.py chunk-size --run-id ... --chunk-kind ... --chunk-multiplier ...
    oceanicu_runs.py set-chunk-delay --run-id ... --seconds N   # persistent, per-run pacing
    oceanicu_runs.py pause  --run-id ... | --all
    oceanicu_runs.py resume --run-id ... | --all
    oceanicu_runs.py delay-all --seconds N | --clear
    oceanicu_runs.py rerun  --run-id ... [--from-chunk N | --from-current | --from-scratch] [--note ...]

    # preview ANY of the above for real, against a scratch copy in /tmp,
    # without ever writing to the configured registry:
    oceanicu_runs.py --dry-run <any command above>

pause/resume set the DB `control` column -- the normal, auditable way.
For a genuine HPC-overload emergency, `touch <run_root>/PAUSE` (one run)
works even if this tool or the DB itself is unreachable; for pausing
everything, see run_tracking.pause_all_sentinel_path (its location is
derived from wherever the registry DB actually lives, not a fixed path).
Either mechanism takes effect only between chunks, never mid-chunk -- a
currently-running chunk always finishes cleanly first, and needs a manual
resume to lift.

delay-all is different: a TIMED pause on an already-running system ("the
HPC needs to be used for something else for a while") -- the next
submission waits out the remainder then proceeds automatically, no manual
resume needed, and it's live-adjustable at any time by running `delay-all
--seconds N` again with a new value (see run_tracking.
chunk_delay_sentinel_path for the raw file, if this tool itself is
unreachable).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import run_tracking as rt

_RUN_COLUMNS = [
    "run_id", "status", "control", "chunk_kind", "chunk_multiplier",
    "initial_date", "stop_date", "priority", "chunk_delay_seconds",
]


def _print_table(rows: list, columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in columns}
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in columns))


def _resolve_real_db_path(args: argparse.Namespace):
    real = args.db or os.environ.get("OCEANICU_RUN_DB")
    if not real:
        raise RuntimeError("No database path given: pass --db, or set OCEANICU_RUN_DB.")
    return rt._parse_db_spec(real)  # Path, or RemoteSpec for an ssh:// path


def _pull_snapshot_copy(real_db, scratch_db: Path) -> None:
    """Copy *real_db* to a fresh LOCAL file at *scratch_db*, for real
    (not simulated) dry-run execution against it -- read-only as far as
    the real DB is concerned either way. A local real_db is just
    shutil.copy2 (skipped if it doesn't exist yet -- dry-run then starts
    from an empty scratch DB, same as the no-DB-configured case). A
    remote one is backed up with Python's own sqlite3.Connection.backup()
    (WAL-consistent, unlike copying a possibly-mid-write file directly,
    and depends only on the sqlite3 MODULE -- always present, since
    run_tracking_server.py itself needs it -- not the separate `sqlite3`
    CLI binary, which isn't guaranteed to be installed on an arbitrary
    relay) then `scp`'d down; nothing is ever written back to the real
    DB. The script is sent over `python3`'s stdin (same pattern as
    RemoteConn.call's own JSON payload), not as a `-c` argument -- ssh
    joins all trailing argv into one string for the REMOTE shell to
    re-parse, so a multi-line script with quotes and colons gets mangled
    if sent that way; stdin has no such quoting to survive."""
    if isinstance(real_db, rt.RemoteSpec):
        remote_tmp = f"/tmp/.oceanicu_dryrun_backup_{os.getpid()}.sqlite"
        backup_script = (
            "import os, sqlite3\n"
            f"src, dst = {real_db.db_path!r}, {remote_tmp!r}\n"
            "if os.path.exists(src):\n"
            "    s = sqlite3.connect(src)\n"
            "    d = sqlite3.connect(dst)\n"
            "    s.backup(d)\n"
            "    d.close(); s.close()\n"
        )
        subprocess.run(["ssh", real_db.host, "python3"], input=backup_script, text=True, check=False)
        subprocess.run(["scp", "-q", f"{real_db.host}:{remote_tmp}", str(scratch_db)],
                        check=False, stderr=subprocess.DEVNULL)
        subprocess.run(["ssh", real_db.host, "rm", "-f", remote_tmp], check=False)
    elif real_db.exists():
        shutil.copy2(real_db, scratch_db)


def _snapshot(db_path: Path) -> dict:
    """Full state worth comparing before/after: every run row, plus (for
    whichever run_id this invocation names, if any) its full chunk
    history -- cheap enough to just always capture both in full rather
    than trying to guess which command touches what. Works uniformly
    whether *db_path* exists yet or not -- connect() creates an empty
    schema either way, so an as-yet-nonexistent scratch DB just reads
    back as "no runs", no special-casing needed."""
    with rt.connect(db_path) as conn:
        runs = [dict(r) for r in rt.list_runs(conn)]
        chunks = {r["run_id"]: [dict(c) for c in rt.list_chunks(conn, r["run_id"])] for r in runs}
        history = {r["run_id"]: [dict(h) for h in rt.list_history(conn, r["run_id"])] for r in runs}
    return {"runs": runs, "chunks": chunks, "history": history}


def _print_diff(before: dict, after: dict) -> None:
    before_runs = {r["run_id"]: r for r in before["runs"]}
    after_runs = {r["run_id"]: r for r in after["runs"]}

    added = after_runs.keys() - before_runs.keys()
    removed = before_runs.keys() - after_runs.keys()
    changed = {
        rid for rid in (after_runs.keys() & before_runs.keys())
        if before_runs[rid] != after_runs[rid]
    }

    if not (added or removed or changed):
        print("No change to any run row.")
    for rid in sorted(added):
        print(f"+ {rid}: NEW")
        _print_table([after_runs[rid]], _RUN_COLUMNS)
    for rid in sorted(removed):
        print(f"- {rid}: REMOVED")
    for rid in sorted(changed):
        print(f"~ {rid}:")
        for key in _RUN_COLUMNS:
            if before_runs[rid][key] != after_runs[rid][key]:
                print(f"    {key}: {before_runs[rid][key]!r} -> {after_runs[rid][key]!r}")

    for rid in sorted(added | changed):
        n_before = len(before["chunks"].get(rid, []))
        n_after = len(after["chunks"].get(rid, []))
        if n_before != n_after:
            print(f"    chunks: {n_before} -> {n_after}")
        h_before = before["history"].get(rid, [])
        h_after = after["history"].get(rid, [])
        if len(h_after) != len(h_before):
            print(f"    history: {len(h_before)} -> {len(h_after)} entries")
            for h in h_after[len(h_before):]:
                who = f" ({h['user']})" if h['user'] else ""
                print(f"      + {h['event']}{who}" + (f": {h['detail']}" if h['detail'] else ""))


def _preview_chunk_runner(scratch_db: Path, run_id: str) -> None:
    """What run_chunk.slurm's own chunk_runner.py call would actually do
    next for this run, given the (dry-run) registry state -- resolved
    dates, chunk directory, load/save-restart paths, the real launch
    command -- by really invoking chunk_runner.py --dry-run against the
    same scratch copy, not re-deriving/guessing the logic separately."""
    script = Path(__file__).resolve().parent / "chunk_runner.py"
    print(f"[dry-run] what run_chunk.slurm would do next for {run_id!r}:")
    result = subprocess.run(
        [sys.executable, str(script), "--db", str(scratch_db), "--run-id", run_id, "--dry-run"],
        capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    if "OCEANICU_RUN_ROOT_BASE is not set" in output:
        print(f"    -- can't preview: run_root is relative and this machine has no "
              f"OCEANICU_RUN_ROOT_BASE set. NOT a real problem -- this is the same known "
              f"limitation as the PAUSE-file check (see RUN_TRACKING.md's \"One real "
              f"limitation\" note): resolved on whichever machine actually runs the chunk, "
              f"once that machine's OCEANICU_RUN_ROOT_BASE is exported there. --")
        return
    for line in output.splitlines():
        print(f"    {line}")


def cmd_add(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.add_run(
            conn, run_id=args.run_id, run_root=args.run_root, script=args.script,
            config=args.config, initial_date=args.initial_date, stop_date=args.stop_date,
            data_roots_file=args.data_roots_file, chunk_kind=args.chunk_kind,
            chunk_multiplier=args.chunk_multiplier, np=args.np, launcher=args.launcher,
            priority=args.priority, notes=args.notes, fabm=args.fabm,
            chunk_delay_seconds=args.chunk_delay_seconds, user=rt._current_user(),
        )
    print(f"added {args.run_id!r}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        try:
            rt.remove_run(conn, args.run_id, force=args.force, user=rt._current_user())
        except (KeyError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    print(f"removed {args.run_id!r} from the registry (files on disk untouched)")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rows = rt.list_runs(conn, status=args.status)
        if args.like:
            rows = [r for r in rows if args.like in r["run_id"]]
        _print_table(rows, _RUN_COLUMNS)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        run = rt.get_run(conn, args.run_id)
        if run is None:
            print(f"ERROR: no such run_id: {args.run_id!r}", file=sys.stderr)
            return 1
        print("run:")
        _print_table([run], _RUN_COLUMNS + ["run_root", "script", "config", "np", "launcher", "fabm", "data_roots_file", "notes"])
        print()
        print("chunks:")
        chunks = rt.list_chunks(conn, args.run_id)
        # Full sha256 is 64 hex chars -- far too wide for this table, and a
        # short prefix is all a human needs to eyeball "did this change
        # between chunks" (run_tracking.start_chunk's own script_changed/
        # config_changed history events already carry the same prefix
        # length, so a hash spotted here is directly greppable there).
        chunks_display = [
            {
                **dict(c),
                "script_sha256": (c["script_sha256"] or "")[:12],
                "config_sha256": (c["config_sha256"] or "")[:12],
            }
            for c in chunks
        ]
        _print_table(
            chunks_display,
            ["chunk_index", "start", "stop", "status", "exit_code", "nan_detected",
             "script_sha256", "config_sha256", "slurm_job_id", "start_time", "end_time"],
        )
        print()
        print("history:")
        history = rt.list_history(conn, args.run_id)
        _print_table(history, ["timestamp", "user", "event", "detail"])
    return 0


def cmd_set_priority(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_priority(conn, args.run_id, args.priority, user=rt._current_user())
    print(f"{args.run_id}: priority set to {args.priority}")
    return 0


def cmd_set_chunk_delay(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_chunk_delay(conn, args.run_id, args.seconds, user=rt._current_user())
    print(f"{args.run_id}: chunk_delay_seconds set to {args.seconds} "
          f"(takes effect on the next chunk/resubmission)")
    return 0


def cmd_set_stop_date(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_stop_date(conn, args.run_id, args.stop_date, user=rt._current_user())
    print(f"{args.run_id}: stop_date set to {args.stop_date} (takes effect on the next chunk)")
    return 0


def cmd_chunk_size(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_chunk_settings(
            conn, args.run_id, chunk_kind=args.chunk_kind, chunk_multiplier=args.chunk_multiplier,
            user=rt._current_user(),
        )
    print(f"{args.run_id}: chunk size updated for the remaining (not-yet-run) part of the run")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    user = rt._current_user()
    with rt.connect(args.db) as conn:
        if args.all:
            for r in rt.list_runs(conn):
                rt.set_control(conn, r["run_id"], "pause_requested", user=user)
            print("pause requested for all runs")
        else:
            rt.set_control(conn, args.run_id, "pause_requested", user=user)
            print(f"pause requested for {args.run_id!r} (takes effect after the current chunk finishes)")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    user = rt._current_user()
    with rt.connect(args.db) as conn:
        if args.all:
            for r in rt.list_runs(conn):
                rt.set_control(conn, r["run_id"], "run", user=user)
            print("resumed all runs")
        else:
            rt.set_control(conn, args.run_id, "run", user=user)
            print(f"resumed {args.run_id!r}")
    return 0


def cmd_delay_all(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        path = rt.chunk_delay_sentinel_path(conn)
        if args.clear:
            Path(path).unlink(missing_ok=True)
            print(f"cleared {path} -- resubmissions proceed immediately again")
            return 0
        Path(path).write_text(str(args.seconds))
        mins = args.seconds / 60
        print(f"wrote {path} ({args.seconds}s) -- any chunk/run about to be submitted "
              f"in the next ~{mins:.0f} min will wait out the remainder first, then "
              f"proceed automatically. A chunk already RUNNING is never interrupted -- "
              f"this only delays the hand-off to the next one.")
    return 0


def cmd_rerun(args: argparse.Namespace) -> int:
    if args.from_scratch:
        chunk_index = 0
    elif args.from_chunk is not None:
        chunk_index = args.from_chunk
    else:
        chunk_index = None  # "from the present chunk"
    with rt.connect(args.db) as conn:
        n = rt.rerun_from(conn, args.run_id, chunk_index=chunk_index, user=rt._current_user(), note=args.note)
    print(f"{args.run_id}: dropped {n} chunk record(s) -- next submission redoes from there")
    return 0


def _add_common(sp: argparse.ArgumentParser) -> None:
    """Give a subparser its own --db/--dry-run so they work AFTER the
    subcommand too (e.g. `oceanicu_runs.py list --db X`), not just before.

    default=SUPPRESS is required, not just default=None/False: argparse
    parses each subparser into a fresh namespace and then unconditionally
    copies every key from it onto the parent namespace, so a plain default
    here would silently clobber a real --db/--dry-run value already given
    BEFORE the subcommand whenever it isn't repeated after. SUPPRESS makes
    argparse omit the key entirely when the flag isn't present in the
    subcommand's own args, so the parent's value survives untouched.
    """
    sp.add_argument("--db", default=argparse.SUPPRESS, help="override the SQLite registry path")
    sp.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                    help="copy the real registry to a scratch file in /tmp, run the command "
                         "against THAT (a real execution, not a simulated one), report what "
                         "changed, and leave the resulting file for inspection. The real "
                         "registry is never opened for writing.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=None, help="override the SQLite registry path")
    p.add_argument("--dry-run", action="store_true",
                    help="copy the real registry to a scratch file in /tmp, run the command "
                         "against THAT (a real execution, not a simulated one), report what "
                         "changed, and leave the resulting file for inspection. The real "
                         "registry is never opened for writing.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add"); _add_common(a); a.set_defaults(func=cmd_add)
    a.add_argument("--run-id", required=True)
    a.add_argument("--run-root", required=True,
                    help="absolute or relative -- a relative run-root is resolved against "
                         "OCEANICU_RUN_ROOT_BASE on whichever machine actually touches this "
                         "run's files (see RUN_TRACKING.md), so you don't need to know the "
                         "production path when adding from elsewhere")
    a.add_argument("--script", required=True)
    a.add_argument("--config", required=True)
    a.add_argument("--initial-date", required=True, metavar="YYYY-MM-DD")
    a.add_argument("--stop-date", required=True, metavar="YYYY-MM-DD")
    a.add_argument("--data-roots-file", default=None)
    a.add_argument("--chunk-kind", default="annual", choices=["annual", "monthly", "daily"])
    a.add_argument("--chunk-multiplier", type=int, default=1)
    a.add_argument("--np", type=int, default=1)
    a.add_argument("--launcher", default="srun", choices=["srun", "mpiexec"])
    a.add_argument("--priority", type=int, default=0)
    a.add_argument("--chunk-delay-seconds", type=int, default=0,
                    help="wait this many seconds before EACH future resubmission of this "
                         "run's own chunks, or before it's picked up as the next queued run "
                         "(default: 0, no delay). Persistent, not one-shot -- changeable "
                         "later with set-chunk-delay. Different from delay-all, which is a "
                         "global, one-shot TIMED pause, not tied to one run.")
    a.add_argument("--notes", default=None)
    # Mirrors the generated driver script's own --fabm/--no-fabm exactly
    # (see pygetm_config.codegen's _emit_argparse) -- None here means "no
    # override, run the script's own baked-in FABM setting unchanged";
    # 'off'/'on' are sentinels for bare --no-fabm/--fabm; anything else is
    # an explicit fabm.yaml path. chunk_runner.py passes this straight
    # through to the driver's own --fabm/--no-fabm.
    a.add_argument("--fabm", nargs="?", const="on", default=None, metavar="PATH",
                    help="override the run's FABM state at chunk-run time (bare --fabm "
                         "reuses the script's configured path; --fabm PATH forces a "
                         "specific one; default: don't override, use whatever the "
                         "script was generated with)")
    a.add_argument("--no-fabm", dest="fabm", action="store_const", const="off",
                    help="force FABM off at chunk-run time, regardless of the script's own setting")

    r = sub.add_parser("remove"); _add_common(r); r.set_defaults(func=cmd_remove)
    r.add_argument("--run-id", required=True)
    r.add_argument("--force", action="store_true")

    l = sub.add_parser("list"); _add_common(l); l.set_defaults(func=cmd_list)
    l.add_argument("--status", default=None, choices=list(rt.RUN_STATUSES))
    l.add_argument("--like", default=None, help="substring filter on run_id")

    s = sub.add_parser("show"); _add_common(s); s.set_defaults(func=cmd_show)
    s.add_argument("--run-id", required=True)

    c = sub.add_parser("chunk-size"); _add_common(c); c.set_defaults(func=cmd_chunk_size)
    c.add_argument("--run-id", required=True)
    c.add_argument("--chunk-kind", default=None, choices=["annual", "monthly", "daily"])
    c.add_argument("--chunk-multiplier", type=int, default=None)

    sp = sub.add_parser("set-priority"); _add_common(sp); sp.set_defaults(func=cmd_set_priority)
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--priority", type=int, required=True)

    scd = sub.add_parser(
        "set-chunk-delay",
        help="persistent, per-run pacing -- wait N seconds before EACH future resubmission "
             "of this run's own chunks (0 = no delay, the default). Different from "
             "delay-all, which is a global, one-shot TIMED pause covering every run.",
    )
    _add_common(scd); scd.set_defaults(func=cmd_set_chunk_delay)
    scd.add_argument("--run-id", required=True)
    scd.add_argument("--seconds", type=int, required=True, metavar="N")

    sd = sub.add_parser("set-stop-date"); _add_common(sd); sd.set_defaults(func=cmd_set_stop_date)
    sd.add_argument("--run-id", required=True)
    sd.add_argument("--stop-date", required=True, metavar="YYYY-MM-DD")

    pa = sub.add_parser("pause"); _add_common(pa); pa.set_defaults(func=cmd_pause)
    g1 = pa.add_mutually_exclusive_group(required=True)
    g1.add_argument("--run-id")
    g1.add_argument("--all", action="store_true")

    re_ = sub.add_parser("resume"); _add_common(re_); re_.set_defaults(func=cmd_resume)
    g2 = re_.add_mutually_exclusive_group(required=True)
    g2.add_argument("--run-id")
    g2.add_argument("--all", action="store_true")

    da = sub.add_parser(
        "delay-all",
        help="pause the hand-off before the next chunk/run submission for N seconds, "
             "then resume automatically -- e.g. the HPC is needed for something else "
             "for a while. Unlike pause/resume, a chunk already running is never "
             "affected, and nothing needs to be manually resumed afterward.",
    )
    _add_common(da)
    da.set_defaults(func=cmd_delay_all)
    g4 = da.add_mutually_exclusive_group(required=True)
    g4.add_argument("--seconds", type=int, metavar="N",
                     help="wait this many seconds (from now) before the next "
                          "submission proceeds; live-adjustable at any time by "
                          "running this again with a new value")
    g4.add_argument("--clear", action="store_true",
                     help="cancel any pending delay -- submissions proceed immediately again")

    rr = sub.add_parser("rerun"); _add_common(rr); rr.set_defaults(func=cmd_rerun)
    rr.add_argument("--run-id", required=True)
    rr.add_argument("--note", default=None,
                     help="optional free-text reason, recorded in the history log alongside "
                          "this rerun (e.g. 'fixed off-by-one in river forcing script') -- "
                          "complements the automatic script_changed/config_changed detection "
                          "(see chunk_runner.py), which shows THAT something changed; this is "
                          "for saying WHY.")
    g3 = rr.add_mutually_exclusive_group()
    g3.add_argument("--from-chunk", type=int, default=None, metavar="N")
    g3.add_argument("--from-current", action="store_true")
    g3.add_argument("--from-scratch", action="store_true")

    args = p.parse_args()

    if not args.dry_run:
        try:
            return args.func(args)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    # --- dry run: copy real DB -> scratch, run the REAL command against
    # the copy, report what changed, never open the real DB for writing.
    # No --db/OCEANICU_RUN_DB configured at all is NOT an error here (same
    # philosophy as chunk_runner.py's standalone mode) -- dry-run is for
    # exploring/testing, so it just starts completely empty instead of
    # copying anything.
    try:
        real_db = _resolve_real_db_path(args)
    except RuntimeError:
        real_db = None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    scratch_db = Path(f"/tmp/oceanicu_runs_dryrun_{args.cmd}_{stamp}.sqlite")
    if real_db is None:
        print(f"[dry-run] no --db/OCEANICU_RUN_DB configured -- "
              f"starting {scratch_db} completely empty")
    else:
        _pull_snapshot_copy(real_db, scratch_db)
        if scratch_db.exists():
            print(f"[dry-run] copied real registry ({real_db}) -> {scratch_db}")
        else:
            print(f"[dry-run] real registry ({real_db}) doesn't exist yet -- "
                  f"starting {scratch_db} empty")

    before = _snapshot(scratch_db)
    args.db = scratch_db  # redirect the command at the scratch copy only

    print(f"[dry-run] running: {args.cmd} ...")
    try:
        rc = args.func(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    after = _snapshot(scratch_db)
    print()
    print(f"[dry-run] result (command exit code {rc}):")
    _print_diff(before, after)

    # Show what run_chunk.slurm would actually submit next, for whichever
    # run(s) this command named -- the registry diff above only says what
    # changed in the table; this says what that change actually causes to
    # run. --all commands (pause/resume) preview every run currently
    # not_started/in_progress rather than just one.
    run_ids: list[str] = []
    if getattr(args, "run_id", None):
        run_ids = [args.run_id]
    elif getattr(args, "all", False):
        run_ids = [r["run_id"] for r in after["runs"] if r["status"] in ("not_started", "in_progress")]
    print()
    for rid in run_ids:
        _preview_chunk_runner(scratch_db, rid)

    print()
    print(f"[dry-run] real registry was never opened for writing.")
    print(f"[dry-run] resulting DB left at {scratch_db} for inspection:")
    print(f"[dry-run]   sqlite3 {scratch_db}")
    print(f"[dry-run]   python oceanicu_runs.py --db {scratch_db} list")
    return rc


if __name__ == "__main__":
    sys.exit(main())
