#!/bin/bash
# restart_registry_watcher.sh -- cron watchdog for watch_registry_and_push.sh:
# checks whether that watcher is actually running, (re)starts it if not.
# Run on the LOGIN NODE ONLY, from cron:
#   0 * * * * OCEANICU_EXPERIMENT_DB=/path/submission_registry.sqlite /path/restart_registry_watcher.sh
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
#
# Checks for `inotifywait` itself before even trying to (re)start the
# watcher -- without it, watch_registry_and_push.sh exits immediately
# and never writes a pidfile, so a naive watchdog would just retry that
# doomed start every single interval, forever, forever logging the same
# failure. Instead this logs ONE clear message the first time it's
# missing (a sentinel file suppresses repeats) and exits without
# attempting to start anything -- self-healing if inotify-tools gets
# installed later (the presence check itself always re-runs fresh; only
# the log message is throttled).
#
# OCEANICU_INOTIFYWAIT (optional): same override watch_registry_and_push.sh
# itself respects, for pointing at a conda-installed inotifywait (e.g.
# in the pygetm env -- confirmed available via conda-forge 2026-08-30)
# when there's no root to install the system package. Set once here in
# the crontab line and it's automatically visible to the watcher too,
# once (re)started below -- plain env-var inheritance, nothing extra
# needed. Falls back to plain `inotifywait` on PATH if unset.
set -eu

: "${OCEANICU_EXPERIMENT_DB:?OCEANICU_EXPERIMENT_DB must be set}"
# watch_registry_and_push.sh lives right here in running/bin, same as
# this script -- plain dirname on BASH_SOURCE[0] is already correct.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${OCEANICU_EXPERIMENT_DB}.watcher.log"
PIDFILE="${OCEANICU_EXPERIMENT_DB}.watcher.pid"
NO_INOTIFY_FLAG="${OCEANICU_EXPERIMENT_DB}.watcher.no_inotify_warned"
INOTIFYWAIT="${OCEANICU_INOTIFYWAIT:-inotifywait}"

if ! command -v "$INOTIFYWAIT" >/dev/null 2>&1; then
    if [ ! -f "$NO_INOTIFY_FLAG" ]; then
        echo "$(date -Is): $INOTIFYWAIT not found (package inotify-tools) -- giving up on" >> "$LOG"
        echo "  the watcher until it's installed (not retrying every interval; the" >> "$LOG"
        echo "  separate push_registry_snapshot.sh cron is the real fallback either" >> "$LOG"
        echo "  way). This message only prints once -- it reappears automatically" >> "$LOG"
        echo "  if $INOTIFYWAIT goes missing again after having worked." >> "$LOG"
        touch "$NO_INOTIFY_FLAG"
    fi
    exit 0
fi
rm -f "$NO_INOTIFY_FLAG"   # available again (or always was) -- clear any prior warning

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    exit 0   # already running, nothing to do
fi

echo "$(date -Is): registry watcher not running (or pidfile stale) -- starting it" >> "$LOG"
nohup "$HERE/watch_registry_and_push.sh" >> "$LOG" 2>&1 < /dev/null &
disown
