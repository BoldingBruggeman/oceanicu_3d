#!/usr/bin/env python
"""get_commands_and_update_registry.py -- pull queued commands in from
bb-server1 (optional), then replay whatever's pending against the LOCAL
registry. Renamed from apply_commands.py when the --pull-from step was
added (2026-08-30) -- same file, same core logic, now also does the
"get" half, not just the "apply" half.

Deployed alongside oceanicu_experiments.py/experiment_tracking.py/chunk_runner.py
wherever the real submission_registry.sqlite actually lives (see
EXPERIMENT_TRACKING.md "Command queue") -- for a registry with no network path
reachable from wherever experiments are actually defined/queued from (e.g. a
fully network-isolated HPC whose login node can only ever INITIATE an
outbound connection, never receive one).

**If `--pull-from` is given**, this rsyncs that remote directory (e.g.
bb-server1's `hpc_commands/`) into `--queue-dir` FIRST, before looking
for anything pending -- so a single cron job covers both halves: get
the latest commands, then apply whatever's new. Only makes sense on
whatever machine actually has outbound network reach (the HPC's LOGIN
node, not a compute node -- see EXPERIMENT_TRACKING.md "Keeping bb-server1's
copy of the registry up to date" for the identical constraint on the
push side, `push_registry_snapshot.sh`). Omit `--pull-from` to run
exactly as before (`apply_commands.py`'s original behavior, unchanged) --
e.g. for local testing, or if the local `hpc_commands/` copy is kept up
to date some other way (a human rsyncing by hand).

Each pending entry is replayed through oceanicu_experiments.py's own CLI
(reconstructed from the stored args as real --flag values), so applying
a queued command goes through IDENTICAL validation/behavior as running
it directly -- no per-action logic is reimplemented here, and this file
never needs updating when a new oceanicu_experiments.py subcommand is added.

Deliberately narrow: never accepts an ssh:// --db (this only ever makes
sense run locally, where the registry's own machine is), and NEVER calls
sbatch, for any experiment, new or resubmitting -- submitting a job is always a
deliberate manual action on the HPC, whether that's the first chunk of a
brand-new experiment or anything else. This script only ever touches the
registry's `experiments`/`history` rows via oceanicu_experiments.py's own commands.

A brand-new experiment's `add` also needs its actual driver script/config
physically present at `experiment_root` before any chunk can start -- the queue
file alone only ever carries the DB row. Getting them there is `oceanicu-experiments
stage`'s job (see its own docstring): it writes directly to the experiment's
real, resolved `experiment_root` on whatever machine runs it -- there is no
separate staging directory to copy from here. This file's own role is
narrower: before applying an `add`, `_verify_experiment_files_present`
confirms `script`/`config` are actually present at the resolved
`experiment_root` on THIS machine (populated by `stage` directly if this
machine IS where staging happened, or by `bin/pull_experiment_files.sh`'s
filtered sync if not) and fails the command loudly if not -- never
registering a file-less experiment, without copying anything itself.

Multiple people can queue commands from different places -- rather than
have them all write to one shared file (a real risk of one rsync
clobbering another's in-flight edit), each person gets their OWN queue
file, `queue_<name>.yaml`, in the same directory. `--queue-dir` processes
every `queue_*.yaml` found there, combined and applied in `queued_at`
order across all of them, so real submission order is preserved
regardless of which file an entry lives in. `--queue PATH` (a single,
exact file) still works too, for a quick one-off or testing.

`hpc_commands/` itself is plain data, deliberately NOT part of the
`oceanicu_3d` git repo -- see EXPERIMENT_TRACKING.md "Command queue" for where
it actually lives and how it physically gets here (rsync, at every hop,
never git/GitHub).

**`--pull-from` merges by id, it does NOT overwrite (fixed 2026-09-02):**
`_pull` rsyncs the remote directory into a throwaway staging area, then
`_merge_queue_file` folds each staged file into this machine's own
local copy keyed by each entry's own unique `id` -- an id this machine
has already resolved (status != `pending`, from actually applying it)
keeps its local resolution no matter what the upstream copy still says
about it (upstream has no push-back channel of its own, so its copy of
a since-applied id can sit at `pending` forever); only genuinely NEW
ids get added. This is the actual reason the `id` field exists.

**Why this matters, concretely:** the OLD behavior here was a plain
directory rsync (`-au`), not a merge -- a fresh pull would silently
revert every already-resolved entry back to `pending`, and a `submit-
chunk` entry re-arming itself this way meant a real second `sbatch`
call, an actually-dangerous double-submission, not just a cosmetic
inconsistency. Confirmed happening in production, 2026-09-02, which is
what prompted this fix (previously this section documented it as a
"known limitation" -- it no longer is one).

After applying, each queue file that had at least one entry change status
this run is ALSO pushed BACK to the same `--pull-from` remote directory,
under a renamed, non-`queue_*.yaml` archival name (see
`_push_back_applied`'s own docstring) -- a human-readable paper trail of
what got applied/failed and when, visible from bb-server1 without
needing to log into the HPC. Purely additive on top of the merge fix
above, not a substitute for it.

Usage:
    python get_commands_and_update_registry.py --db /local/path/submission_registry.sqlite \\
        --queue-dir /local/path/hpc_commands/ \\
        [--pull-from bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands]

    # or a single exact file (never combined with --pull-from -- that's
    # a whole-directory sync, not a single-file one):
    python get_commands_and_update_registry.py --db ... --queue /local/path/hpc_commands/queue_kb.yaml

    # --db and --queue-dir both fall back to an env var if omitted
    # (OCEANICU_EXPERIMENT_DB, OCEANICU_HPC_COMMANDS_DIR respectively) --
    # handy for manual invocation once those are already exported, same
    # convention as oceanicu-experiments' own --db:
    OCEANICU_EXPERIMENT_DB=... OCEANICU_HPC_COMMANDS_DIR=... python get_commands_and_update_registry.py

Typically run from a cron job (or by hand, or from run_chunk.slurm's own
execution) on whichever machine physically holds both the registry and
a local copy of the queue file(s) -- the LOGIN node specifically if
`--pull-from` is used, since that's the only place with outbound network
reach -- see EXPERIMENT_TRACKING.md.
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


def _load_queue_data(path: Path) -> dict:
    if not path.is_file():
        return {"commands": []}
    data = yaml.safe_load(path.read_text()) or {}
    if isinstance(data, list):
        data = {"commands": data}
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

    args.db = args.db or os.environ.get("OCEANICU_EXPERIMENT_DB")
    if not args.db:
        print("ERROR: --db not given and OCEANICU_EXPERIMENT_DB not set.", file=sys.stderr)
        return 1

    if not args.queue and not args.queue_dir:
        args.queue_dir = os.environ.get("OCEANICU_HPC_COMMANDS_DIR")
        if not args.queue_dir:
            print("ERROR: one of --queue/--queue-dir is required (or set "
                  "OCEANICU_HPC_COMMANDS_DIR).", file=sys.stderr)
            return 1

    if args.db.startswith("ssh://"):
        print("ERROR: --db must be a local path -- get_commands_and_update_registry.py only "
              "ever runs where the registry actually lives, never over the relay.", file=sys.stderr)
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
        return 0

    applied = 0
    failed = 0
    for qp, entry in pending:
        action = entry["action"]
        entry_args = entry.get("args", {})
        entry["applied_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if action == "add":
            verify_error = _verify_experiment_files_present(
                entry_args["experiment_root"], entry_args["script"], entry_args["config"],
            )
            if verify_error:
                entry["status"] = "failed"
                entry["note"] = verify_error
                failed += 1
                print(f"{_ts()}: {entry['id']} ({qp.name}): FAILED (add, checking files) -- {verify_error}",
                      file=sys.stderr)
                _write_back(qp, sources[qp])
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
            print(f"{_ts()}: {entry['id']} ({qp.name}): applied ({action})")
        else:
            entry["status"] = "failed"
            entry["note"] = (result.stderr.strip() or result.stdout.strip() or "unknown error")[-2000:]
            failed += 1
            print(f"{_ts()}: {entry['id']} ({qp.name}): FAILED ({action}) -- {entry['note']}", file=sys.stderr)

        # Write back after EACH command, not just at the end -- a crash
        # partway through a long queue must not lose already-applied
        # statuses or force re-applying everything from scratch.
        _write_back(qp, sources[qp])

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

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
