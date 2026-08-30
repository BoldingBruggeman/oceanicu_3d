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
# RUN_TRACKING.md),
# the watchdog notices within its own interval and starts a fresh one.
# The existing periodic push_registry_snapshot.sh cron (see
# RUN_TRACKING.md "Keeping bb-server1's copy of the registry up to
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
# Watches the git-backup snapshot FILE itself (run_tracking.py's own
# accidental-deletion-protection mechanism, see RUN_TRACKING.md), not
# the live registry -- that's the one thing that actually changes
# exactly when there's something new worth pushing (every
# OCEANICU_DB_BACKUP_EVERY_N_WRITES writes; set that to 1 on the HPC for
# "every single write" granularity), not on every WAL-internal write to
# the live database file, most of which aren't yet a real change to ship.
#
# Requires inotify-tools (inotifywait) -- a standard package, but not
# guaranteed present; check with `command -v inotifywait` before relying
# on this. Not tested against a real inotifywait binary in the session
# that wrote this (unavailable in that sandbox without sudo) -- verify
# it actually fires as expected on the real login node before trusting
# it exclusively; the periodic cron fallback above covers you either way.
#
# Usage: OCEANICU_RUN_DB=/path/run_registry.sqlite watch_registry_and_push.sh
set -eu

: "${OCEANICU_RUN_DB:?OCEANICU_RUN_DB must be set (local path to the registry)}"
# Plain dirname is enough: push_registry_snapshot.sh lives right here in
# running/bin, same as this script, whether invoked by its own name or
# via the watch-registry-and-push symlink next to it (same directory
# either way, so BASH_SOURCE[0]'s own dirname is already correct).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAPSHOT="${OCEANICU_RUN_DB}.backups/$(basename "$OCEANICU_RUN_DB")"
PIDFILE="${OCEANICU_RUN_DB}.watcher.pid"

if ! command -v inotifywait >/dev/null 2>&1; then
    echo "ERROR: inotifywait not found (package inotify-tools) -- cannot watch." >&2
    exit 1
fi

echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

mkdir -p "$(dirname "$SNAPSHOT")"
touch "$SNAPSHOT"   # inotifywait needs the path to already exist

echo "$(date -Is): watching $SNAPSHOT for changes (pid $$)"
inotifywait -m -q -e close_write -e create --format '%w%f' "$SNAPSHOT" | while read -r _changed; do
    echo "$(date -Is): change detected -- pushing"
    "$HERE/push_registry_snapshot.sh" || echo "$(date -Is): push failed, will retry on next change" >&2
done
