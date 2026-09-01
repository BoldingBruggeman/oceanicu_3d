#!/bin/bash
# push_registry_snapshot.sh -- push the latest registry snapshot out to
# bb-server1, read-only on arrival. Run this on the LOGIN NODE ONLY (it
# needs outbound reach -- compute nodes don't have it). Three ways to
# trigger it, not mutually exclusive:
#
#   1. A login-node cron job (always-on fallback, works regardless of
#      whether ssh from compute nodes to here is even possible):
#        */10 * * * * OCEANICU_EXPERIMENT_DB=/path/submission_registry.sqlite /path/push_registry_snapshot.sh
#
#   2. watch_registry_and_push.sh -- a persistent inotify watcher, run on
#      the LOGIN NODE itself (nothing to do with run_chunk.slurm or any
#      compute node), that calls this script the instant a fresh
#      snapshot commit appears. This is the near-immediate path on this
#      project's actual HPC -- see EXPERIMENT_TRACKING.md "Keeping
#      bb-server1's copy of the registry up to date".
#
#   3. Best-effort, event-driven, from run_chunk.slurm itself right
#      before each self-resubmission -- only if OCEANICU_LOGIN_NODE is
#      set (see setup_experiment_tracking.sh) AND ssh from a compute
#      node to here actually works. **Confirmed 2026-08-29, directly by
#      PML: compute nodes on this project's HPC cannot ssh to their own
#      login node at all** (see test_compute_to_login_ssh.sbatch) -- so
#      on THIS cluster this path never fires, silently and harmlessly.
#      Kept in the code only for a different cluster where compute-to-
#      login ssh might actually work; never set OCEANICU_LOGIN_NODE here.
#
# Doesn't take a new snapshot itself -- reuses experiment_tracking.py's own
# git-backup mechanism (already WAL-safe, already committed after every
# OCEANICU_DB_BACKUP_EVERY_N_WRITES writes, see EXPERIMENT_TRACKING.md
# "Accidental-deletion protection") as the source; just ships out
# whatever's already there.
#
# Usage: OCEANICU_EXPERIMENT_DB=/path/submission_registry.sqlite push_registry_snapshot.sh [dest]
# dest defaults to bb-server1:/data/OceanICU/oceanicu_3d/experiments/<same filename>
set -eu

: "${OCEANICU_EXPERIMENT_DB:?OCEANICU_EXPERIMENT_DB must be set (local path to the registry)}"

snapshot="${OCEANICU_EXPERIMENT_DB}.backups/$(basename "$OCEANICU_EXPERIMENT_DB")"
dest="${1:-bb-server1:/data/OceanICU/oceanicu_3d/experiments/$(basename "$OCEANICU_EXPERIMENT_DB")}"

if [ ! -f "$snapshot" ]; then
    echo "$snapshot does not exist yet -- nothing to push (no writes have" >&2
    echo "happened yet, or OCEANICU_DB_BACKUP_EVERY_N_WRITES hasn't been reached once)." >&2
    exit 0
fi

# Safety guard: refuse to push a snapshot with ZERO experiments if the
# remote already has real ones -- added after a real incident (2026-08-31)
# where something emptied the live registry on the HPC, and the routine
# push then silently overwrote bb-server1's own good copy with that empty
# state, destroying the only other copy that could have shown something
# was wrong. This doesn't fix whatever emptied the source DB in the first
# place -- it just stops that corruption from propagating and destroying
# the one remaining good copy.
#
# Uses `python3 -c` + the sqlite3 STDLIB module, not the external sqlite3
# CLI binary -- python3 is already a hard requirement everywhere in this
# pipeline (chunk_runner.py, experiment_tracking.py, ...), the CLI tool is
# not guaranteed installed on every login node. Best-effort either way: if
# python3 is missing, the remote is unreachable, or the remote file
# doesn't exist yet, this can't tell either way and the push proceeds as
# before (never block a legitimate push just because the safety check
# itself couldn't run). Override with OCEANICU_ALLOW_EMPTY_PUSH=1 for the
# rare legitimate case (e.g. every experiment really has been removed on
# purpose).
_count_experiments_py='
import sqlite3, sys
try:
    conn = sqlite3.connect(sys.argv[1])
    print(conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0])
except Exception:
    print("")
'
if [ -z "${OCEANICU_ALLOW_EMPTY_PUSH:-}" ] && command -v python3 >/dev/null 2>&1; then
    local_count=$(python3 -c "$_count_experiments_py" "$snapshot" 2>/dev/null || echo "")
    if [ "$local_count" = "0" ]; then
        dest_host="${dest%%:*}"
        dest_path="${dest#*:}"
        remote_count=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$dest_host" \
            "python3 -c '$_count_experiments_py' '$dest_path'" 2>/dev/null || echo "")
        if [ -n "$remote_count" ] && [ "$remote_count" != "0" ]; then
            echo "REFUSING to push: local snapshot $snapshot has 0 experiments, but" >&2
            echo "$dest already has $remote_count -- this looks like the local registry" >&2
            echo "was emptied by something, not a real remove-everything. Pushing would" >&2
            echo "destroy the only other good copy. Investigate the local DB first (its" >&2
            echo "own git-backup history at ${OCEANICU_EXPERIMENT_DB}.backups/ may still" >&2
            echo "have the last good state). Set OCEANICU_ALLOW_EMPTY_PUSH=1 to push" >&2
            echo "anyway if every experiment really has been removed on purpose." >&2
            exit 1
        fi
    fi
fi

# --chmod=a-w: read-only on arrival, so an accidental write against this
# copy on bb-server1 fails loudly instead of silently diverging the
# mirror. rsync replaces the destination file via rename, not in-place
# overwrite, so a previously-read-only destination doesn't block this.
# --checksum: rsync's default "quick check" (size+mtime) can skip a real
# change if two snapshots happen to land on the same size within the
# same second (confirmed this actually happens) -- checksum comparison
# is the only way to guarantee a real content diff always gets copied.
# Cheap enough for a registry this size.
rsync -a --checksum --chmod=a-w "$snapshot" "$dest"
echo "pushed $snapshot -> $dest (read-only)"
