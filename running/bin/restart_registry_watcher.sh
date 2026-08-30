#!/bin/bash
# restart_registry_watcher.sh -- cron watchdog for watch_registry_and_push.sh:
# checks whether that watcher is actually running, (re)starts it if not.
# Run on the LOGIN NODE ONLY, from cron:
#   0 * * * * OCEANICU_RUN_DB=/path/run_registry.sqlite /path/restart_registry_watcher.sh
#
# Once an hour is plenty -- the watcher is meant to stay running
# indefinitely between checks; this only recovers from it having been
# killed (e.g. by the login node's own idle/session-limit policy). Not a
# substitute for the separate push_registry_snapshot.sh cron -- keep
# both: this gives near-immediate pushes when the watcher is alive, that
# one guarantees a push at least every interval regardless.
#
# Detects a live watcher via its pidfile (kill -0 on the recorded PID),
# NOT `pgrep -f` on the script name -- pgrep -f matches a process's
# entire command line, and testing this confirmed it false-positives on
# anything else that happens to mention the filename (e.g. a wrapping
# shell's own echoed command text). A stale pidfile (process gone,
# file left behind by an unclean kill) is treated the same as no
# pidfile at all.
set -eu

: "${OCEANICU_RUN_DB:?OCEANICU_RUN_DB must be set}"
# Plain dirname is enough: watch_registry_and_push.sh lives right here
# in running/bin, same as this script, whether invoked by its own name
# or via the restart-registry-watcher symlink next to it (same
# directory either way, so BASH_SOURCE[0]'s own dirname is already
# correct).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${OCEANICU_RUN_DB}.watcher.log"
PIDFILE="${OCEANICU_RUN_DB}.watcher.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    exit 0   # already running, nothing to do
fi

echo "$(date -Is): registry watcher not running (or pidfile stale) -- starting it" >> "$LOG"
nohup "$HERE/watch_registry_and_push.sh" >> "$LOG" 2>&1 < /dev/null &
disown
