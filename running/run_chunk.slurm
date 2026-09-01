#!/bin/bash

# Runs exactly ONE chunk of a registered simulation, then -- if the experiment
# isn't paused and hasn't reached its stop_date -- submits itself again
# for the next chunk. Each submission gets a fresh walltime allocation,
# so there's no need to track remaining walltime mid-job; the chunk that
# just ran either finished within one allocation or it didn't (a chunk
# size chosen sensibly relative to --time should make that a given).
#
# MUST be submitted from INSIDE the directory this script (and
# chunk_runner.py/experiment_tracking.py) actually live in -- SCRIPT_DIR below
# is resolved from $SLURM_SUBMIT_DIR, not this script's own path (see why
# below), so `cd` there first, every time:
#
# Usage (local registry):
#   cd /path/to/running && sbatch --export=ALL,EXPERIMENT_ID='...',OCEANICU_EXPERIMENT_DB='/abs/path/to/experiment_registry.sqlite' run_chunk.slurm
# Usage (registry behind a relay -- see EXPERIMENT_TRACKING.md's "Working across
# machines"; OCEANICU_RELAY_DIR is where run_tracking_server.py lives ON
# the relay, not here):
#   cd /path/to/running && sbatch --export=ALL,EXPERIMENT_ID='...',OCEANICU_EXPERIMENT_DB='ssh://relay/abs/path/db.sqlite',OCEANICU_RELAY_DIR='/abs/path/on/relay' run_chunk.slurm
#
# The leading `ALL,` matters -- without it, --export replaces the job's
# ENTIRE environment with just the listed vars, including dropping HOME
# (confirmed in production, job 11233: HOME ended up as bare "/", so
# `source ~/.bashrc` tried to open "//.bashrc" and failed, cascading into
# "conda: command not found" and every `module load` failing too, since
# .bashrc/PATH setup never ran). `ALL,` inherits the submitting shell's
# full environment first, then adds/overrides EXPERIMENT_ID etc. on top.
#
# EXPERIMENT_ID must already exist in the registry (oceanicu_experiments.py add).
# This script locates chunk_runner.py/experiment_tracking.py via
# $SLURM_SUBMIT_DIR (see SELF/SCRIPT_DIR below), NOT its own path -- deploy
# all three together anywhere on the production machine, no path to
# hardcode or configure for that part, but DO always submit from inside
# that directory (see above).
#
# Pace the hand-off to the next chunk/experiment with `oceanicu_experiments.py delay-all
# --seconds N` (per user, 2026-08-28: "the HPC must be used for something
# else for a while") -- a TIMED pause, checked LIVE right before EACH
# self-resubmission, not a static env var baked in at the first `sbatch`
# (which can't be changed once a job is queued/running). Unlike pause/
# resume (which stop resubmission indefinitely until a human resumes),
# this waits out the remainder then proceeds automatically -- set it,
# forget it, the system catches up on its own. Live-adjustable at any
# time while jobs are already chaining: run `delay-all --seconds N` again
# with a new value, or `delay-all --clear` to cancel early. Never
# interrupts a chunk already running. See experiment_tracking.
# chunk_delay_sentinel_path/get_chunk_delay_remaining for the mechanism
# (a DELAY_ALL file next to the registry DB, mirroring PAUSE_ALL's own
# convention -- content is the delay in seconds, its own mtime marks when
# it was set).

#SBATCH --job-name=oceanicu_chunk
#SBATCH --nodes=3
#SBATCH --exclusive
#SBATCH --ntasks-per-node=64
#SBATCH --cpus-per-task=1
#SBATCH --time=48:00:00
#SBATCH --mail-type=FAIL
#SBATCH --mail-user=rict@pml.ac.uk

set -u

if [ -z "${EXPERIMENT_ID:-}" ]; then
    echo "ERROR: EXPERIMENT_ID must be set (sbatch --export=ALL,EXPERIMENT_ID=... run_chunk.slurm)" >&2
    exit 1
fi

# experiment_tracking.py refuses to guess a DB path, deliberately -- this script
# runs on whatever machine/cluster the job lands on, with whatever folder
# layout THAT machine has, so there is no path that's safe to assume as a
# default here either. Must be set on the sbatch command line:
#   sbatch --export=ALL,EXPERIMENT_ID='...',OCEANICU_EXPERIMENT_DB='...' run_chunk.slurm
# (SLURM's --export replaces the job's entire environment with just what's
# listed, so it has to be given on every submission, not just exported in
# the submitting shell -- every self-resubmission below already carries it
# forward for you once it's set on this first one.)
if [ -z "${OCEANICU_EXPERIMENT_DB:-}" ]; then
    echo "ERROR: OCEANICU_EXPERIMENT_DB must be set (sbatch --export=ALL,EXPERIMENT_ID=...,OCEANICU_EXPERIMENT_DB=... run_chunk.slurm)" >&2
    exit 1
fi

# Where THIS script (and chunk_runner.py/experiment_tracking.py alongside it)
# actually live -- resolved at runtime, not hardcoded, since this script
# is deployed independently on every production machine that runs it
# (different user, different home directory, quite possibly a different
# checkout location entirely -- a fixed path here would only ever be
# right for the one machine it was written on).
#
# Deliberately NOT ${BASH_SOURCE[0]} -- SLURM copies the submitted script's
# CONTENT into a per-job spool directory (/var/spool/slurmd/job<ID>/
# slurm_script) and runs THAT copy, so ${BASH_SOURCE[0]} always resolves to
# the spool copy, never this script's real deployment location (confirmed
# in production, job 11233: "python: can't open file '/var/spool/slurmd/
# job11233/chunk_runner.py'" -- cd "$SCRIPT_DIR" had "succeeded" into the
# spool dir, which exists but never had chunk_runner.py deployed in it).
# $SLURM_SUBMIT_DIR is SLURM's own synthesized var for wherever `sbatch`
# was actually invoked FROM -- unaffected by both the spool-copy behavior
# above and by --export= filtering (it's not part of "the submitting
# shell's environment" that --export controls, same category as
# $SLURM_JOB_ID). Requires `sbatch` to be invoked from INSIDE this
# directory every time (see the header comment above) -- self-resubmission
# below satisfies this automatically, since it runs `sbatch ... "$SELF"`
# only after the `cd "$SCRIPT_DIR"` a few lines down has already happened.
SCRIPT_DIR="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR not set -- sbatch must be invoked from inside the directory run_chunk.slurm/chunk_runner.py/experiment_tracking.py actually live in}"
SELF="$SCRIPT_DIR/run_chunk.slurm"

source ~/.bashrc
conda activate pygetm

export OMP_NUM_THREADS=1
export SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK

module purge
prgenv=igi
vprgenv=2024.1
module load ${prgenv}/${vprgenv}
module load ucx/1.16.0.mlx
module load hdf5-parallel/1.14.5-${prgenv}-${vprgenv}
module load netcdf-hdf5parallel/4.9.2-${prgenv}-${vprgenv}
module load intel_ipp_intel64/latest
export I_MPI_PMI_LIBRARY=/usr/lib64/libpmi2.so

cd "$SCRIPT_DIR" || exit 1

# Live-checks the DELAY_ALL sentinel (oceanicu_experiments.py delay-all --
# experiment_tracking.get_chunk_delay_remaining's own docstring has the full
# mechanism) and, if a delay is currently active, waits it out in short
# polls rather than one fixed sleep -- so a delay that gets shortened,
# extended, or cleared WHILE this is already waiting takes effect right
# away instead of only on the next chunk's own hand-off. No-op (returns
# immediately) when nothing is set.
_wait_for_delay_all() {
    while true; do
        remaining=$(python -c "
import experiment_tracking as rt
with rt.connect() as conn:
    print(int(rt.get_chunk_delay_remaining(conn)))
")
        if [ "$remaining" -le 0 ]; then
            return 0
        fi
        poll=$remaining
        [ "$poll" -gt 60 ] && poll=60
        echo "  DELAY_ALL active: ${remaining}s remaining -- waiting (re-checking in ${poll}s)..."
        sleep "$poll"
    done
}

# Persistent, per-experiment pacing (oceanicu_experiments.py set-chunk-delay / the
# experiment's own chunk_delay_seconds column, default 0) -- unlike DELAY_ALL
# above (a global, one-shot TIMED pause), this is an ongoing setting for
# ONE specific experiment, read fresh each call so a change picks up on the very
# next hand-off. $1 is the experiment_id about to be (re)submitted -- the NEXT
# experiment's own delay applies when picking a new one up from the queue, not
# the experiment that just finished.
_wait_for_experiment_chunk_delay() {
    local target_experiment_id="$1"
    local delay
    delay=$(python -c "
import experiment_tracking as rt
with rt.connect() as conn:
    experiment = rt.get_experiment(conn, '$target_experiment_id')
    print(experiment['chunk_delay_seconds'] if experiment else 0)
")
    if [ "$delay" -gt 0 ]; then
        echo "  $target_experiment_id: chunk_delay_seconds=$delay -- waiting before submitting..."
        sleep "$delay"
    fi
}

# Best-effort, event-driven push of the registry out to bb-server1, right
# before each self-resubmission -- only fires if OCEANICU_LOGIN_NODE is
# set (see setup_experiment_tracking.sh; usually via ~/.bashrc, which this
# script already sources above, and which is visible here unmodified
# since $HOME is shared between login and compute nodes) AND ssh from
# THIS compute node to it actually works. **Confirmed 2026-08-29,
# directly by PML: compute nodes on this project's HPC cannot ssh to
# their own login node at all** (see test_compute_to_login_ssh.sbatch)
# -- so on THIS cluster this never fires, silently and harmlessly; never
# set OCEANICU_LOGIN_NODE here. Kept only for a different cluster where
# compute-to-login ssh might actually work. Backgrounded and silenced: a
# slow or failed push must never delay or block the real resubmission
# that follows it, which proceeds identically either way. On THIS
# cluster the real mechanisms are both on the login node itself, neither
# touching this script at all: a periodic cron running
# push_registry_snapshot.sh, and/or watch_registry_and_push.sh's
# persistent inotify watcher for near-immediate pushes -- see
# EXPERIMENT_TRACKING.md "Keeping bb-server1's copy of the registry up
# to date" / push_registry_snapshot.sh's own header.
_push_registry_snapshot_best_effort() {
    if [ -n "${OCEANICU_LOGIN_NODE:-}" ]; then
        ssh -o BatchMode=yes -o ConnectTimeout=5 "$OCEANICU_LOGIN_NODE" \
            "$SCRIPT_DIR/bin/push_registry_snapshot.sh" >/dev/null 2>&1 &
    fi
}

# --- run the chunk -----------------------------------------------------
# chunk_runner.py itself invokes the driver script (via srun -- the
# preferred launcher, matching the real production SLURM scripts; see
# its own --launcher/the experiment's `launcher` column for the mpiexec
# fallback) and waits for it (it does not background the srun the way
# the old pml.slurm did) -- NaN monitoring runs alongside it here so this
# script can still scancel a runaway job, and records the NaN on the
# chunk's own DB row.

# experiment_root may be relative (registered from a workstation that doesn't
# know exactly where this experiment's output will land HERE) -- resolve it
# against this machine's own OCEANICU_EXPERIMENT_ROOT_BASE, same as
# chunk_runner.py does internally. Absolute experiment_root passes through
# unchanged.
EXPERIMENT_ROOT=$(python -c "
import experiment_tracking as rt
with rt.connect() as conn:
    print(rt.resolve_experiment_root(rt.get_experiment(conn, '$EXPERIMENT_ID')['experiment_root']))
")
POINTER_FILE="$EXPERIMENT_ROOT/.current_chunk_dir"
rm -f "$POINTER_FILE"

python chunk_runner.py --experiment-id "$EXPERIMENT_ID" --slurm-job-id "$SLURM_JOB_ID" &
RUNNER_PID=$!

# Wait for chunk_runner.py to tell us which directory it's using (written
# right before it launches the driver script), then tail that chunk's
# log for NaN for as long as the runner is alive.
for _ in $(seq 1 60); do
    [ -s "$POINTER_FILE" ] && break
    sleep 1
done
LOGFILE="$(cat "$POINTER_FILE" 2>/dev/null)/getm-0000.log"

( shopt -s nocasematch
  # Portable POSIX-ERE word-boundary equivalent -- bash's own [[ =~ ]]
  # (glibc regex via regcomp, no PCRE) does NOT support \b as a word-
  # boundary anchor the way grep -P or Perl do; confirmed by direct
  # testing (\bnan\b silently matched nothing at all, every time,
  # rather than erroring). "nan" must be preceded and followed by
  # either the string boundary or a non-alphanumeric character, so a
  # standalone "nan" token matches (any case, via nocasematch above)
  # but "banana"/"nanometer" don't.
  nan_pattern='(^|[^a-zA-Z0-9])nan($|[^a-zA-Z0-9])'
  tail -n 0 -F "$LOGFILE" 2>/dev/null |
  while IFS= read -r line; do
      # NOT a fixed 'salt:' prefix -- NaN can show up in any logged
      # variable (temp, u, v, elevation, ...), not just salinity.
      #
      # bash's own [[ =~ ]], NOT `echo "$line" | grep -qiE` -- that
      # forked two new processes (echo, grep) for EVERY single line
      # GETM writes, for the entire chunk's runtime. Confirmed causing
      # a real production slowdown (fine for the first ~15 simulated
      # days, then degrading to minutes/day): this script runs with
      # --exclusive --ntasks-per-node=64, so there is zero spare CPU on
      # the node for that forking overhead to absorb -- every fork
      # directly steals cycles from the MPI ranks -- made worse by
      # running on a parallel filesystem (scyllapfs), where process
      # startup (dynamic linking, etc.) is markedly slower than on
      # local disk. [[ =~ ]] does the identical case-insensitive,
      # word-boundaried check with zero process creation.
      if [[ "$line" =~ $nan_pattern ]]; then
          echo "========================================"
          echo "NaN detected! Cancelling SLURM job $SLURM_JOB_ID"
          echo "  offending line: $line"
          echo "========================================"
          scancel "$SLURM_JOB_ID"
          exit 1
      fi
  done ) &
MONITOR_PID=$!

wait "$RUNNER_PID"
EXIT_CODE=$?

kill "$MONITOR_PID" 2>/dev/null
wait "$MONITOR_PID" 2>/dev/null

if [ $EXIT_CODE -eq 1 ]; then
    echo "$EXPERIMENT_ID: nothing to do (already complete or paused) -- not resubmitting."
    exit 0
fi
if [ $EXIT_CODE -ne 0 ]; then
    echo "$EXPERIMENT_ID: chunk failed (exit $EXIT_CODE) -- not resubmitting automatically."
    echo "Investigate, then either 'oceanicu_experiments.py rerun --experiment-id $EXPERIMENT_ID' to redo it,"
    echo "or resubmit this same script once fixed."
    exit $EXIT_CODE
fi

# --- self-chain for the next chunk --------------------------------------
STATUS=$(python -c "
import experiment_tracking as rt
with rt.connect() as conn:
    print(rt.get_experiment(conn, '$EXPERIMENT_ID')['status'])
")

if [ "$STATUS" = "complete" ] || [ "$STATUS" = "complete_with_warnings" ]; then
    echo "$EXPERIMENT_ID: reached stop_date (status=$STATUS)."
    NEXT_EXPERIMENT_ID=$(python -c "
import experiment_tracking as rt
with rt.connect() as conn:
    r = rt.next_experiment_to_start(conn)
    print(r if r else '')
")
    if [ -z "$NEXT_EXPERIMENT_ID" ]; then
        echo "No queued (not_started, unpaused) experiment waiting -- nothing to pick up. Stopping."
        exit 0
    fi
    echo "Picking up next queued experiment: $NEXT_EXPERIMENT_ID"
    _wait_for_delay_all
    _wait_for_experiment_chunk_delay "$NEXT_EXPERIMENT_ID"
    _push_registry_snapshot_best_effort
    sbatch --export=ALL,EXPERIMENT_ID="$NEXT_EXPERIMENT_ID",OCEANICU_EXPERIMENT_DB="$OCEANICU_EXPERIMENT_DB",OCEANICU_RELAY_DIR="${OCEANICU_RELAY_DIR:-}" "$SELF"
    exit 0
fi

PAUSED=$(python -c "
import experiment_tracking as rt
with rt.connect() as conn:
    experiment = rt.get_experiment(conn, '$EXPERIMENT_ID')
    print(rt.is_paused(conn, '$EXPERIMENT_ID', experiment['experiment_root']))
")
if [ "$PAUSED" = "True" ]; then
    echo "$EXPERIMENT_ID: paused -- not resubmitting. 'oceanicu_experiments.py resume --experiment-id $EXPERIMENT_ID' to continue."
    exit 0
fi

_wait_for_delay_all
_wait_for_experiment_chunk_delay "$EXPERIMENT_ID"
_push_registry_snapshot_best_effort
echo "$EXPERIMENT_ID: submitting next chunk"
sbatch --export=ALL,EXPERIMENT_ID="$EXPERIMENT_ID",OCEANICU_EXPERIMENT_DB="$OCEANICU_EXPERIMENT_DB",OCEANICU_RELAY_DIR="${OCEANICU_RELAY_DIR:-}" "$SELF"
