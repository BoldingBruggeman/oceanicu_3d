#!/bin/bash
# push_experiment_logs.sh -- push getm*.log files out to bb-server1,
# mirroring OCEANICU_EXPERIMENT_ROOT_BASE's own relative structure (the
# same NSe/{CMEMS,WOA,CMIP6,...}/<model>/<scenario>/<run>/ layout already
# used on the HPC). Run this on the LOGIN NODE ONLY (it needs outbound
# reach -- compute nodes don't have it; confirmed 2026-08-29, directly by
# PML, see push_registry_snapshot.sh's own header) -- e.g. via cron:
#
#   */15 * * * * OCEANICU_EXPERIMENT_ROOT_BASE=/path/experiments \
#       push_experiment_logs.sh
#
# Deliberately NOT triggered from inside run_chunk.slurm on chunk
# finish -- that runs on a COMPUTE node, which can't reach bb-server1
# either (same restriction). A periodic login-node push covers "on
# finish, success or failure" well enough without needing exit-code-
# aware logic in the job script: getm*.log exists and is meaningful
# either way, so there's nothing to special-case.
#
# Same --include/--prune-empty-dirs shape as pull_experiment_files.sh,
# reversed in direction -- getm*.log only, everything else (driver
# scripts, *.nc output, restarts) excluded, so this never duplicates
# what `stage`/pull_experiment_files.sh or a real output sync already
# handle. -u (update): skip any file newer on bb-server1 than here --
# harmless here (nobody edits a log on arrival) but cheap, consistent
# insurance, same as pull_experiment_files.sh's own use of it.
#
# Usage: OCEANICU_EXPERIMENT_ROOT_BASE=/path/experiments push_experiment_logs.sh [dest]
# dest defaults to bb-server1:/data/OceanICU/oceanicu_3d/experiments
set -eu

: "${OCEANICU_EXPERIMENT_ROOT_BASE:?OCEANICU_EXPERIMENT_ROOT_BASE must be set (local experiment tree base)}"
dest="${1:-bb-server1:/data/OceanICU/oceanicu_3d/experiments}"

result=$(rsync -au -i --prune-empty-dirs \
    --include 'getm*.log' \
    --include '*/' --exclude '*' \
    "${OCEANICU_EXPERIMENT_ROOT_BASE%/}/" "${dest%/}/")

if [ -n "$result" ]; then
    echo "$(date -Is): pushed to $dest:"
    echo "$result" | sed 's/^/  /'
else
    echo "$(date -Is): $dest: up to date, nothing new."
fi
