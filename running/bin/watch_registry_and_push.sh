#!/bin/bash
# watch_registry_and_push.sh -- persistent watcher: pushes a fresh
# registry snapshot out to bb-server1 the moment a new one exists,
# instead of waiting for the next cron interval. Run on the LOGIN NODE
# ONLY (needs outbound reach, see push_registry_snapshot.sh).
#
# Meant to be kept alive by restart_registry_watcher.sh's own cron
# watchdog, not started by hand and left unsupervised -- if this process
# gets killed (e.g. by the login node's own idle/session-limit policy,
# common on shared login nodes -- the same reason a bare background
# polling loop was avoided for get_commands_and_update_registry.py, see
# EXPERIMENT_TRACKING.md),
# the watchdog notices within its own interval and starts a fresh one.
# The existing periodic push_registry_snapshot.sh cron (see
# EXPERIMENT_TRACKING.md "Keeping bb-server1's copy of the registry up to
# date") is a further, coarser fallback if even the watchdog somehow
# doesn't fire in time -- keep both, they're cheap and independent.
#
# Writes its own PID to a pidfile next to the registry (see
# restart_registry_watcher.sh) -- deliberately NOT detected via
# `pgrep -f` on the script name, which matches against a process's
# *entire* command line and can false-positive on anything else that
# happens to mention this filename (confirmed hitting exactly this in
# testing -- a wrapping shell's own echoed command text was enough to
# fool it).
#
# Watches the git-backup snapshot FILE itself (experiment_tracking.py's own
# accidental-deletion-protection mechanism, see EXPERIMENT_TRACKING.md), not
# the live registry -- that's the one thing that actually changes
# exactly when there's something new worth pushing (every
# OCEANICU_DB_BACKUP_EVERY_N_WRITES writes; set that to 1 on the HPC for
# "every single write" granularity), not on every WAL-internal write to
# the live database file, most of which aren't yet a real change to ship.
#
# Requires inotify-tools (inotifywait) -- a standard system package, but
# not guaranteed present, and this project's HPC has no root access to
# install one (see EXPERIMENT_TRACKING.md). It's also available via
# conda-forge (confirmed 2026-08-30: `conda search -c conda-forge
# inotify-tools` finds it), installable into an existing env with no
# root needed -- but that env's own bin/ is NOT on PATH in a cron
# context (cron doesn't activate conda, same reason it doesn't source
# ~/.bashrc), so OCEANICU_INOTIFYWAIT lets the crontab line point at
# that env's exact binary directly instead of relying on PATH:
#   OCEANICU_INOTIFYWAIT=/path/to/envs/pygetm/bin/inotifywait
# Falls back to plain `inotifywait` (searched on PATH as normal) if
# unset -- for a system install, or an interactive shell where the
# right conda env is already activated.
#
# Usage: OCEANICU_EXPERIMENT_DB=/path/experiment_registry.sqlite watch_registry_and_push.sh
set -eu

: "${OCEANICU_EXPERIMENT_DB:?OCEANICU_EXPERIMENT_DB must be set (local path to the registry)}"
# push_registry_snapshot.sh lives right here in running/bin, same as
# this script -- plain dirname on BASH_SOURCE[0] is already correct.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAPSHOT="${OCEANICU_EXPERIMENT_DB}.backups/$(basename "$OCEANICU_EXPERIMENT_DB")"
PIDFILE="${OCEANICU_EXPERIMENT_DB}.watcher.pid"
INOTIFYWAIT="${OCEANICU_INOTIFYWAIT:-inotifywait}"

if ! command -v "$INOTIFYWAIT" >/dev/null 2>&1; then
    echo "ERROR: $INOTIFYWAIT not found (package inotify-tools) -- cannot watch." >&2
    exit 1
fi

echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

mkdir -p "$(dirname "$SNAPSHOT")"
touch "$SNAPSHOT"   # inotifywait needs the path to already exist

echo "$(date -Is): watching $SNAPSHOT for changes (pid $$)"
"$INOTIFYWAIT" -m -q -e close_write -e create --format '%w%f' "$SNAPSHOT" | while read -r _changed; do
    echo "$(date -Is): change detected -- pushing"
    "$HERE/push_registry_snapshot.sh" || echo "$(date -Is): push failed, will retry on next change" >&2
done
