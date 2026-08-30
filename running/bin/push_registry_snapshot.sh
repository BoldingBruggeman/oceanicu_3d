#!/bin/bash
# push_registry_snapshot.sh -- push the latest registry snapshot out to
# bb-server1, read-only on arrival. Run this on the LOGIN NODE ONLY (it
# needs outbound reach -- compute nodes don't have it). Three ways to
# trigger it, not mutually exclusive:
#
#   1. A login-node cron job (always-on fallback, works regardless of
#      whether ssh from compute nodes to here is even possible):
#        */10 * * * * OCEANICU_EXPERIMENT_DB=/path/experiment_registry.sqlite /path/push_registry_snapshot.sh
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
# Usage: OCEANICU_EXPERIMENT_DB=/path/experiment_registry.sqlite push_registry_snapshot.sh [dest]
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
