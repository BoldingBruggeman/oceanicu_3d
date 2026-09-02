#!/usr/bin/env python
"""oceanicu_experiments.py -- manage the production-experiment tracking registry.

Use --queue -- this is the default, correct way for almost everybody,
not a fallback for the rare case. Appends the exact same command, same
flags, same validation, to a local YAML file instead of touching a
registry directly -- no network path to anything required:

    oceanicu_experiments.py --queue ~/hpc_commands/queue_<you>.yaml add \\
                             --experiment-id ... --experiment-root ... --script ... --config ...
                             --initial-date 2015-01-01 --stop-date 2099-12-31
                             [--data-roots-file ...] [--chunk-kind annual]
                             [--chunk-multiplier 5] [--np 192] [--priority 0]
    oceanicu_experiments.py --queue ~/hpc_commands/queue_<you>.yaml <any write command below>

    # stage a new experiment's own files (filtered by --include, default
    # generated*.py/generated*.yaml) directly into its real, resolved
    # experiment_root (OCEANICU_EXPERIMENT_ROOT_BASE) -- doesn't touch
    # any registry, no --db needed:
    oceanicu_experiments.py stage --experiment-root ... --source-dir /wherever/you/generated/it

That queue directory is plain data, deliberately NOT part of this git
repo -- see EXPERIMENT_TRACKING.md "Command queue" for where it actually lives
and how it reaches the registry from there.

list/show are read-only and safe to point at a read-only mirror (e.g.
bb-server1) if that's all you can reach -- you just might be looking at
a slightly stale snapshot, not live state:

    oceanicu_experiments.py list   [--status in_progress] [--like MPI-ESM1-2-HR]
    oceanicu_experiments.py show   --experiment-id ...              # experiment + full chunk history

Every WRITE command below also works without --queue, but only if you
have an actual, live path to the AUTHORITATIVE registry -- the
production machine itself, or the ssh:// relay (see EXPERIMENT_TRACKING.md
"Working across machines"). A read-only mirror does NOT count for these:
pointing --db at one "succeeds" with no warning, writes into a copy with
no effect on the real thing, and vanishes next time a real push
overwrites it. On this project's actual HPC, nobody runs these directly
by hand at all -- see EXPERIMENT_TRACKING.md "Set up an experiment" before using any of
these without --queue:

    oceanicu_experiments.py add    --experiment-id ... --experiment-root ... --script ... --config ...
                             --initial-date 2015-01-01 --stop-date 2099-12-31
                             [--data-roots-file ...] [--chunk-kind annual]
                             [--chunk-multiplier 5] [--np 192] [--priority 0]
    oceanicu_experiments.py remove --experiment-id ... [--force]
    oceanicu_experiments.py chunk-size --experiment-id ... --chunk-kind ... --chunk-multiplier ...
    oceanicu_experiments.py set-chunk-delay --experiment-id ... --seconds N   # persistent, per-experiment pacing
    oceanicu_experiments.py set-data-roots-file --experiment-id ... --path ...
    oceanicu_experiments.py set-np --experiment-id ... --np ...
    oceanicu_experiments.py set-launcher --experiment-id ... --launcher srun|mpiexec
    oceanicu_experiments.py set-notes --experiment-id ... --notes ...
    oceanicu_experiments.py pause  --experiment-id ... | --all
    oceanicu_experiments.py resume --experiment-id ... | --all
    oceanicu_experiments.py kill   --experiment-id ...   # scancel the running chunk now, unlike pause
    oceanicu_experiments.py delay-all --seconds N | --clear
    oceanicu_experiments.py rerun  --experiment-id ... [--from-chunk N | --from-current | --from-scratch] [--note ...]

    # preview ANY of the above for real, against a scratch copy in /tmp,
    # without ever writing to the configured registry:
    oceanicu_experiments.py --dry-run <any command above>

pause/resume set the DB `control` column -- the normal, auditable way.
For a genuine HPC-overload emergency, `touch <experiment_root>/PAUSE` (one experiment)
works even if this tool or the DB itself is unreachable; for pausing
everything, see experiment_tracking.pause_all_sentinel_path (its location is
derived from wherever the registry DB actually lives, not a fixed path).
Either mechanism takes effect only between chunks, never mid-chunk -- a
currently-running chunk always finishes cleanly first, and needs a manual
resume to lift.

delay-all is different: a TIMED pause on an already-running system ("the
HPC needs to be used for something else for a while") -- the next
submission waits out the remainder then proceeds automatically, no manual
resume needed, and it's live-adjustable at any time by running `delay-all
--seconds N` again with a new value (see experiment_tracking.
chunk_delay_sentinel_path for the raw file, if this tool itself is
unreachable).
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

import experiment_tracking as rt

# Keys on the parsed argparse.Namespace that are about HOW the command
# runs, never part of the command's own meaning -- never serialized into
# a queued command (see _queue_command/get_commands_and_update_registry.py).
_QUEUE_EXCLUDE_KEYS = {"db", "dry_run", "queue", "cmd", "func"}

_EXPERIMENT_COLUMNS = [
    "experiment_id", "status", "control", "chunk_kind", "chunk_multiplier",
    "initial_date", "stop_date", "priority", "chunk_delay_seconds",
]


def _cell(r, c: str):
    """r[c], tolerating a column that genuinely doesn't exist on this
    particular row -- e.g. a chunk fetched from an as-yet-unmigrated
    (read-only mirror) registry that predates a newer column like
    last_health_check. dict raises KeyError for a missing key,
    sqlite3.Row raises IndexError for the same thing -- catch both
    rather than assuming which container type r is. '' (not None) so
    the column still renders as an empty cell, not the literal string
    'None'."""
    try:
        return r[c]
    except (KeyError, IndexError):
        return ""


def _print_table(rows: list, columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = {c: max(len(c), *(len(str(_cell(r, c))) for r in rows)) for c in columns}
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  ".join(str(_cell(r, c)).ljust(widths[c]) for c in columns))


def _resolve_real_db_path(args: argparse.Namespace):
    real = args.db or os.environ.get("OCEANICU_EXPERIMENT_DB")
    if not real:
        raise RuntimeError("No database path given: pass --db, or set OCEANICU_EXPERIMENT_DB.")
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
    experiment_tracking_server.py itself needs it -- not the separate `sqlite3`
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
    """Full state worth comparing before/after: every experiment row, plus (for
    whichever experiment_id this invocation names, if any) its full chunk
    history -- cheap enough to just always capture both in full rather
    than trying to guess which command touches what. Works uniformly
    whether *db_path* exists yet or not -- connect() creates an empty
    schema either way, so an as-yet-nonexistent scratch DB just reads
    back as "no experiments", no special-casing needed."""
    with rt.connect(db_path) as conn:
        experiments = [dict(r) for r in rt.list_experiments(conn)]
        chunks = {r["experiment_id"]: [dict(c) for c in rt.list_chunks(conn, r["experiment_id"])] for r in experiments}
        history = {r["experiment_id"]: [dict(h) for h in rt.list_history(conn, r["experiment_id"])] for r in experiments}
    return {"experiments": experiments, "chunks": chunks, "history": history}


def _print_diff(before: dict, after: dict) -> None:
    before_experiments = {r["experiment_id"]: r for r in before["experiments"]}
    after_experiments = {r["experiment_id"]: r for r in after["experiments"]}

    added = after_experiments.keys() - before_experiments.keys()
    removed = before_experiments.keys() - after_experiments.keys()
    changed = {
        rid for rid in (after_experiments.keys() & before_experiments.keys())
        if before_experiments[rid] != after_experiments[rid]
    }

    if not (added or removed or changed):
        print("No change to any experiment row.")
    for rid in sorted(added):
        print(f"+ {rid}: NEW")
        _print_table([after_experiments[rid]], _EXPERIMENT_COLUMNS)
    for rid in sorted(removed):
        print(f"- {rid}: REMOVED")
    for rid in sorted(changed):
        print(f"~ {rid}:")
        for key in _EXPERIMENT_COLUMNS:
            if before_experiments[rid][key] != after_experiments[rid][key]:
                print(f"    {key}: {before_experiments[rid][key]!r} -> {after_experiments[rid][key]!r}")

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


def _preview_chunk_runner(scratch_db: Path, experiment_id: str) -> None:
    """What run_chunk.slurm's own chunk_runner.py call would actually do
    next for this experiment, given the (dry-run) registry state -- resolved
    dates, chunk directory, load/save-restart paths, the real launch
    command -- by really invoking chunk_runner.py --dry-run against the
    same scratch copy, not re-deriving/guessing the logic separately."""
    script = Path(__file__).resolve().parent / "chunk_runner.py"
    print(f"[dry-run] what run_chunk.slurm would do next for {experiment_id!r}:")
    result = subprocess.run(
        [sys.executable, str(script), "--db", str(scratch_db), "--experiment-id", experiment_id, "--dry-run"],
        capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    if "OCEANICU_EXPERIMENT_ROOT_BASE is not set" in output:
        print(f"    -- can't preview: experiment_root is relative and this machine has no "
              f"OCEANICU_EXPERIMENT_ROOT_BASE set. NOT a real problem -- this is the same known "
              f"limitation as the PAUSE-file check (see EXPERIMENT_TRACKING.md's \"One real "
              f"limitation\" note): resolved on whichever machine actually runs the chunk, "
              f"once that machine's OCEANICU_EXPERIMENT_ROOT_BASE is exported there. --")
        return
    for line in output.splitlines():
        print(f"    {line}")


def _queue_command(queue_path: Path, args: argparse.Namespace) -> int:
    """Append this command to a command-queue YAML file instead of
    touching a real registry at all -- for a registry with no network
    path to it (see EXPERIMENT_TRACKING.md "Command queue").
    get_commands_and_update_registry.py, run wherever the registry actually
    lives, later replays each pending
    entry through this exact same CLI (reconstructed from the stored
    args, as real --flag values) -- so queuing and running directly go
    through identical validation/behavior, nothing duplicated here."""
    if args.cmd in ("list", "show", "stage"):
        print(f"ERROR: {args.cmd!r} doesn't touch a registry -- nothing to queue.", file=sys.stderr)
        return 1

    queue_path = Path(queue_path)
    if queue_path.exists():
        data = yaml.safe_load(queue_path.read_text()) or {}
    else:
        data = {}
    # Tolerate a bare list (the pre-wrapper format some older/hand-made
    # queue files still use) instead of crashing on .setdefault -- see
    # the matching normalization in get_commands_and_update_registry.py.
    if isinstance(data, list):
        data = {"commands": data}
    commands = data.setdefault("commands", [])

    call_args = {k: v for k, v in vars(args).items() if k not in _QUEUE_EXCLUDE_KEYS}
    cmd_id = f"cmd-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"
    # Prepended, not appended -- newest entry on top for anyone eyeballing
    # the file by hand. Purely cosmetic: get_commands_and_update_registry.py
    # explicitly sorts by queued_at before applying anything, so this has
    # zero effect on actual processing order.
    commands.insert(0, {
        "id": cmd_id,
        "action": args.cmd,
        "args": call_args,
        "queued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "queued_by": rt._current_user(),
        "status": "pending",
        "applied_at": None,
        "note": None,
    })

    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(yaml.safe_dump(data, sort_keys=False))
    print(f"queued {cmd_id}: {args.cmd} {call_args.get('experiment_id', '')}".rstrip())
    print(f"-- commit and push/rsync {queue_path} for it to actually cross to the registry's "
          f"own machine; nothing has been applied yet.")
    return 0


_STAGE_DEFAULT_INCLUDES = ["generated*.py", "generated*.yaml"]
_STAGE_DEFAULT_EXCLUDE_DIRS = ["__pycache__"]
_STAGE_DEFAULT_EXCLUDE_PATTERNS = ["*.nc"]

_EXPERIMENT_DEFAULTS_PATH = Path(__file__).resolve().parent / "experiment_defaults.yaml"
# Fallback if experiment_defaults.yaml is ever missing (a partial/old
# deployment) -- must match that file's own values exactly. Never a hard
# crash just because this one file didn't make the trip; best-effort.
_EXPERIMENT_DEFAULTS_FALLBACK = {
    "chunk_kind": "annual",
    "chunk_multiplier": 1,
    "np": 1,
    "launcher": "srun",
    "priority": 0,
    "chunk_delay_seconds": 0,
    "data_roots_file": None,
    "notes": None,
    "fabm": None,
}


def _load_experiment_defaults() -> dict:
    """Single source of truth for `add`'s optional-flag defaults -- see
    experiment_defaults.yaml's own header for why it lives where it does."""
    try:
        loaded = yaml.safe_load(_EXPERIMENT_DEFAULTS_PATH.read_text()) or {}
    except OSError:
        loaded = {}
    return {**_EXPERIMENT_DEFAULTS_FALLBACK, **loaded}


_EXPERIMENT_DEFAULTS = _load_experiment_defaults()


def cmd_stage(args: argparse.Namespace) -> int:
    """rsync --source-dir directly into the experiment's REAL destination --
    resolve_experiment_root(experiment_root), i.e. exactly the same path
    chunk_runner.py/is_paused resolve against THIS machine's own
    OCEANICU_EXPERIMENT_ROOT_BASE -- not a separate staging area. Whatever
    machine runs `stage` (workstation, bb-server1) writes to its own local
    copy of that same relative-path tree; getting those files onto the
    HPC's own copy is a separate, filtered sync (see
    bin/pull_experiment_files.sh) using the same --include/--exclude
    pattern as here, kept small deliberately (see EXPERIMENT_TRACKING.md
    "Command queue") -- filtered to only the files that actually matter
    (driver script, utils module, config -- --include patterns, default
    generated*.py/generated*.yaml) -- never the whole directory verbatim,
    since a real experiment_root commonly has __pycache__/, logs, restart
    files, and NetCDF output alongside the 2-3 files it actually needs
    (see this project's own NSe/experiments tree). For a later queued
    `add` to pick up: get_commands_and_update_registry.py verifies these
    files are actually present at the resolved experiment_root before
    registering the run, but doesn't copy them itself -- `stage` already
    wrote them to their real, final location directly. Doesn't touch any
    registry -- pure local file staging, --db/--dry-run/--queue don't
    apply here.

    rsync, not shutil: this is filtered by pattern, not by exact
    filename, so an include/exclude filter (rsync's own well-tested
    syntax) is the right tool -- not reinventing it with fnmatch/glob.

    --exclude (default *.nc) is a belt-and-braces guard, not the actual
    mechanism keeping output data out -- the default --include list is
    already a whitelist (generated*.py/generated*.yaml only), so *.nc
    never matches it and is already excluded by the trailing catch-all
    below. This exists for the case where --include is later widened
    (e.g. to *) and this experiment_root already has real NetCDF output
    sitting right next to its driver script (a real risk here, since
    stage's destination now IS the real, eventually-populated experiment
    directory, not a separate empty staging area) -- rsync evaluates
    filter rules in order and stops at the first match, so putting this
    exclude BEFORE the include patterns means it always wins regardless
    of what --include ends up being, not just under the current
    defaults."""
    source_dir = Path(args.source_dir)
    if not source_dir.is_dir():
        print(f"ERROR: {source_dir}: not a directory", file=sys.stderr)
        return 1

    dest_dir = Path(rt.resolve_experiment_root(args.experiment_root))
    dest_dir.mkdir(parents=True, exist_ok=True)

    includes = args.include or _STAGE_DEFAULT_INCLUDES
    cmd = ["rsync", "-a", "--prune-empty-dirs"]
    for name in args.exclude_dir:
        cmd += ["--exclude", f"{name}/"]
    for pattern in args.exclude:
        cmd += ["--exclude", pattern]
    for pattern in includes:
        cmd += ["--include", pattern]
    # --include='*/' lets rsync descend into subdirectories to look for
    # matches (otherwise a bare --exclude='*' below would stop it from
    # even entering them); --prune-empty-dirs above then drops any
    # subdirectory that ends up with nothing matched inside it, so this
    # doesn't create a hollow directory tree in the destination.
    cmd += ["--include", "*/", "--exclude", "*"]
    cmd += [f"{source_dir}/", f"{dest_dir}/"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: rsync failed: {result.stderr.strip()}", file=sys.stderr)
        return 1

    staged = sorted(p for p in dest_dir.rglob("*") if p.is_file())
    if not staged:
        print(f"WARNING: nothing matched {includes} under {source_dir} -- "
              f"{dest_dir} is empty.", file=sys.stderr)
        return 1
    for p in staged:
        print(f"staged {p.relative_to(dest_dir)}")
    return 0


def _refuse_direct_add_unless_hpc(args: argparse.Namespace) -> "str | None":
    """A direct (non---queue) `add` against the real path is exactly the
    footgun documented in "Set up an experiment": bb-server1's mirror copy sits
    at the SAME path string as the authoritative registry on the HPC
    (deliberately, so push_registry_snapshot.sh needs no path
    translation), so a path-based check alone can't tell them apart --
    only the machine differs. OCEANICU_HPC=1 (set only by
    setup_experiment_tracking.sh's hpc role) is the actual signal. Returns an
    error message to print and abort on, or None to proceed.

    Exemptions, both legitimate and never the real mirror: --dry-run
    (already redirected to a throwaway scratch copy before this ever
    runs) and an obvious /tmp/ scratch path (this session's own, and
    EXPERIMENT_TRACKING.md's documented, testing convention -- the real
    registry is never there)."""
    if args.dry_run:
        return None
    db = args.db or os.environ.get("OCEANICU_EXPERIMENT_DB") or ""
    if db.startswith("/tmp/"):
        return None
    if os.environ.get("OCEANICU_HPC") == "1":
        return None
    return (
        "refusing to add directly -- this doesn't look like the HPC "
        "(OCEANICU_HPC is not set) and the target isn't an obvious scratch "
        "path. Use --queue instead (the default, correct way -- see "
        "EXPERIMENT_TRACKING.md \"Set up an experiment\"), or export OCEANICU_HPC=1 if this "
        "really is the machine that owns the authoritative registry."
    )


def cmd_add(args: argparse.Namespace) -> int:
    refusal = _refuse_direct_add_unless_hpc(args)
    if refusal:
        print(f"ERROR: {refusal}", file=sys.stderr)
        return 1
    with rt.connect(args.db) as conn:
        rt.add_experiment(
            conn, experiment_id=args.experiment_id, experiment_root=args.experiment_root, script=args.script,
            config=args.config, initial_date=args.initial_date, stop_date=args.stop_date,
            data_roots_file=args.data_roots_file, chunk_kind=args.chunk_kind,
            chunk_multiplier=args.chunk_multiplier, np=args.np, launcher=args.launcher,
            priority=args.priority, notes=args.notes, fabm=args.fabm,
            chunk_delay_seconds=args.chunk_delay_seconds, user=rt._current_user(),
        )
    print(f"added {args.experiment_id!r}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        try:
            rt.remove_experiment(conn, args.experiment_id, force=args.force, user=rt._current_user())
        except (KeyError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    print(f"removed {args.experiment_id!r} from the registry (files on disk untouched)")
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    """scancel the actually-running SLURM job for an experiment's current
    chunk, then mark that chunk failed -- for exactly the case `pause`
    doesn't cover: pause only takes effect at the NEXT chunk boundary, it
    never interrupts a chunk already running, so a paused experiment can
    still show status=running (correctly!) for a long time. This is the
    tool for "no, stop it now" without removing the experiment from the
    registry the way `remove` does."""
    user = rt._current_user()
    with rt.connect(args.db) as conn:
        running = rt.get_running_chunk(conn, args.experiment_id)
        if running is None:
            print(f"ERROR: no chunk currently marked running for {args.experiment_id!r} -- nothing to kill.",
                  file=sys.stderr)
            return 1
        chunk_index = running["chunk_index"]
        job_id = running["slurm_job_id"]
        if not job_id:
            print(f"ERROR: chunk {chunk_index} for {args.experiment_id!r} has no recorded slurm_job_id -- "
                  f"can't scancel it. (Use 'remove --force' instead if you just want the DB row gone.)",
                  file=sys.stderr)
            return 1
        ok, msg = rt.cancel_slurm_job(job_id)
        if ok:
            print(f"scancel {job_id}: {msg}")
        else:
            print(f"WARNING: scancel {job_id} failed ({msg}) -- job may already be gone; "
                  f"marking the chunk failed in the DB anyway.", file=sys.stderr)
        rt.finish_chunk(conn, experiment_id=args.experiment_id, chunk_index=chunk_index,
                         exit_code=-1, nan_detected=False, user=user)
    print(f"{args.experiment_id}: chunk {chunk_index} (SLURM job {job_id}) killed and marked failed. "
          f"Use 'rerun --from-current' (or --from-chunk/--from-scratch) to redo it once ready.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rows = rt.list_experiments(conn, status=args.status)
        if args.like:
            rows = [r for r in rows if args.like in r["experiment_id"]]
        _print_table(rows, _EXPERIMENT_COLUMNS)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        experiment = rt.get_experiment(conn, args.experiment_id)
        if experiment is None:
            print(f"ERROR: no such experiment_id: {args.experiment_id!r}", file=sys.stderr)
            return 1
        print("experiment:")
        _print_table([experiment], _EXPERIMENT_COLUMNS + ["experiment_root", "script", "config", "np", "launcher", "fabm", "data_roots_file", "notes"])
        print()
        print("chunks:")
        chunks = rt.list_chunks(conn, args.experiment_id)
        # Full sha256 is 64 hex chars -- far too wide for this table, and a
        # short prefix is all a human needs to eyeball "did this change
        # between chunks" (experiment_tracking.start_chunk's own script_changed/
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
             "script_sha256", "config_sha256", "slurm_job_id", "submitted_host",
             "start_time", "end_time", "last_health_check"],
        )
        print()
        print("history:")
        history = rt.list_history(conn, args.experiment_id)
        _print_table(history, ["timestamp", "user", "event", "detail"])
    return 0


def cmd_set_priority(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_priority(conn, args.experiment_id, args.priority, user=rt._current_user())
    print(f"{args.experiment_id}: priority set to {args.priority}")
    return 0


def cmd_set_data_roots_file(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_data_roots_file(conn, args.experiment_id, args.path, user=rt._current_user())
    print(f"{args.experiment_id}: data_roots_file set to {args.path!r}")
    return 0


def cmd_set_np(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_np(conn, args.experiment_id, args.np, user=rt._current_user())
    print(f"{args.experiment_id}: np set to {args.np}")
    return 0


def cmd_set_launcher(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_launcher(conn, args.experiment_id, args.launcher, user=rt._current_user())
    print(f"{args.experiment_id}: launcher set to {args.launcher!r}")
    return 0


def cmd_set_notes(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_notes(conn, args.experiment_id, args.notes, user=rt._current_user())
    print(f"{args.experiment_id}: notes set to {args.notes!r}")
    return 0


def cmd_set_chunk_delay(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_chunk_delay(conn, args.experiment_id, args.seconds, user=rt._current_user())
    print(f"{args.experiment_id}: chunk_delay_seconds set to {args.seconds} "
          f"(takes effect on the next chunk/resubmission)")
    return 0


def cmd_set_stop_date(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_stop_date(conn, args.experiment_id, args.stop_date, user=rt._current_user())
    print(f"{args.experiment_id}: stop_date set to {args.stop_date} (takes effect on the next chunk)")
    return 0


def cmd_chunk_size(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_chunk_settings(
            conn, args.experiment_id, chunk_kind=args.chunk_kind, chunk_multiplier=args.chunk_multiplier,
            user=rt._current_user(),
        )
    print(f"{args.experiment_id}: chunk size updated for the remaining (not-yet-run) part of the experiment")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    user = rt._current_user()
    with rt.connect(args.db) as conn:
        if args.all:
            for r in rt.list_experiments(conn):
                rt.set_control(conn, r["experiment_id"], "pause_requested", user=user)
            print("pause requested for all experiments")
        else:
            rt.set_control(conn, args.experiment_id, "pause_requested", user=user)
            print(f"pause requested for {args.experiment_id!r} (takes effect after the current chunk finishes)")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    user = rt._current_user()
    with rt.connect(args.db) as conn:
        if args.all:
            for r in rt.list_experiments(conn):
                rt.set_control(conn, r["experiment_id"], "run", user=user)
            print("resumed all experiments")
        else:
            rt.set_control(conn, args.experiment_id, "run", user=user)
            print(f"resumed {args.experiment_id!r}")
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
        print(f"wrote {path} ({args.seconds}s) -- any chunk/experiment about to be submitted "
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
        n = rt.rerun_from(conn, args.experiment_id, chunk_index=chunk_index, user=rt._current_user(), note=args.note)
    print(f"{args.experiment_id}: dropped {n} chunk record(s) -- next submission redoes from there")
    return 0


def _add_common(sp: argparse.ArgumentParser) -> None:
    """Give a subparser its own --db/--dry-run so they work AFTER the
    subcommand too (e.g. `oceanicu_experiments.py list --db X`), not just before.

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
    sp.add_argument("--queue", default=argparse.SUPPRESS, metavar="PATH",
                    help="append this command to a command-queue YAML file instead of "
                         "touching a real registry at all -- see EXPERIMENT_TRACKING.md "
                         "\"Command queue\"")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=None, help="override the SQLite registry path")
    p.add_argument("--dry-run", action="store_true",
                    help="copy the real registry to a scratch file in /tmp, run the command "
                         "against THAT (a real execution, not a simulated one), report what "
                         "changed, and leave the resulting file for inspection. The real "
                         "registry is never opened for writing.")
    p.add_argument("--queue", default=None, metavar="PATH",
                    help="append this command to a command-queue YAML file instead of "
                         "touching a real registry at all -- see EXPERIMENT_TRACKING.md "
                         "\"Command queue\"")
    sub = p.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser(
        "stage",
        help="rsync (filtered by --include, default generated*.py/generated*.yaml) an "
             "experiment's generated files directly into its real, resolved experiment_root "
             "(see OCEANICU_EXPERIMENT_ROOT_BASE), for a later --queue'd add to pick up "
             "(see EXPERIMENT_TRACKING.md \"Command queue\") -- doesn't touch any registry, no "
             "--db needed",
    )
    st.set_defaults(func=cmd_stage)
    st.add_argument("--experiment-root", required=True,
                    help="resolved the same way as `add`'s own --experiment-root -- a relative "
                         "one is resolved against THIS machine's own OCEANICU_EXPERIMENT_ROOT_BASE, "
                         "since that's where stage actually writes the files")
    st.add_argument("--source-dir", required=True, metavar="PATH",
                    help="directory containing the experiment's generated files -- only files "
                         "matching --include are actually copied, so real clutter "
                         "alongside them (__pycache__, logs, ...) is left behind")
    st.add_argument("--include", action="append", default=None, metavar="PATTERN",
                    help="rsync include pattern, repeatable (default: generated*.py, generated*.yaml)")
    st.add_argument("--exclude-dir", action="append", default=list(_STAGE_DEFAULT_EXCLUDE_DIRS),
                    metavar="NAME", help="subdirectory name to exclude entirely, repeatable "
                                          "(default: __pycache__)")
    st.add_argument("--exclude", action="append", default=list(_STAGE_DEFAULT_EXCLUDE_PATTERNS),
                    metavar="PATTERN", help="rsync exclude pattern, repeatable, checked BEFORE "
                                             "--include so it always wins (default: *.nc -- "
                                             "never sweep real output data into the experiment's "
                                             "own directory, even if --include is later widened)")

    a = sub.add_parser("add"); _add_common(a); a.set_defaults(func=cmd_add)
    a.add_argument("--experiment-id", required=True)
    a.add_argument("--experiment-root", required=True,
                    help="absolute or relative -- a relative experiment-root is resolved against "
                         "OCEANICU_EXPERIMENT_ROOT_BASE on whichever machine actually touches this "
                         "experiment's files (see EXPERIMENT_TRACKING.md), so you don't need to know the "
                         "production path when adding from elsewhere")
    a.add_argument("--script", required=True)
    a.add_argument("--config", required=True)
    a.add_argument("--initial-date", required=True, metavar="YYYY-MM-DD")
    a.add_argument("--stop-date", required=True, metavar="YYYY-MM-DD")
    a.add_argument("--data-roots-file", default=_EXPERIMENT_DEFAULTS["data_roots_file"])
    a.add_argument("--chunk-kind", default=_EXPERIMENT_DEFAULTS["chunk_kind"], choices=["annual", "monthly", "daily"])
    a.add_argument("--chunk-multiplier", type=int, default=_EXPERIMENT_DEFAULTS["chunk_multiplier"])
    a.add_argument("--np", type=int, default=_EXPERIMENT_DEFAULTS["np"])
    a.add_argument("--launcher", default=_EXPERIMENT_DEFAULTS["launcher"], choices=["srun", "mpiexec"])
    a.add_argument("--priority", type=int, default=_EXPERIMENT_DEFAULTS["priority"])
    a.add_argument("--chunk-delay-seconds", type=int, default=_EXPERIMENT_DEFAULTS["chunk_delay_seconds"],
                    help="wait this many seconds before EACH future resubmission of this "
                         "experiment's own chunks, or before it's picked up as the next queued "
                         f"experiment (default: {_EXPERIMENT_DEFAULTS['chunk_delay_seconds']}, "
                         "see experiment_defaults.yaml). Persistent, not one-shot -- "
                         "changeable later with set-chunk-delay. Different from delay-all, "
                         "which is a global, one-shot TIMED pause, not tied to one experiment.")
    a.add_argument("--notes", default=_EXPERIMENT_DEFAULTS["notes"])
    # Mirrors the generated driver script's own --fabm/--no-fabm exactly
    # (see pygetm_config.codegen's _emit_argparse) -- None here means "no
    # override, run the script's own baked-in FABM setting unchanged";
    # 'off'/'on' are sentinels for bare --no-fabm/--fabm; anything else is
    # an explicit fabm.yaml path. chunk_runner.py passes this straight
    # through to the driver's own --fabm/--no-fabm.
    a.add_argument("--fabm", nargs="?", const="on", default=_EXPERIMENT_DEFAULTS["fabm"], metavar="PATH",
                    help="override the experiment's FABM state at chunk-run time (bare --fabm "
                         "reuses the script's configured path; --fabm PATH forces a "
                         "specific one; default: don't override, use whatever the "
                         "script was generated with)")
    a.add_argument("--no-fabm", dest="fabm", action="store_const", const="off",
                    help="force FABM off at chunk-run time, regardless of the script's own setting")

    r = sub.add_parser("remove"); _add_common(r); r.set_defaults(func=cmd_remove)
    r.add_argument("--experiment-id", required=True)
    r.add_argument("--force", action="store_true")

    k = sub.add_parser("kill"); _add_common(k); k.set_defaults(func=cmd_kill)
    k.add_argument("--experiment-id", required=True,
                    help="scancel this experiment's currently-running chunk (if any) and mark it "
                         "failed -- unlike pause, takes effect immediately rather than at the next "
                         "chunk boundary; unlike remove, the experiment stays in the registry")

    l = sub.add_parser("list"); _add_common(l); l.set_defaults(func=cmd_list)
    l.add_argument("--status", default=None, choices=list(rt.EXPERIMENT_STATUSES))
    l.add_argument("--like", default=None, help="substring filter on experiment_id")

    s = sub.add_parser("show"); _add_common(s); s.set_defaults(func=cmd_show)
    s.add_argument("--experiment-id", required=True)

    c = sub.add_parser("chunk-size"); _add_common(c); c.set_defaults(func=cmd_chunk_size)
    c.add_argument("--experiment-id", required=True)
    c.add_argument("--chunk-kind", default=None, choices=["annual", "monthly", "daily"])
    c.add_argument("--chunk-multiplier", type=int, default=None)

    sp = sub.add_parser("set-priority"); _add_common(sp); sp.set_defaults(func=cmd_set_priority)
    sp.add_argument("--experiment-id", required=True)
    sp.add_argument("--priority", type=int, required=True)

    scd = sub.add_parser(
        "set-chunk-delay",
        help="persistent, per-experiment pacing -- wait N seconds before EACH future resubmission "
             "of this experiment's own chunks (0 = no delay, the default). Different from "
             "delay-all, which is a global, one-shot TIMED pause covering every experiment.",
    )
    _add_common(scd); scd.set_defaults(func=cmd_set_chunk_delay)
    scd.add_argument("--experiment-id", required=True)
    scd.add_argument("--seconds", type=int, required=True, metavar="N")

    sd = sub.add_parser("set-stop-date"); _add_common(sd); sd.set_defaults(func=cmd_set_stop_date)
    sd.add_argument("--experiment-id", required=True)
    sd.add_argument("--stop-date", required=True, metavar="YYYY-MM-DD")

    sdrf = sub.add_parser(
        "set-data-roots-file",
        help="change which data-roots file this experiment's future chunks use -- same reason "
             "experiment-root can be relative (EXPERIMENT_TRACKING.md): the add-machine doesn't always "
             "know the right one for wherever this ends up actually running, or it can "
             "change over the experiment's lifetime. Takes effect on the next chunk, never "
             "retroactively.",
    )
    _add_common(sdrf); sdrf.set_defaults(func=cmd_set_data_roots_file)
    sdrf.add_argument("--experiment-id", required=True)
    sdrf.add_argument("--path", required=True)

    snp = sub.add_parser(
        "set-np",
        help="change this experiment's process count -- takes effect on the next chunk, never "
             "retroactively (never affects a chunk already running).",
    )
    _add_common(snp); snp.set_defaults(func=cmd_set_np)
    snp.add_argument("--experiment-id", required=True)
    snp.add_argument("--np", type=int, required=True)

    sl = sub.add_parser(
        "set-launcher",
        help="change this experiment's launcher (srun/mpiexec) -- e.g. after registering it "
             "with the wrong one for the machine it's actually going to run on. Takes effect on "
             "the next chunk, never retroactively (never affects a chunk already running).",
    )
    _add_common(sl); sl.set_defaults(func=cmd_set_launcher)
    sl.add_argument("--experiment-id", required=True)
    sl.add_argument("--launcher", required=True, choices=["srun", "mpiexec"])

    sn = sub.add_parser(
        "set-notes",
        help="change this experiment's free-text notes -- e.g. to record why it was paused "
             "or what a rerun fixed, without needing direct DB access.",
    )
    _add_common(sn); sn.set_defaults(func=cmd_set_notes)
    sn.add_argument("--experiment-id", required=True)
    sn.add_argument("--notes", required=True)

    pa = sub.add_parser("pause"); _add_common(pa); pa.set_defaults(func=cmd_pause)
    g1 = pa.add_mutually_exclusive_group(required=True)
    g1.add_argument("--experiment-id")
    g1.add_argument("--all", action="store_true")

    re_ = sub.add_parser("resume"); _add_common(re_); re_.set_defaults(func=cmd_resume)
    g2 = re_.add_mutually_exclusive_group(required=True)
    g2.add_argument("--experiment-id")
    g2.add_argument("--all", action="store_true")

    da = sub.add_parser(
        "delay-all",
        help="pause the hand-off before the next chunk/experiment submission for N seconds, "
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
    rr.add_argument("--experiment-id", required=True)
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

    if args.queue:
        if args.dry_run:
            print("ERROR: --queue and --dry-run don't combine -- queuing never touches a "
                  "real registry to begin with.", file=sys.stderr)
            return 1
        return _queue_command(Path(args.queue), args)

    if not args.dry_run:
        try:
            return args.func(args)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    # --- dry run: copy real DB -> scratch, run the REAL command against
    # the copy, report what changed, never open the real DB for writing.
    # No --db/OCEANICU_EXPERIMENT_DB configured at all is NOT an error here (same
    # philosophy as chunk_runner.py's standalone mode) -- dry-run is for
    # exploring/testing, so it just starts completely empty instead of
    # copying anything.
    try:
        real_db = _resolve_real_db_path(args)
    except RuntimeError:
        real_db = None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    scratch_db = Path(f"/tmp/oceanicu_experiments_dryrun_{args.cmd}_{stamp}.sqlite")
    if real_db is None:
        print(f"[dry-run] no --db/OCEANICU_EXPERIMENT_DB configured -- "
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
    # experiment(s) this command named -- the registry diff above only says what
    # changed in the table; this says what that change actually causes to
    # experiment. --all commands (pause/resume) preview every experiment currently
    # not_started/in_progress rather than just one.
    experiment_ids: list[str] = []
    if getattr(args, "experiment_id", None):
        experiment_ids = [args.experiment_id]
    elif getattr(args, "all", False):
        experiment_ids = [r["experiment_id"] for r in after["experiments"] if r["status"] in ("not_started", "in_progress")]
    print()
    for eid in experiment_ids:
        _preview_chunk_runner(scratch_db, eid)

    print()
    print(f"[dry-run] real registry was never opened for writing.")
    print(f"[dry-run] resulting DB left at {scratch_db} for inspection:")
    print(f"[dry-run]   sqlite3 {scratch_db}")
    print(f"[dry-run]   python oceanicu_experiments.py --db {scratch_db} list")
    return rc


if __name__ == "__main__":
    sys.exit(main())
