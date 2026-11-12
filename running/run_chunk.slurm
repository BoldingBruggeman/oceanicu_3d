#!/bin/bash

# Runs ONE chunk of a registered experiment, then, unless it is paused or
# complete, submits itself for the next chunk (or the next queued
# experiment). Each chunk gets a fresh walltime allocation.
#
# Submit from inside running/ (SCRIPT_DIR comes from $SLURM_SUBMIT_DIR):
#   cd /path/to/running && sbatch --export=ALL,EXPERIMENT_ID='...',OCEANICU_EXPERIMENT_DB='/abs/path/to/experiment_registry.sqlite' run_chunk.slurm
# With the registry behind a relay (EXPERIMENT_TRACKING.md "Working across
# machines"; OCEANICU_RELAY_DIR is where experiment_tracking_server.py lives
# on the relay):
#   cd /path/to/running && sbatch --export=ALL,EXPERIMENT_ID='...',OCEANICU_EXPERIMENT_DB='ssh://relay/abs/path/db.sqlite',OCEANICU_RELAY_DIR='/abs/path/on/relay' run_chunk.slurm
#
# Keep the leading `ALL,`: without it --export replaces the whole job
# environment (HOME included), and module/conda setup fails.
#
# EXPERIMENT_ID must already be registered (oceanicu_experiments.py add).
# `oceanicu_experiments.py delay-all --seconds N` delays every hand-off to
# the next chunk (checked live before each resubmission; --clear cancels);
# set-chunk-delay paces one experiment. Neither interrupts a running chunk.

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

# No default DB path: it differs per machine. Self-resubmissions carry it on.
if [ -z "${OCEANICU_EXPERIMENT_DB:-}" ]; then
    echo "ERROR: OCEANICU_EXPERIMENT_DB must be set (sbatch --export=ALL,EXPERIMENT_ID=...,OCEANICU_EXPERIMENT_DB=... run_chunk.slurm)" >&2
    exit 1
fi

# Not ${BASH_SOURCE[0]}: SLURM runs a spool copy of this script.
# $SLURM_SUBMIT_DIR is where sbatch was invoked, i.e. running/
# (self-resubmission happens after the cd below).
SCRIPT_DIR="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR not set -- sbatch must be invoked from inside the directory run_chunk.slurm/chunk_runner.py/experiment_tracking.py actually live in}"
SELF="$SCRIPT_DIR/run_chunk.slurm"
export OMP_NUM_THREADS=1
export SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK

# ~/.bashrc is not sourced here (it breaks a manual sbatch); `module` comes
# from the submitting environment via --export=ALL -- an interactive shell,
# or cmd_submit_chunk, which sources ~/.bashrc itself for the queue path.

module purge
prgenv=igi
vprgenv=2024.1
module load ${prgenv}/${vprgenv}
module load ucx/1.16.0.mlx
module load hdf5-parallel/1.14.5-${prgenv}-${vprgenv}
module load netcdf-hdf5parallel/4.9.2-${prgenv}-${vprgenv}
module load intel_ipp_intel64/latest
export I_MPI_PMI_LIBRARY=/usr/lib64/libpmi2.so
#export I_MPI_ASYNC_PROGRESS=1
#export MPI4PY_RC_THREADS=true
#export MPI4PY_RC_THREAD_LEVEL=multiple

#source /users/modellers/rict/.bashrc
source /scyllapfs/sw/miniforge3/etc/profile.d/conda.sh
conda activate pygetm


cd "$SCRIPT_DIR" || exit 1

# Wait out an active DELAY_ALL, re-checking every <=60 s so a changed or
# cleared delay takes effect at once.
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

# Per-experiment pacing (chunk_delay_seconds), read fresh each time; $1 is
# the experiment about to be submitted.
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

# Push the registry to bb-server1 before resubmitting, if OCEANICU_LOGIN_NODE
# is set and reachable. Compute nodes on scylla cannot ssh to the login node
# (investigations/test_compute_to_login_ssh.sbatch), so leave it unset
# there; the login node's cron/watch_registry_and_push.sh does the push.
# Backgrounded: never delays the resubmission.
_push_registry_snapshot_best_effort() {
    if [ -n "${OCEANICU_LOGIN_NODE:-}" ]; then
        ssh -o BatchMode=yes -o ConnectTimeout=5 "$OCEANICU_LOGIN_NODE" \
            "$SCRIPT_DIR/bin/push_registry_snapshot.sh" >/dev/null 2>&1 &
    fi
}

# --- run the chunk -----------------------------------------------------
# chunk_runner.py launches the driver script (srun, or the experiment's
# `launcher`) and waits for it. Exit codes: 0 chunk done, 1 nothing to do
# (complete, paused or already running), anything else a failure.
python chunk_runner.py --experiment-id "$EXPERIMENT_ID" --slurm-job-id "$SLURM_JOB_ID"
EXIT_CODE=$?

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
