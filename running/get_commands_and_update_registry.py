#!/usr/bin/env python
"""get_commands_and_update_registry.py -- optionally pull queued commands
from bb-server1, then apply the pending ones to the LOCAL registry. Runs where
the registry lives (the HPC login node, which can only make outbound
connections), usually from cron. See EXPERIMENT_TRACKING.md "Command queue".

--pull-from rsyncs the remote hpc_commands/ into a staging area and merges
each queue file into the local copy BY ENTRY ID: an id already resolved here
keeps its local status whatever the remote copy says (the remote has no other
way to learn it). A plain overwrite re-armed applied entries -- including
submit-chunk, i.e. a second sbatch. Queue files that changed are pushed back
under an archival (non-queue_*.yaml) name, as a record visible on bb-server1.

Each pending entry is replayed through oceanicu_experiments.py's own CLI, so
it gets exactly the same validation as running it directly, and new
subcommands need no change here. Never takes an ssh:// --db and never calls
sbatch itself. Before an `add`, the script/config must already be at the
resolved experiment_root (from `stage` or bin/pull_experiment_files.sh).

--queue-dir applies every queue_*.yaml there, merged in queued_at order. Use
one file per person AND machine (queue_kb_orca.yaml): the orca -> bb-server1
hop is a plain overwrite, so two machines sharing one filename lose entries.

Usage:
    python get_commands_and_update_registry.py --db /local/path/submission_registry.sqlite \\
        --queue-dir /local/path/hpc_commands/ \\
        [--pull-from bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands]
    python get_commands_and_update_registry.py --db ... --queue /local/path/hpc_commands/queue_kb.yaml

--db and --queue-dir default to $OCEANICU_EXPERIMENT_DB / $OCEANICU_HPC_COMMANDS_DIR.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def _ts() -> str:
    """Local time with UTC offset, matching the `$(date -Is)` convention
    already used in bin/restart_registry_watcher.sh and
    bin/watch_registry_and_push.sh -- so all three log files this system
    produces (this script's own, and the two watcher scripts') show
    directly-comparable timestamps when read side by side."""
    return datetime.now().astimezone().isoformat(timespec="seconds")
OCEANICU_EXPERIMENTS_SCRIPT = HERE / "oceanicu_experiments.py"

sys.path.insert(0, str(HERE))
import experiment_tracking as rt  # noqa: E402


def _args_dict_to_cli(args_dict: dict) -> list[str]:
    """Turn a queued entry's stored args dict back into real --flag
    values -- the inverse of oceanicu_experiments.py's own _queue_command."""
    cli: list[str] = []
    for key, value in args_dict.items():
        if value is None:
            continue
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                cli.append(flag)
            continue
        cli.extend([flag, str(value)])
    return cli


def _verify_experiment_files_present(experiment_root: str, script: str, config: str) -> "str | None":
    """Confirm *script*/*config* actually exist at the REAL (resolved)
    experiment_root before the experiment is registered -- doesn't copy
    or stage anything itself. `oceanicu-experiments stage` (wherever it
    was run) already writes an experiment's generated files directly to
    this same real location; getting them onto THIS machine's own copy
    of that location is a separate, filtered sync
    (bin/pull_experiment_files.sh), not something this function does --
    this is purely the safety check that used to be a side effect of the
    old copy-based approach ("never register a file-less experiment"),
    kept as an explicit check now that there's no copy step here to fail.
    Returns an error message on failure, None on success."""
    try:
        real_root = Path(rt.resolve_experiment_root(experiment_root))
    except RuntimeError as exc:
        return f"can't resolve experiment_root {experiment_root!r} to check for its files: {exc}"
    missing = []
    for label, name in (("script", script), ("config", config)):
        path = Path(name) if Path(name).is_absolute() else real_root / name
        if not path.is_file():
            missing.append(f"{label} ({path})")
    if missing:
        return (f"{', '.join(missing)} not found -- expected the experiment's driver files to "
                f"already be at {real_root} (via `stage` + bin/pull_experiment_files.sh) before "
                f"registering it")
    return None


def _write_back(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _push_back_applied(remote: str, entries_by_file: dict[Path, list[dict]]) -> None:
    """Write and push back a queue file containing ONLY the entries that
    were actually resolved (changed status away from pending) THIS
    apply round -- not this machine's full accumulated local queue file
    (which also holds every entry resolved in EARLIER rounds too).
    Pushed to the SAME remote directory --pull-from pulled from, under
    a renamed, non-`queue_*.yaml` archival name -- e.g. `queue_kb.yaml`
    -> `applied-queue_kb-20260902T120000Z.yaml`.

    Deliberately a NEW name, never overwriting the original: the real
    `queue_kb.yaml` on bb-server1 is a live, still-being-appended-to file
    (people keep queuing new commands into it via `oceanicu-experiments
    --queue`), so blindly overwriting it with this machine's post-apply
    copy would race with -- and could silently drop -- anything queued
    there since this run's own --pull-from pull. The renamed copy also
    deliberately does NOT match the `queue_*.yaml` glob, so it can never
    be mistaken for a live queue file by _pull's own --include filter or
    by the applier's own queue_dir.glob("queue_*.yaml") on either side.

    Purely an archival record for whoever's on bb-server1 to glance at
    ("did my command actually go through, and what did it say, THIS
    round") -- separate from, and additive on top of, _merge_queue_file's
    own id-based merge (which is what actually keeps an already-resolved
    entry from being re-applied; this function alone would NOT
    accomplish that -- it only ever writes a differently-named archival
    copy, never the live queue_kb.yaml upstream itself still reads
    from)."""
    remote_dir = remote.rstrip("/")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for qp, entries in entries_by_file.items():
        archival_name = f"applied-{qp.stem}-{ts}{qp.suffix}"
        tmp_path = qp.parent / f".{archival_name}.tmp"
        _write_back(tmp_path, {"commands": entries})
        dest = f"{remote_dir}/{archival_name}"
        result = subprocess.run(
            ["rsync", "-a", str(tmp_path), dest], capture_output=True, text=True,
        )
        tmp_path.unlink(missing_ok=True)
        if result.returncode != 0:
            print(f"{_ts()}: WARNING: failed to push {qp.name} back to {dest} "
                  f"(rc={result.returncode}): {result.stderr.strip()}", file=sys.stderr)
        else:
            print(f"{_ts()}: pushed {qp.name} -> {dest}")


def _push_back_pull_status(remote: str, queue_dir: Path) -> None:
    """Push bin/pull_experiment_files.sh's own last-run status (a
    .last_pull_experiment_files.yaml it drops in queue_dir, if it ran
    at all this cycle -- see its own OCEANICU_HPC_COMMANDS_DIR
    handling) back to bb-server1, so a round with ZERO queued commands
    still confirms from bb-server1's side that the file-pull actually
    ran and what it changed -- unlike _push_back_applied above, this
    is unconditional, not gated on there being anything to report from
    the queue itself. Silently does nothing if the status file isn't
    there (e.g. this script run standalone, without the file-pull half
    of the chained cron -- see setup_experiment_tracking.sh's own note
    on that being a supported, if less complete, configuration)."""
    status_path = queue_dir / ".last_pull_experiment_files.yaml"
    if not status_path.is_file():
        return
    dest = f"{remote.rstrip('/')}/last_pull_experiment_files.yaml"
    result = subprocess.run(
        ["rsync", "-a", str(status_path), dest], capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"{_ts()}: WARNING: failed to push pull-status back to {dest} "
              f"(rc={result.returncode}): {result.stderr.strip()}", file=sys.stderr)
    else:
        print(f"{_ts()}: pushed pull-status -> {dest}")


def _load_queue_data(path: Path) -> dict:
    if not path.is_file():
        return {"commands": []}
    data = yaml.safe_load(path.read_text()) or {}
    if isinstance(data, list):
        data = {"commands": data}
    # A hand-edited file with a bare "commands:" (no value) parses as
    # {"commands": None} -- every caller below does data.get("commands", [])
    # for exactly this "commands never explicitly set to a list" case, but
    # dict.get's default only kicks in when the KEY is missing, not when
    # it's present and None (confirmed: a real queue_kb.yaml hand-edited
    # this way crashed oceanicu_experiments.py's own _queue_command the
    # same way, TypeError: 'NoneType' object is not iterable here).
    if data.get("commands") is None:
        data["commands"] = []
    return data


def _merge_queue_file(local_path: Path, incoming_path: Path) -> bool:
    """Merge a freshly-pulled queue file into the existing LOCAL one,
    keyed by each entry's own unique `id` -- this is the actual
    mechanism the id field was created for (a design decision from
    earlier in this project, predating this specific fix): once THIS
    machine has recorded a given command id as resolved (status !=
    pending, from actually applying it), a later pull must NEVER
    revert that id back to pending, no matter what the incoming copy
    still says about it.

    Why this matters: the UPSTREAM copy (bb-server1's queue_kb.yaml)
    has no push-back channel of its own (see this module's docstring's
    "Known limitation") -- it can sit there showing status: pending for
    an id FOREVER, even long after this machine actually applied it.
    A plain rsync overwrite (the old behavior) would blindly replace
    this machine's own resolved copy with that stale pending one on
    EVERY pull, causing the same command to be re-applied on every
    single cron cycle indefinitely. Confirmed happening in production,
    2026-09-02: a `submit-chunk` entry re-armed itself and resubmitted
    a real SLURM job on a later cron cycle purely because of this.

    Only genuinely NEW ids (never seen in the local copy before) are
    ever added, as-is, from the incoming copy -- any id the local copy
    already knows about (pending OR resolved) keeps exactly what the
    local copy already says, full stop; the incoming copy is never
    trusted over local history for an id this machine has already seen.
    Returns True if anything was actually added (local file changed on
    disk), False if there was nothing new (local file left untouched)."""
    incoming_data = _load_queue_data(incoming_path)
    local_data = _load_queue_data(local_path)

    known_ids = {c["id"] for c in local_data.get("commands", []) if c.get("id")}
    new_entries = [c for c in incoming_data.get("commands", []) if c.get("id") not in known_ids]
    if not new_entries:
        return False

    local_data["commands"] = new_entries + local_data.get("commands", [])
    _write_back(local_path, local_data)
    return True


def _pull(remote: str, queue_dir: Path) -> "int | None":
    """rsync remote (bb-server1's hpc_commands/) into a throwaway staging
    directory, then MERGE each staged file into queue_dir's own copy by
    id (see _merge_queue_file's own docstring for why a plain overwrite
    -- the old behavior here -- is actively dangerous: it silently
    reverts this machine's own already-resolved statuses back to
    pending on every pull, causing indefinite re-application). Returns
    None on success, an exit code on failure.

    --include/--exclude restricts the rsync to queue_*.yaml only,
    matching this module's own docstring contract ("hpc_commands/ ...
    deliberately NOT part of git ... only ever contains queue_*.yaml
    files"). Without this, a stray editor artifact left behind on the
    SOURCE side (e.g. someone's vim .queue_kb.yaml.swp from having the
    file open) gets pulled in too -- confirmed 2026-09-02. That file
    itself is harmless here (the applier's own queue_dir.glob("queue_*.yaml")
    never matches it), but its presence is the tell for the actual
    failure mode: a live vim buffer on bb-server1 doesn't see writes
    _queue_command makes directly to the file on disk, so a later
    unrelated `:w` in that vim session silently overwrites -- and loses
    -- any commands appended since vim opened it, before this pull ever
    runs. Filtering the swap file out of the sync doesn't fix that
    underlying risk (only closing/reloading the vim session does), but
    it does stop the confusing artifact from showing up on the HPC side
    at all."""
    queue_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = queue_dir / ".pull_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    src = remote.rstrip("/") + "/"
    dst = str(staging_dir).rstrip("/") + "/"
    result = subprocess.run(
        ["rsync", "-a", "-i", "--include=queue_*.yaml", "--exclude=*", src, dst],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        shutil.rmtree(staging_dir, ignore_errors=True)
        print(f"ERROR: rsync --pull-from {remote} failed (rc={result.returncode}): "
              f"{result.stderr.strip()}", file=sys.stderr)
        return 1
    merged_files = []
    for staged_file in sorted(staging_dir.glob("queue_*.yaml")):
        local_file = queue_dir / staged_file.name
        if _merge_queue_file(local_file, staged_file):
            merged_files.append(staged_file.name)
    shutil.rmtree(staging_dir, ignore_errors=True)
    if merged_files:
        print(f"{_ts()}: merged new command(s) in from {remote}, into: {', '.join(merged_files)}")
    else:
        print(f"{_ts()}: {remote}: up to date, nothing new.")
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=None,
                    help="LOCAL registry path -- never an ssh:// URL, this only ever runs "
                         "where the registry's own machine is. Falls back to "
                         "OCEANICU_EXPERIMENT_DB if not given, same convention as "
                         "oceanicu-experiments' own --db.")
    g = p.add_mutually_exclusive_group(required=False)
    g.add_argument("--queue", metavar="PATH", help="a single, exact queue file")
    g.add_argument("--queue-dir", metavar="DIR",
                    help="process every queue_*.yaml found here (one per person who queues "
                         "commands -- see this file's own docstring). If neither --queue nor "
                         "--queue-dir is given, falls back to OCEANICU_HPC_COMMANDS_DIR.")
    p.add_argument("--pull-from", default=None, metavar="REMOTE",
                    help="rsync source to pull hpc_commands/ in from first, e.g. "
                         "bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands "
                         "-- only valid with --queue-dir (a whole-directory sync). Only "
                         "makes sense run from a machine with outbound network reach "
                         "(the HPC's login node, not a compute node).")
    args = p.parse_args()

    try:
        args.db = rt.resolve_db_path_arg(args.db, allow_remote=False)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not args.queue and not args.queue_dir:
        args.queue_dir = os.environ.get("OCEANICU_HPC_COMMANDS_DIR")
        if not args.queue_dir:
            print("ERROR: one of --queue/--queue-dir is required (or set "
                  "OCEANICU_HPC_COMMANDS_DIR).", file=sys.stderr)
            return 1

    if args.pull_from and not args.queue_dir:
        print("ERROR: --pull-from needs --queue-dir (it syncs a whole directory, "
              "not a single --queue file).", file=sys.stderr)
        return 1

    if args.pull_from:
        pull_error = _pull(args.pull_from, Path(args.queue_dir))
        if pull_error is not None:
            return pull_error

    if args.queue_dir:
        base_dir = Path(args.queue_dir)
        queue_paths = sorted(base_dir.glob("queue_*.yaml"))
        if not queue_paths:
            print(f"{_ts()}: {base_dir}: no queue_*.yaml files found -- nothing to apply.")
            if args.pull_from:
                _push_back_pull_status(args.pull_from, base_dir)
            return 0
    else:
        queue_paths = [Path(args.queue)]
        if not queue_paths[0].exists():
            print(f"{_ts()}: {queue_paths[0]}: nothing to apply (file doesn't exist yet).")
            return 0

    # Load every source file once, then build one combined, time-ordered
    # work list across all of them -- so real submission order (queued_at)
    # is preserved regardless of which person's file an entry lives in.
    sources: dict[Path, dict] = {}
    pending: list[tuple[Path, dict]] = []
    for qp in queue_paths:
        # _load_queue_data also tolerates a bare list (the pre-wrapper
        # format some older/hand-made queue files still use) instead of
        # crashing on .get -- matching normalization lives in
        # oceanicu_experiments.py's _queue_command.
        sources[qp] = _load_queue_data(qp)
        for entry in sources[qp].get("commands", []):
            if entry.get("status") == "pending":
                pending.append((qp, entry))
    pending.sort(key=lambda item: item[1].get("queued_at", ""))

    if not pending:
        total = sum(len(d.get("commands", [])) for d in sources.values())
        print(f"{_ts()}: nothing pending across {len(queue_paths)} queue file(s) ({total} total entries).")
        if args.pull_from and args.queue_dir:
            _push_back_pull_status(args.pull_from, Path(args.queue_dir))
        return 0

    def _record_queue_result(entry: dict, qp: Path, entry_args: dict) -> None:
        """Best-effort: log this queue entry's real result (id, action,
        who queued it, when, outcome) into the registry's own history
        table -- see record_queue_command's own docstring for why this
        matters (a durable, in-DB record independent of whatever the
        applied action's own history entry says, attributed to the
        REAL queuer rather than whoever's OS account ran this apply
        step). Skipped (not fatal) if the entry has no experiment_id to
        attach it to, or if the DB write itself fails -- this is a
        supplementary record, never allowed to break the actual apply."""
        eid = entry_args.get("experiment_id")
        if not eid:
            return
        # .get() throughout, not entry[...] -- a malformed entry (missing
        # even 'id'/'action') must still be able to reach this best-effort
        # log call without raising, same "never allowed to break the
        # actual apply" intent this function already documents for the
        # DB-write half below.
        entry_id = entry.get("id", "?")
        detail = (f"{entry_id} ({entry.get('action', '?')}) from {qp.name}, queued_at="
                  f"{entry.get('queued_at')} -> {entry.get('status', '?')}"
                  f"{': ' + entry['note'][:300] if entry.get('note') else ''}")
        try:
            with rt.connect(args.db) as conn:
                rt.record_queue_command(conn, eid, detail, user=entry.get("queued_by"))
        except Exception as exc:
            print(f"{_ts()}: WARNING: couldn't record queue-command history for "
                  f"{entry_id}: {exc}", file=sys.stderr)

    applied = 0
    failed = 0
    for qp, entry in pending:
        entry_id = entry.get("id", "?")
        entry_args = entry.get("args", {})
        try:
            action = entry["action"]
            entry["applied_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

            if action == "add":
                verify_error = _verify_experiment_files_present(
                    entry_args["experiment_root"], entry_args["script"], entry_args["config"],
                )
                if verify_error:
                    entry["status"] = "failed"
                    entry["note"] = verify_error
                    failed += 1
                    print(f"{_ts()}: {entry_id} ({qp.name}): FAILED (add, checking files) -- {verify_error}",
                          file=sys.stderr)
                    _write_back(qp, sources[qp])
                    _record_queue_result(entry, qp, entry_args)
                    continue

            cli_args = _args_dict_to_cli(entry_args)
            result = subprocess.run(
                [sys.executable, str(OCEANICU_EXPERIMENTS_SCRIPT), "--db", args.db, action, *cli_args],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                entry["status"] = "applied"
                entry["note"] = result.stdout.strip() or None
                applied += 1
                print(f"{_ts()}: {entry_id} ({qp.name}): applied ({action})")
            else:
                entry["status"] = "failed"
                entry["note"] = (result.stderr.strip() or result.stdout.strip() or "unknown error")[-2000:]
                failed += 1
                print(f"{_ts()}: {entry_id} ({qp.name}): FAILED ({action}) -- {entry['note']}", file=sys.stderr)
        except Exception as exc:
            # A malformed entry (e.g. an `add` missing experiment_root/
            # script/config, or no `action` at all) must fail just THIS
            # entry, not crash the whole batch -- every other failure mode
            # in this loop already only ever marks one entry `failed` and
            # moves on; an uncaught exception here would silently lose
            # track of every remaining pending entry in the same run
            # instead (they'd never get written back at all, and would
            # incorrectly still show `pending` next round even though this
            # run never actually looked at them again).
            entry["status"] = "failed"
            entry["note"] = f"unexpected error applying this entry: {exc}"[:2000]
            failed += 1
            print(f"{_ts()}: {entry_id} ({qp.name}): FAILED (unexpected error) -- {exc}", file=sys.stderr)

        # Write back after EACH command, not just at the end -- a crash
        # partway through a long queue must not lose already-applied
        # statuses or force re-applying everything from scratch.
        _write_back(qp, sources[qp])
        _record_queue_result(entry, qp, entry_args)

    still_pending = sum(
        1 for d in sources.values() for e in d.get("commands", []) if e.get("status") == "pending"
    )
    print(f"{_ts()}: done: {applied} applied, {failed} failed, {still_pending} still pending "
          f"(across {len(queue_paths)} queue file(s)).")

    if args.pull_from:
        # pending's entries are the SAME dict objects mutated in-place by
        # the apply loop above, so this already reflects each entry's
        # real post-apply status/applied_at/note -- just grouped by which
        # local file it came from, for _push_back_applied's own per-file
        # archival write (see its own docstring for why this must be ONLY
        # what changed this round, not the whole accumulated local file).
        entries_by_file: dict[Path, list[dict]] = {}
        for qp, entry in pending:
            entries_by_file.setdefault(qp, []).append(entry)
        _push_back_applied(args.pull_from, entries_by_file)
        _push_back_pull_status(args.pull_from, Path(args.queue_dir))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
