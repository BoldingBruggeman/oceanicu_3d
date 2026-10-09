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

    oceanicu_experiments.py list   [--status in_progress] [--like MPI-ESM1-2-HR] [--sort updated_at]
    oceanicu_experiments.py show   --experiment-id ...              # experiment + full chunk history

Every WRITE command below also works without --queue, but only if you
have an actual, live path to the AUTHORITATIVE registry -- the
production machine itself, or the ssh:// relay (see EXPERIMENT_TRACKING.md
"Working across machines"). A read-only mirror does NOT count for these:
pointing --db at one "succeeds" with no warning, writes into a copy with
no effect on the real thing, and vanishes next time a real push
overwrites it. On this project's actual HPC, nobody runs these directly
by hand at all -- see EXPERIMENT_TRACKING.md "Set up an experiment" before using any of
these without --queue. Grouped below by what they actually do, not
alphabetically:

    # Lifecycle -- bring an experiment's files/registry row into or out
    # of existence, or relocate/rename it (see stage, above, for the
    # local-file-only half of this):
    oceanicu_experiments.py add    --experiment-id ... --experiment-root ... --script ... --config ...
                             --initial-date 2015-01-01 --stop-date 2099-12-31
                             [--data-roots-file ...] [--chunk-kind annual]
                             [--chunk-multiplier 5] [--np 192] [--priority 0]
    oceanicu_experiments.py remove --experiment-id ... [--force]
    oceanicu_experiments.py rename --experiment-id ... [--new-experiment-id ...] [--new-experiment-root ...] [--force]
    oceanicu_experiments.py clean  --experiment-id ... [--force]   # rm generated*.py/generated*.yaml at its real experiment_root

    # Start / stop / kill -- whether and when chunks actually run:
    oceanicu_experiments.py submit-chunk --experiment-id ...   # sbatch run_chunk.slurm now; HPC only, never touches the registry
    oceanicu_experiments.py pause  --experiment-id ... | --all
    oceanicu_experiments.py resume --experiment-id ... | --all
    oceanicu_experiments.py kill   --experiment-id ...   # scancel the running chunk now, unlike pause
    oceanicu_experiments.py delay-all --seconds N | --clear

    # Alter the situation -- settings that take effect on the NEXT
    # chunk hand-off, never retroactively:
    oceanicu_experiments.py chunk-size --experiment-id ... --chunk-kind ... --chunk-multiplier ...
    oceanicu_experiments.py set-priority --experiment-id ... --priority ...
    oceanicu_experiments.py set-chunk-delay --experiment-id ... --seconds N   # persistent, per-experiment pacing
    oceanicu_experiments.py set-start-date --experiment-id ... --start-date ...   # only takes effect before chunk 0 runs
    oceanicu_experiments.py set-stop-date --experiment-id ... --stop-date ...
    oceanicu_experiments.py set-data-roots-file --experiment-id ... --path ...
    oceanicu_experiments.py set-np --experiment-id ... --np ...
    oceanicu_experiments.py set-launcher --experiment-id ... --launcher srun|mpiexec
    oceanicu_experiments.py set-notes --experiment-id ... --notes ...

    # Recovery -- rewind an experiment's own progress:
    oceanicu_experiments.py rerun  --experiment-id ... [--from-chunk N | --from-current | --from-scratch] [--note ...] [--force]
    oceanicu_experiments.py reset  --experiment-id ... [--note ...] [--force]   # = rerun --from-scratch, clearer name: back to just-added state

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
import re
import secrets
import shlex
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
    "actual_chunk", "initial_date", "stop_date", "priority", "chunk_delay_seconds", "np",
    "updated_at",
]

# Shorter display labels for `list`'s own printed header row ONLY -- the
# real column names above still drive _cell() lookups and cmd_apply's own
# before/after diff (both keyed off _EXPERIMENT_COLUMNS' real DB column
# names), so this is purely cosmetic, not a rename of anything stored or
# compared. Per user, 2026-09-08.
_EXPERIMENT_COLUMN_LABELS = {
    "chunk_kind": "chunk",
    "chunk_multiplier": "multiplier",
    "actual_chunk": "actual",
    "chunk_delay_seconds": "delay",
    "next_load_restart_time": "pending restart-time",
}


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


def _print_table(rows: list, columns: list[str], labels: dict[str, str] | None = None) -> None:
    labels = labels or {}
    if not rows:
        print("(none)")
        return
    # Width from the DISPLAYED label, not the real column name -- e.g.
    # chunk_delay_seconds (20 chars) shouldn't pad the column out to 20
    # chars just because that's the real name, when the printed header is
    # the much shorter "chunk_delay" (a real bug hit immediately: the
    # first version of this labels= support fixed the header TEXT but left
    # width computed off the old long name, per user, 2026-09-08).
    widths = {c: max(len(labels.get(c, c)), *(len(str(_cell(r, c))) for r in rows)) for c in columns}
    header = "  ".join(labels.get(c, c).ljust(widths[c]) for c in columns)
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
    # Deliberately NOT queue_path.parent.mkdir(...) -- a typo'd/wrong
    # --queue directory (confirmed happening in production, 2026-10-09:
    # .../experiments/hpc_commands/queue_kb_orcal.yaml instead of
    # .../hpc_commands/queue_kb_orca.yaml) would otherwise silently
    # succeed by creating a new directory nothing ever looks at, instead
    # of failing loudly where the typo is actually visible. A new FILE
    # in an EXISTING folder is still fine (see queue_path.write_text
    # below) -- only a missing PARENT folder is refused.
    if not queue_path.parent.is_dir():
        print(
            f"ERROR: {queue_path.parent} does not exist -- refusing to create it "
            f"(check for a typo in --queue). Create the folder yourself first if "
            f"this is genuinely a new location.",
            file=sys.stderr,
        )
        return 1
    if queue_path.exists():
        data = yaml.safe_load(queue_path.read_text()) or {}
    else:
        data = {}
    # Tolerate a bare list (the pre-wrapper format some older/hand-made
    # queue files still use) instead of crashing on .setdefault -- see
    # the matching normalization in get_commands_and_update_registry.py.
    if isinstance(data, list):
        data = {"commands": data}
    # setdefault's default only applies when the KEY is missing -- a
    # hand-edited file with a bare "commands:" (no value) parses as
    # {"commands": None}, and setdefault("commands", []) then returns
    # that existing None right back (confirmed: this is exactly what a
    # real queue_kb.yaml did in production -- AttributeError: 'NoneType'
    # object has no attribute 'insert' on the commands.insert() below).
    commands = data.get("commands")
    if commands is None:
        commands = data["commands"] = []

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

    queue_path.write_text(yaml.safe_dump(data, sort_keys=False))
    print(f"queued {cmd_id}: {args.cmd} {call_args.get('experiment_id', '')}".rstrip())
    print(f"-- commit and push/rsync {queue_path} for it to actually cross to the registry's "
          f"own machine; nothing has been applied yet.")
    return 0


_STAGE_DEFAULT_INCLUDES = ["generated*.py", "generated*.yaml", "gotm.yaml", "fabm*.yaml"]
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
    (driver script, utils module, config, plus the run's own gotm.yaml/
    fabm*.yaml -- see driver/scripts/fabm.py's own comments on why those
    two are bare, cwd-relative filenames rather than resolve_data_path'd
    ${VAR} references, and so need their own real per-run copy -- --include
    patterns, default generated*.py/generated*.yaml/gotm.yaml/fabm*.yaml)
    -- never the whole directory verbatim,
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
    already a whitelist (generated*.py/generated*.yaml/gotm.yaml/fabm*.yaml
    only), so *.nc
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


def cmd_rename(args: argparse.Namespace) -> int:
    """Rename an experiment's experiment_id and/or move its
    experiment_root -- for reusing/relocating an experiment (e.g.
    before `clean` + `stage`-ing a fresh copy of its generated files
    from a remote source) without losing its chunk/history record.

    A --new-experiment-root move is real disk I/O this function does
    itself (rename_experiment's own DB update never touches the
    filesystem -- see its docstring), so it must happen BEFORE the DB
    update, and never at all under --dry-run (the generic scratch-DB
    redirect in main() protects the SQLite side automatically, but does
    nothing for a real `shutil.move` performed here)."""
    if args.new_experiment_id is None and args.new_experiment_root is None:
        print("ERROR: nothing to do -- pass --new-experiment-id and/or --new-experiment-root", file=sys.stderr)
        return 1

    with rt.connect(args.db) as conn:
        row = rt.get_experiment(conn, args.experiment_id)
        if row is None:
            print(f"ERROR: no such experiment_id: {args.experiment_id!r}", file=sys.stderr)
            return 1

        old_root_resolved = None
        new_root_resolved = None
        if args.new_experiment_root is not None:
            old_root_resolved = Path(rt.resolve_experiment_root(row["experiment_root"]))
            new_root_resolved = Path(rt.resolve_experiment_root(args.new_experiment_root))
            if not old_root_resolved.is_dir():
                print(f"ERROR: {old_root_resolved} (current experiment_root, resolved) isn't a directory on "
                      f"this machine -- refusing to guess; run this from the machine that actually holds it.",
                      file=sys.stderr)
                return 1
            if new_root_resolved.exists():
                print(f"ERROR: {new_root_resolved} (new experiment_root, resolved) already exists -- "
                      f"refusing to overwrite it.", file=sys.stderr)
                return 1
            if args.dry_run:
                print(f"[dry-run] would mv {old_root_resolved} -> {new_root_resolved}")
            else:
                new_root_resolved.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_root_resolved), str(new_root_resolved))

        try:
            rt.rename_experiment(
                conn, args.experiment_id,
                new_experiment_id=args.new_experiment_id,
                new_experiment_root=args.new_experiment_root,
                force=args.force, user=rt._current_user(),
            )
        except (KeyError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            if new_root_resolved is not None and not args.dry_run:
                print(f"WARNING: {old_root_resolved} was already moved to {new_root_resolved} before this "
                      f"failed -- the registry still has the OLD experiment_root on record. Move it back, "
                      f"or re-run rename with the same --new-experiment-root to finish the DB update.",
                      file=sys.stderr)
            return 1

    msg = f"renamed {args.experiment_id!r}"
    if args.new_experiment_id:
        msg += f" -> {args.new_experiment_id!r}"
    if args.new_experiment_root:
        msg += f", root moved to {args.new_experiment_root}"
    print(msg)
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    """Remove generated*.py/generated*.yaml at an experiment's real,
    resolved experiment_root -- the exact files/location `stage`
    writes to, so `clean` then `stage` is the intended pair for
    overwriting an experiment's generated files from a fresh remote
    source. Non-recursive, same as stage's own destination."""
    with rt.connect(args.db) as conn:
        row = rt.get_experiment(conn, args.experiment_id)
        if row is None:
            print(f"ERROR: no such experiment_id: {args.experiment_id!r}", file=sys.stderr)
            return 1
        if row["status"] == "in_progress" and not args.force:
            print(f"ERROR: {args.experiment_id!r} is in_progress -- pause it first, or pass --force "
                  f"if you're sure.", file=sys.stderr)
            return 1

        root = Path(rt.resolve_experiment_root(row["experiment_root"]))
        matches = sorted(root.glob("generated*.py")) + sorted(root.glob("generated*.yaml"))
        if not matches:
            print(f"no generated*.py/generated*.yaml files under {root} -- nothing to clean")
            return 0

        for f in matches:
            if args.dry_run:
                print(f"[dry-run] would remove {f}")
            else:
                f.unlink()
                print(f"removed {f}")

        if not args.dry_run:
            rt.log_cleaned(conn, args.experiment_id, [f.name for f in matches], user=rt._current_user())
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


def cmd_submit_chunk(args: argparse.Namespace) -> int:
    """Directly `sbatch run_chunk.slurm` for an ALREADY-REGISTERED
    experiment -- the one deliberate exception to get_commands_and_
    update_registry.py's own rule ("never calls sbatch, submitting a
    job is always a deliberate manual action"). For occasional manual
    use (e.g. queued from orca/bb-server1 via --queue when nobody's
    logged into HPC directly to run sbatch by hand) -- NOT a replacement
    for the routine self-resubmission chain, which stays exactly as
    automatic as before.

    Deliberately never touches the registry at all -- no rt.connect(),
    no DB read/write of any kind. Just `cd running/ && sbatch
    --export=ALL,EXPERIMENT_ID=...`, mirroring run_chunk.slurm's own
    self-resubmission call exactly (same ALL, prefix for the same
    reason -- see its own comment on job 11233's HOME-dropping
    incident). This means it's safe to run from ANY machine's own
    oceanicu-experiments (including via --queue, where get_commands_and_
    update_registry.py's generic subprocess dispatch already handles
    any subcommand uniformly -- no special-casing needed there), not
    just the one that holds the real DB -- but the ACTUAL sbatch call
    only ever succeeds on a machine that's actually part of the SLURM
    cluster (the HPC) with run_chunk.slurm physically deployed there,
    same as every other SLURM-only command in this file.

    Does NOT accept --db / doesn't override OCEANICU_EXPERIMENT_DB --
    the submitted job inherits whatever's already in ITS OWN
    environment (ALL, prefix), exactly matching how run_chunk.slurm's
    own self-resubmission never re-specifies it either. Set up once via
    the HPC's own cron/shell environment, not re-plumbed per
    submission.

    Sources ~/.bashrc itself, in THIS process, before calling sbatch --
    per user, 2026-09-28: run_chunk.slurm used to do this internally,
    but that broke a manual `sbatch run_chunk.slurm` invocation (root
    cause not fully understood, but reproducible), so it was moved out
    to here instead. This path is the one that actually needs it: when
    triggered via --queue, the process calling this is get_commands_and_
    update_registry.py under cron, which has none of ~/.bashrc's
    effects (module/conda) the way an interactive login shell does --
    --export=ALL then carries THIS process's own (now-sourced)
    environment into the job. A manual sbatch run_chunk.slurm by hand
    never goes through this function at all, so it's unaffected either
    way."""
    script_dir = Path(__file__).resolve().parent
    run_chunk = script_dir / "run_chunk.slurm"
    if not run_chunk.is_file():
        print(f"ERROR: {run_chunk} not found -- submit-chunk only works on a machine with "
              f"run_chunk.slurm actually deployed (the HPC).", file=sys.stderr)
        return 1
    sbatch_cmd = (
        f"sbatch --export=ALL,EXPERIMENT_ID={shlex.quote(args.experiment_id)} "
        f"{shlex.quote(str(run_chunk))}"
    )
    # bash -c, not a direct ["sbatch", ...] argv, specifically so
    # `source ~/.bashrc` runs first in the SAME shell that then execs
    # sbatch (see this function's own docstring) -- means a missing
    # sbatch binary now surfaces as a real bash "command not found"
    # (rc=127) caught by the generic returncode check below, not a
    # Python-level FileNotFoundError the way a direct ["sbatch", ...]
    # argv would have raised.
    #
    # `;`, NOT `&&`, between the source and sbatch -- real, reproduced
    # failure (2026-09-29): ~/.bashrc on scylla exits non-zero for some
    # still-unclear reason (silently -- no output on either stream), and
    # `&&` let that alone block sbatch from ever running at all, with
    # nothing to show for it ("ERROR: sbatch failed (rc=1):" and nothing
    # after the colon, even after fixing the separate stdout/stderr
    # reporting gap this same day). Whatever conda/module state ~/.bashrc
    # manages to set up before hitting that point still ends up in THIS
    # shell's environment regardless of its own final exit code, and
    # that's the only reason it's sourced here at all -- sbatch's own
    # success/failure is what actually matters, not bashrc's.
    result = subprocess.run(
        ["bash", "-c", f"source ~/.bashrc; {sbatch_cmd}"],
        cwd=script_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Combined stdout+stderr, same fix as cmd_pull_code's own (2026-09-28):
        # get_commands_and_update_registry.py's dispatch captures ONLY this
        # process's stderr into the queue entry's "note" on failure -- a
        # version that printed sbatch's real error to stdout and only a
        # bare "rc=1" to stderr surfaced exactly that useless, detail-free
        # note in production (confirmed directly, 2026-09-29: "ERROR: sbatch
        # failed (rc=1):" with nothing after the colon).
        output = (result.stdout + result.stderr).strip()
        if "sbatch" in output and "not found" in output:
            print("ERROR: sbatch not found -- submit-chunk only works on a machine that's "
                  "actually part of the SLURM cluster (the HPC), not orca/bb-server1. Queue it "
                  "with --queue instead, for get_commands_and_update_registry.py to apply on "
                  "HPC.", file=sys.stderr)
        else:
            print(f"ERROR: sbatch failed (rc={result.returncode}): {output}", file=sys.stderr)
        return 1
    print(result.stdout.strip())
    return 0


def cmd_pull_code(args: argparse.Namespace) -> int:
    """`git pull` the oceanicu_3d checkout this script itself lives in --
    for queuing a code update (e.g. a chunk_runner.py/run_chunk.slurm fix)
    from orca/bb-server1 via --queue, when nobody's logged into the HPC
    directly to run it by hand. Same shape as cmd_submit_chunk's own
    exception to "nothing here touches the registry" -- no rt.connect(),
    no DB read/write of any kind, --db/--dry-run don't apply. Applied via
    get_commands_and_update_registry.py's own generic --queue dispatch,
    same as any other subcommand -- no special-casing needed there (see
    its own docstring: "this file never needs updating when a new
    oceanicu_experiments.py subcommand is added").

    Explicitly `origin claude` -- every real checkout this project uses
    (orca, bb-server1, shark) is on the `claude` branch, not whatever a
    bare `git pull` would fall back to via the checkout's own upstream
    tracking config (which may be unset or pointed at `main` on a
    checkout set up differently, e.g. the HPC's). `--ff-only` is
    deliberately NOT used -- if the HPC-side checkout ever has real
    local commits of its own (e.g. a hand-edited driver script fix,
    same real scenario pull_experiment_files.sh's own -u guards against
    for a different file), a plain (non-ff-only) pull's own merge/
    conflict behavior surfaces that loudly rather than silently
    overwriting it."""
    repo_root = Path(__file__).resolve().parent.parent
    if not (repo_root / ".git").is_dir():
        print(f"ERROR: {repo_root} is not a git checkout -- pull-code only works where "
              f"oceanicu_3d itself was cloned with git (the HPC's own checkout).", file=sys.stderr)
        return 1
    result = subprocess.run(["git", "-C", str(repo_root), "pull", "origin", "claude"], capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        # The DETAIL goes to stderr here specifically because
        # get_commands_and_update_registry.py's own dispatch captures
        # THIS process's stderr into the queue entry's "note" on failure
        # (stdout on success) -- an earlier version of this printed the
        # real git output to stdout unconditionally and only a bare,
        # detail-free "git pull failed (rc=1)" to stderr, so a real
        # failure's own cause never made it into the applied-queue_kb-
        # *.yaml report at all (confirmed directly, 2026-09-28).
        print(f"ERROR: git pull failed (rc={result.returncode}): {output}", file=sys.stderr)
        return 1
    if output:
        print(output)
    return 0


def _trim_updated_at(rows: list) -> list:
    """updated_at is stored+returned as a full ISO-8601 timestamp with UTC
    offset (e.g. '2026-09-08T07:18:48+00:00') -- display-only trim down to
    whole seconds (drop the '+00:00'), same as script/config_sha256 already
    get truncated for display in cmd_show. Per user, 2026-09-08."""
    out = []
    for r in rows:
        d = dict(r)
        if d.get("updated_at"):
            d["updated_at"] = str(d["updated_at"])[:19]
        out.append(d)
    return out


def cmd_list(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rows = rt.list_experiments(conn, status=args.status, sort=args.sort)
        if args.like:
            rows = [r for r in rows if args.like in r["experiment_id"]]
        # chunk_kind/chunk_multiplier are config (how BIG the next chunk
        # will be) -- actual_chunk is the highest chunk_index this
        # experiment has actually reached so far, one grouped query for
        # every experiment rather than a per-row lookup.
        latest = rt.get_latest_chunk_indices(conn)
        rows = [{**dict(r), "actual_chunk": latest.get(r["experiment_id"], "")} for r in rows]
        _print_table(_trim_updated_at(rows), _EXPERIMENT_COLUMNS, labels=_EXPERIMENT_COLUMN_LABELS)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        experiment = rt.get_experiment(conn, args.experiment_id)
        if experiment is None:
            print(f"ERROR: no such experiment_id: {args.experiment_id!r}", file=sys.stderr)
            return 1
        print("experiment:")
        _print_table(_trim_updated_at([experiment]), _EXPERIMENT_COLUMNS + ["experiment_root", "script", "config", "launcher", "fabm", "data_roots_file", "next_load_restart_time", "notes"], labels=_EXPERIMENT_COLUMN_LABELS)
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


def cmd_set_start_date(args: argparse.Namespace) -> int:
    with rt.connect(args.db) as conn:
        rt.set_start_date(conn, args.experiment_id, args.start_date, user=rt._current_user())
    print(f"{args.experiment_id}: initial_date set to {args.start_date} -- only takes effect if chunk 0 "
          f"hasn't run yet (next_chunk_start falls back to initial_date only when no chunk is 'done'); "
          f"otherwise this has no practical effect until a rerun/reset drops all chunks first")
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
        try:
            n = rt.rerun_from(conn, args.experiment_id, chunk_index=chunk_index, user=rt._current_user(),
                               note=args.note, force=args.force, restart_time=args.restart_time)
        except (KeyError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    print(f"{args.experiment_id}: dropped {n} chunk record(s) -- next submission redoes from there")
    if args.restart_time:
        print(f"  will resume from snapshot {args.restart_time!r} instead of the restart "
              f"file's own last snapshot (one-shot; consumed by that next chunk)")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """rerun --from-scratch under a clearer, more discoverable name --
    same primitive (rerun_from(chunk_index=0)), same in-progress guard.
    Returns an experiment to exactly the state right after `add`: every
    chunk record dropped, next submission starts fresh at initial_date
    with no load_restart. Never starts anything itself, same as `add`
    doesn't -- a human (or submit-chunk) still has to actually sbatch
    it. Files already on disk are untouched (chunk_runner.py archives a
    pre-existing chunk_dir aside rather than overwriting, same
    protection rerun --from-scratch already had)."""
    with rt.connect(args.db) as conn:
        try:
            n = rt.rerun_from(conn, args.experiment_id, chunk_index=0, user=rt._current_user(),
                               note=args.note, force=args.force)
        except (KeyError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    print(f"{args.experiment_id}: reset -- dropped {n} chunk record(s), back to the state right "
          f"after 'add' (nothing started; sbatch/submit-chunk it when ready)")
    return 0


_CHUNK_DIR_RE = re.compile(r"^(?P<num>\d{3})_(?P<start>\d{8})_(?P<end>\d{8})$")


def cmd_recover_chunks(args: argparse.Namespace) -> int:
    """Rebuild chunk history rows for an experiment whose registry chunk
    records were dropped by accident (`reset`/`rerun --from-scratch` run
    against the wrong experiment, or one chunk too many) while the real
    chunk_dir output on disk survived untouched -- rerun_from never
    deletes files, only registry rows (see its own docstring).

    Reconstructs chunks 0 .. --up-to-chunk - 1 as 'done', one at a time
    in order (each chunk's load_restart is the previous one's own
    save_restart, same as a real run), by parsing start/stop straight
    from each chunk_dir's own name (NNN_STARTDATE_ENDDATE -- ground
    truth, not recomputed from initial_date/chunk_kind/chunk_multiplier)
    and computing the restart file chunk_runner.py itself would have
    written (restart_<config-stem>_<stop>.nc, see its own save_restart
    construction) -- REFUSING to record a chunk as done unless that
    exact file actually exists, so this never fabricates a done row for
    a chunk that didn't really produce usable restart output.

    Chunk --up-to-chunk itself (and anything after) is left completely
    untouched. Idempotent: chunk indices already present in the
    registry are skipped, not re-inserted or errored on -- safe to
    re-run after fixing whatever stopped an earlier attempt partway
    through. Queueable like any other command here (--queue) for when
    the files/registry live on a machine nobody's logged into directly --
    this only ever reads chunk_dir names/restart files and writes
    registry rows, no sbatch, so it needs no --dry-run special-case
    (the generic scratch-DB one already covers it, same as `add`)."""
    with rt.connect(args.db) as conn:
        experiment = rt.get_experiment(conn, args.experiment_id)
        if experiment is None:
            print(f"ERROR: no such experiment_id: {args.experiment_id!r}", file=sys.stderr)
            return 1

        experiment_root = Path(rt.resolve_experiment_root(experiment["experiment_root"]))
        setup_name = Path(experiment["config"]).stem
        existing = {row["chunk_index"] for row in rt.list_chunks(conn, args.experiment_id)}

        chunks_on_disk: dict[int, Path] = {}
        for child in sorted(experiment_root.iterdir()):
            if not child.is_dir():
                continue
            m = _CHUNK_DIR_RE.match(child.name)
            if m:
                chunks_on_disk[int(m["num"])] = child

        missing = [i for i in range(args.up_to_chunk) if i not in chunks_on_disk]
        if missing:
            print(f"ERROR: no chunk_dir on disk for chunk index(es) {missing} under {experiment_root} "
                  f"-- refusing to fabricate a 'done' row for a chunk that was never actually run.",
                  file=sys.stderr)
            return 1

        user = rt._current_user()
        load_restart = None
        for i in range(args.up_to_chunk):
            chunk_dir = chunks_on_disk[i]
            if i in existing:
                print(f"chunk {i}: already in the registry -- skipping")
                row = rt.get_chunk(conn, args.experiment_id, i)
                if row is None:
                    print(f"ERROR: chunk {i} was just listed as existing but get_chunk found nothing "
                          f"-- registry changed underneath this command, retry.", file=sys.stderr)
                    return 1
                load_restart = row["save_restart"]
                continue

            m = _CHUNK_DIR_RE.match(chunk_dir.name)
            assert m is not None  # guaranteed by the chunks_on_disk scan above
            start = f"{m['start'][:4]}-{m['start'][4:6]}-{m['start'][6:]}"
            stop = f"{m['end'][:4]}-{m['end'][4:6]}-{m['end'][6:]}"
            save_restart = str(chunk_dir / f"restart_{setup_name}_{m['end']}.nc")

            if not Path(save_restart).is_file():
                print(f"ERROR: chunk {i} ({chunk_dir.name}) has no restart file at {save_restart} "
                      f"-- refusing to record it as done. Fix or remove this chunk_dir, or lower "
                      f"--up-to-chunk, then retry.", file=sys.stderr)
                return 1

            print(f"chunk {i}: {start} -> {stop}  load_restart={load_restart}  save_restart={save_restart}")
            rt.start_chunk(
                conn, experiment_id=args.experiment_id, chunk_index=i,
                start=start, stop=stop, chunk_dir=str(chunk_dir),
                load_restart=load_restart, save_restart=save_restart, user=user,
            )
            rt.finish_chunk(conn, experiment_id=args.experiment_id, chunk_index=i, exit_code=0, user=user)
            load_restart = save_restart

    print(f"{args.experiment_id}: chunks 0..{args.up_to_chunk - 1} recorded as done -- "
          f"next submission starts chunk {args.up_to_chunk}")
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

    # --- Registry lifecycle: bring an experiment's files/registry row into
    # or out of existence, or relocate/rename it. ------------------------
    st = sub.add_parser(
        "stage",
        help="rsync (filtered by --include, default generated*.py/generated*.yaml/gotm.yaml/"
             "fabm*.yaml) an "
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
                    help="rsync include pattern, repeatable (default: generated*.py, generated*.yaml, "
                         "gotm.yaml, fabm*.yaml)")
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

    rn = sub.add_parser(
        "rename",
        help="change an experiment's experiment_id and/or move its experiment_root -- for "
             "reusing/relocating an experiment (e.g. before `clean` + `stage`-ing a fresh copy "
             "of its generated files from a remote source) without losing its chunk/history "
             "record",
    )
    _add_common(rn); rn.set_defaults(func=cmd_rename)
    rn.add_argument("--experiment-id", required=True, help="the experiment's CURRENT experiment_id")
    rn.add_argument("--new-experiment-id", default=None,
                     help="new experiment_id -- chunks and history rows move to it too, and a "
                          "'renamed' history entry records the old id")
    rn.add_argument("--new-experiment-root", default=None,
                     help="new experiment_root -- the CURRENT resolved root is mv'd to the new "
                          "resolved location on THIS machine (refuses if the current one isn't "
                          "found locally, or the new one already exists), and only then is the "
                          "registry updated; stored in the DB as given, unresolved, same as add's "
                          "own --experiment-root")
    rn.add_argument("--force", action="store_true", help="allow renaming an in_progress experiment")

    cl = sub.add_parser(
        "clean",
        help="remove generated*.py/generated*.yaml at an experiment's real, resolved "
             "experiment_root -- the exact files/location `stage` writes to, so `clean` then "
             "`stage` is the intended pair for overwriting an experiment's generated files from "
             "a fresh remote source",
    )
    _add_common(cl); cl.set_defaults(func=cmd_clean)
    cl.add_argument("--experiment-id", required=True)
    cl.add_argument("--force", action="store_true", help="allow cleaning an in_progress experiment")

    # --- Inspect: read-only. ---------------------------------------------
    l = sub.add_parser("list"); _add_common(l); l.set_defaults(func=cmd_list)
    l.add_argument("--status", default=None, choices=list(rt.EXPERIMENT_STATUSES))
    l.add_argument("--like", default=None, help="substring filter on experiment_id")
    l.add_argument("--sort", default=None, choices=list(rt.LIST_SORT_KEYS),
                    help="default: priority (desc), experiment_id")

    s = sub.add_parser("show"); _add_common(s); s.set_defaults(func=cmd_show)
    s.add_argument("--experiment-id", required=True)

    # --- Start / stop / kill: whether and when chunks actually run. -----
    sc = sub.add_parser("submit-chunk"); _add_common(sc); sc.set_defaults(func=cmd_submit_chunk)
    sc.add_argument("--experiment-id", required=True,
                     help="sbatch run_chunk.slurm for this ALREADY-REGISTERED experiment, right "
                          "now -- for occasional manual use (e.g. via --queue from orca/bb-server1 "
                          "when nobody's on HPC directly), not the routine self-resubmission chain "
                          "(unaffected either way). Never touches the registry at all -- --db/"
                          "--dry-run don't apply. Only actually works run on the HPC itself, with "
                          "run_chunk.slurm deployed alongside this script, same as every other "
                          "SLURM-only command here.")

    pc = sub.add_parser(
        "pull-code",
        help="git pull the oceanicu_3d checkout this script itself lives in -- for queuing a "
             "code update (e.g. a chunk_runner.py fix) via --queue when nobody's logged into "
             "the HPC directly. Never touches the registry -- --db/--dry-run don't apply.",
    )
    _add_common(pc); pc.set_defaults(func=cmd_pull_code)

    pa = sub.add_parser("pause"); _add_common(pa); pa.set_defaults(func=cmd_pause)
    g1 = pa.add_mutually_exclusive_group(required=True)
    g1.add_argument("--experiment-id")
    g1.add_argument("--all", action="store_true")

    re_ = sub.add_parser("resume"); _add_common(re_); re_.set_defaults(func=cmd_resume)
    g2 = re_.add_mutually_exclusive_group(required=True)
    g2.add_argument("--experiment-id")
    g2.add_argument("--all", action="store_true")

    k = sub.add_parser("kill"); _add_common(k); k.set_defaults(func=cmd_kill)
    k.add_argument("--experiment-id", required=True,
                    help="scancel this experiment's currently-running chunk (if any) and mark it "
                         "failed -- unlike pause, takes effect immediately rather than at the next "
                         "chunk boundary; unlike remove, the experiment stays in the registry")

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

    # --- Alter the situation: change an experiment's settings, in effect
    # from its next chunk hand-off onward -- never retroactive, never
    # affects a chunk already running. ------------------------------------
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

    ssd = sub.add_parser("set-start-date"); _add_common(ssd); ssd.set_defaults(func=cmd_set_start_date)
    ssd.add_argument("--experiment-id", required=True)
    ssd.add_argument("--start-date", required=True, metavar="YYYY-MM-DD")

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

    # --- Recovery: rewind an experiment's own progress. ------------------
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
    rr.add_argument("--force", action="store_true",
                     help="allow dropping a chunk row that's still marked running -- scancels "
                          "the live job first (same as kill), then proceeds. Without this, "
                          "rerun refuses outright while the experiment is in_progress.")
    rr.add_argument("--restart-time", default=None, metavar="ISO8601",
                     help="resume the redone chunk from this specific internal snapshot of its "
                          "load_restart file, instead of the file's own default (its last "
                          "snapshot) -- only meaningful when that file actually holds more than "
                          "one (a restart written with add_restart(interval=...)). One-shot: "
                          "consumed by the very next chunk that actually starts, then cleared -- "
                          "never silently reused for a later chunk. Omitting this on a rerun "
                          "clears any previously-pending, not-yet-consumed request too.")

    rs = sub.add_parser("reset"); _add_common(rs); rs.set_defaults(func=cmd_reset)
    rs.add_argument("--experiment-id", required=True,
                     help="drop EVERY chunk record -- back to exactly the state right after "
                          "'add' (nothing started; sbatch/submit-chunk it when ready). Same "
                          "primitive as 'rerun --from-scratch', under a clearer name.")
    rs.add_argument("--note", default=None,
                     help="optional free-text reason, recorded in the history log")
    rs.add_argument("--force", action="store_true",
                     help="allow resetting while a chunk is still marked running -- scancels "
                          "the live job first (same as kill), then proceeds. Without this, "
                          "reset refuses outright while the experiment is in_progress.")

    rc = sub.add_parser(
        "recover-chunks",
        help="rebuild registry chunk rows from real chunk_dir output on disk, for when "
             "reset/rerun --from-scratch dropped chunk history by accident. See its own "
             "docstring for exactly what it does and doesn't touch.",
    )
    _add_common(rc); rc.set_defaults(func=cmd_recover_chunks)
    rc.add_argument("--experiment-id", required=True)
    rc.add_argument("--up-to-chunk", type=int, required=True, metavar="N",
                     help="replay chunks 0..N-1 as done; chunk N itself is left untouched")

    args = p.parse_args()

    if args.queue:
        if args.dry_run:
            print("ERROR: --queue and --dry-run don't combine -- queuing never touches a "
                  "real registry to begin with.", file=sys.stderr)
            return 1
        return _queue_command(Path(args.queue), args)

    if args.cmd == "submit-chunk" and args.dry_run:
        print("ERROR: --dry-run doesn't work for submit-chunk -- it calls sbatch directly, "
              "with no registry involved to safely redirect at a scratch copy the way every "
              "other command's --dry-run does. Running this WOULD submit a real job.",
              file=sys.stderr)
        return 1

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
