#!/usr/bin/env python
"""get_commands_and_update_registry.py -- pull queued commands in from
bb-server1 (optional), then replay whatever's pending against the LOCAL
registry. Renamed from apply_commands.py when the --pull-from step was
added (2026-08-30) -- same file, same core logic, now also does the
"get" half, not just the "apply" half.

Deployed alongside oceanicu_experiments.py/experiment_tracking.py/chunk_runner.py
wherever the real experiment_registry.sqlite actually lives (see
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

**Known limitation of `--pull-from`:** it's a plain directory rsync
(`-au`, i.e. skip anything newer on the receiver), not a merge -- if a
queue file gets a NEW entry appended upstream (workstation) after this
machine already applied and status-stamped some of its earlier entries,
the next pull overwrites the whole file, reverting those earlier
entries' status back to `pending` (the upstream copy never learned they
were applied -- there's no push-back channel for status, only a pull-in
channel for new commands). Reapplying an already-applied entry is
usually harmless -- most actions are idempotent (`set-stop-date`,
`pause`, ...) -- except `add`, which fails loudly (`experiment_id` already
exists, a real but noisy `IntegrityError`) rather than silently
double-registering anything. No data corruption either way, just a
confusing-looking `failed` entry for an experiment that's actually fine; check
`oceanicu-experiments show <experiment_id>` if one shows up unexpectedly.

Usage:
    python get_commands_and_update_registry.py --db /local/path/experiment_registry.sqlite \\
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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
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


def _pull(remote: str, queue_dir: Path) -> "int | None":
    """rsync remote (bb-server1's hpc_commands/) into queue_dir. Returns
    None on success, an exit code on failure. -u (skip anything newer on
    the receiver) so a since-applied local file's status stamps aren't
    blindly clobbered by an unchanged remote copy on every single run --
    see this module's own docstring for the residual limitation that
    remains regardless (a real append upstream still overwrites the
    whole file, status stamps included)."""
    queue_dir.mkdir(parents=True, exist_ok=True)
    src = remote.rstrip("/") + "/"
    dst = str(queue_dir).rstrip("/") + "/"
    result = subprocess.run(
        ["rsync", "-au", "-i", src, dst], capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: rsync --pull-from {remote} failed (rc={result.returncode}): "
              f"{result.stderr.strip()}", file=sys.stderr)
        return 1
    changed = [line for line in result.stdout.splitlines() if line.strip()]
    if changed:
        print(f"pulled {len(changed)} new/changed file(s) from {remote}:")
        for line in changed:
            print(f"  {line}")
    else:
        print(f"{remote}: up to date, nothing new.")
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
            print(f"{base_dir}: no queue_*.yaml files found -- nothing to apply.")
            return 0
    else:
        queue_paths = [Path(args.queue)]
        if not queue_paths[0].exists():
            print(f"{queue_paths[0]}: nothing to apply (file doesn't exist yet).")
            return 0

    # Load every source file once, then build one combined, time-ordered
    # work list across all of them -- so real submission order (queued_at)
    # is preserved regardless of which person's file an entry lives in.
    sources: dict[Path, dict] = {}
    pending: list[tuple[Path, dict]] = []
    for qp in queue_paths:
        source_data = yaml.safe_load(qp.read_text()) or {}
        sources[qp] = source_data
        for entry in source_data.get("commands", []):
            if entry.get("status") == "pending":
                pending.append((qp, entry))
    pending.sort(key=lambda item: item[1].get("queued_at", ""))

    if not pending:
        total = sum(len(d.get("commands", [])) for d in sources.values())
        print(f"nothing pending across {len(queue_paths)} queue file(s) ({total} total entries).")
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
                print(f"{entry['id']} ({qp.name}): FAILED (add, checking files) -- {verify_error}",
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
            print(f"{entry['id']} ({qp.name}): applied ({action})")
        else:
            entry["status"] = "failed"
            entry["note"] = (result.stderr.strip() or result.stdout.strip() or "unknown error")[-2000:]
            failed += 1
            print(f"{entry['id']} ({qp.name}): FAILED ({action}) -- {entry['note']}", file=sys.stderr)

        # Write back after EACH command, not just at the end -- a crash
        # partway through a long queue must not lose already-applied
        # statuses or force re-applying everything from scratch.
        _write_back(qp, sources[qp])

    still_pending = sum(
        1 for d in sources.values() for e in d.get("commands", []) if e.get("status") == "pending"
    )
    print(f"done: {applied} applied, {failed} failed, {still_pending} still pending "
          f"(across {len(queue_paths)} queue file(s)).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
