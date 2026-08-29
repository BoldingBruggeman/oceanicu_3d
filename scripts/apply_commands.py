#!/usr/bin/env python
"""apply_commands.py -- replay a command-queue YAML file's pending
entries against the LOCAL registry.

Deployed alongside oceanicu_runs.py/run_tracking.py/chunk_runner.py
wherever the real run_registry.sqlite actually lives (see
RUN_TRACKING.md "Command queue") -- for a registry with no network path
reachable from wherever runs are actually defined/queued from (e.g. a
fully network-isolated HPC whose login node can only ever INITIATE an
outbound connection, never receive one).

Each pending entry is replayed through oceanicu_runs.py's own CLI
(reconstructed from the stored args as real --flag values), so applying
a queued command goes through IDENTICAL validation/behavior as running
it directly -- no per-action logic is reimplemented here, and this file
never needs updating when a new oceanicu_runs.py subcommand is added.

Deliberately narrow: never accepts an ssh:// --db (this only ever makes
sense run locally, where the registry's own machine is), and NEVER calls
sbatch, for any run, new or resubmitting -- submitting a job is always a
deliberate manual action on the HPC, whether that's the first chunk of a
brand-new run or anything else. This script only ever touches the
registry's `runs`/`history` rows via oceanicu_runs.py's own commands.

A brand-new run's `add` also needs its actual driver script/config
physically present at `run_root` before any chunk can start -- the queue
file alone only ever carries the DB row. Alongside `--queue`'s own file,
a sibling `run_files/` directory (see --run-files-dir) mirrors run_root
paths: `run_files/<run_root>/generated_foo.py`, etc. For every `add`
being applied, whatever's staged there is copied into the REAL
(resolved) run_root first, and only then is the run actually registered
-- a copy failure never leaves an orphan, file-less DB row behind.

Usage:
    python apply_commands.py --db /local/path/run_registry.sqlite \\
        --queue hpc_commands/commands.yaml \\
        [--run-files-dir hpc_commands/run_files]   # default: <queue's own dir>/run_files

Typically run from a cron job (or by hand, or from run_chunk.slurm's own
execution) on whichever machine physically holds both the registry and
a local copy of the queue file -- see RUN_TRACKING.md.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
OCEANICU_RUNS = HERE / "oceanicu_runs.py"

sys.path.insert(0, str(HERE))
import run_tracking as rt  # noqa: E402


def _args_dict_to_cli(args_dict: dict) -> list[str]:
    """Turn a queued entry's stored args dict back into real --flag
    values -- the inverse of oceanicu_runs.py's own _queue_command."""
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


def _stage_run_files(run_root: str, run_files_dir: Path) -> "str | None":
    """Copy whatever's staged at run_files_dir/<run_root>/ into the REAL
    (resolved) run_root, before the run is registered. Returns an error
    message on failure, None on success (including the legitimate
    no-op case: nothing staged for this run, e.g. the files were already
    placed there some other way)."""
    staged = run_files_dir / run_root
    if not staged.is_dir():
        return None
    try:
        real_root = Path(rt.resolve_run_root(run_root))
    except RuntimeError as exc:
        return f"can't resolve run_root {run_root!r} to stage files into: {exc}"
    try:
        real_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged, real_root, dirs_exist_ok=True)
    except OSError as exc:
        return f"failed copying staged files {staged} -> {real_root}: {exc}"
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True,
                    help="LOCAL registry path -- never an ssh:// URL, this only ever runs "
                         "where the registry's own machine is")
    p.add_argument("--queue", required=True, metavar="PATH")
    p.add_argument("--run-files-dir", default=None, metavar="PATH",
                    help="defaults to <queue's own directory>/run_files")
    args = p.parse_args()

    if args.db.startswith("ssh://"):
        print("ERROR: --db must be a local path -- apply_commands.py only ever runs "
              "where the registry actually lives, never over the relay.", file=sys.stderr)
        return 1

    queue_path = Path(args.queue)
    run_files_dir = Path(args.run_files_dir) if args.run_files_dir else queue_path.parent / "run_files"

    if not queue_path.exists():
        print(f"{queue_path}: nothing to apply (file doesn't exist yet).")
        return 0

    data = yaml.safe_load(queue_path.read_text()) or {}
    commands = data.get("commands", [])
    if not commands:
        print(f"{queue_path}: queue is empty.")
        return 0

    applied = 0
    failed = 0
    for entry in commands:
        if entry.get("status") != "pending":
            continue

        action = entry["action"]
        entry_args = entry.get("args", {})
        entry["applied_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if action == "add":
            stage_error = _stage_run_files(entry_args["run_root"], run_files_dir)
            if stage_error:
                entry["status"] = "failed"
                entry["note"] = stage_error
                failed += 1
                print(f"{entry['id']}: FAILED (add, staging files) -- {stage_error}", file=sys.stderr)
                queue_path.write_text(yaml.safe_dump(data, sort_keys=False))
                continue

        cli_args = _args_dict_to_cli(entry_args)
        result = subprocess.run(
            [sys.executable, str(OCEANICU_RUNS), "--db", args.db, action, *cli_args],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            entry["status"] = "applied"
            entry["note"] = result.stdout.strip() or None
            applied += 1
            print(f"{entry['id']}: applied ({action})")
        else:
            entry["status"] = "failed"
            entry["note"] = (result.stderr.strip() or result.stdout.strip() or "unknown error")[-2000:]
            failed += 1
            print(f"{entry['id']}: FAILED ({action}) -- {entry['note']}", file=sys.stderr)

        # Write back after EACH command, not just at the end -- a crash
        # partway through a long queue must not lose already-applied
        # statuses or force re-applying everything from scratch.
        queue_path.write_text(yaml.safe_dump(data, sort_keys=False))

    still_pending = sum(1 for e in commands if e.get("status") == "pending")
    print(f"done: {applied} applied, {failed} failed, {still_pending} still pending.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
