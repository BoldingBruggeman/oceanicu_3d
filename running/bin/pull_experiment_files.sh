#!/bin/bash
# pull_experiment_files.sh -- pull freshly staged experiment files (driver
# script + config) in from bb-server1, symmetric to push_registry_snapshot.sh
# going the other direction. Run this on the LOGIN NODE ONLY (it needs
# outbound reach -- compute nodes don't have it):
#
#   */15 * * * * OCEANICU_EXPERIMENT_ROOT_BASE=/path/experiments \
#       /path/pull_experiment_files.sh bb-server1:/data/OceanICU/oceanicu_3d/experiments
#
# Same --include/--exclude filter as `oceanicu-experiments stage` itself
# (generated*.py/generated*.yaml only, everything else excluded --
# EXPERIMENT_TRACKING.md "Command queue") -- deliberately a strict
# whitelist, not a general directory sync: the remote side of this same
# path is also where real chunk output (logs, restarts, *.nc results)
# lives once an experiment is actually running, and none of that may
# ever be swept up by this. No --delete either, so nothing already
# present here (including the registry DB and its own backups, which
# also live directly under OCEANICU_EXPERIMENT_ROOT_BASE) is ever
# touched by this beyond the whitelisted files it pulls in.
#
# This is what makes get_commands_and_update_registry.py's own
# presence-check on `add` (_verify_experiment_files_present) actually
# reliable on the HPC: by the time a queued `add` is applied there,
# this cron has already had a chance to pull the experiment's files in
# -- `stage` itself only ever wrote them to the machine it was run on,
# never directly to the HPC.
#
# Usage: OCEANICU_EXPERIMENT_ROOT_BASE=/path/experiments pull_experiment_files.sh [remote]
# remote defaults to bb-server1:/data/OceanICU/oceanicu_3d/experiments
set -eu

: "${OCEANICU_EXPERIMENT_ROOT_BASE:?OCEANICU_EXPERIMENT_ROOT_BASE must be set (local experiment tree base)}"
remote="${1:-bb-server1:/data/OceanICU/oceanicu_3d/experiments}"

mkdir -p "$OCEANICU_EXPERIMENT_ROOT_BASE"

result=$(rsync -a -i --prune-empty-dirs \
    --include 'generated*.py' --include 'generated*.yaml' \
    --include '*/' --exclude '*' \
    "${remote%/}/" "${OCEANICU_EXPERIMENT_ROOT_BASE%/}/")

if [ -n "$result" ]; then
    echo "pulled from $remote:"
    echo "$result" | sed 's/^/  /'
else
    echo "$remote: up to date, nothing new."
fi
