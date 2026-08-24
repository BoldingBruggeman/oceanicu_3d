#!/bin/bash

# Runs exactly ONE chunk of a registered simulation, then -- if the run
# isn't paused and hasn't reached its stop_date -- submits itself again
# for the next chunk. Each submission gets a fresh walltime allocation,
# so there's no need to track remaining walltime mid-job; the chunk that
# just ran either finished within one allocation or it didn't (a chunk
# size chosen sensibly relative to --time should make that a given).
#
# Usage (local registry):
#   sbatch --export=RUN_ID='...',OCEANICU_RUN_DB='/abs/path/to/run_registry.sqlite' run_chunk.slurm
# Usage (registry behind a relay -- see RUN_TRACKING.md's "Working across
# machines"; OCEANICU_RELAY_DIR is where run_tracking_server.py lives ON
# the relay, not here):
#   sbatch --export=RUN_ID='...',OCEANICU_RUN_DB='ssh://relay/abs/path/db.sqlite',OCEANICU_RELAY_DIR='/abs/path/on/relay' run_chunk.slurm
#
# RUN_ID must already exist in the registry (oceanicu_runs.py add). This
# script finds its OWN location at runtime (see SELF/SCRIPT_DIR below) --
# deploy it and chunk_runner.py/run_tracking.py together anywhere on the
# production machine, no path to hardcode or configure for that part.

#SBATCH --job-name=oceanicu_chunk
#SBATCH --nodes=3
#SBATCH --exclusive
#SBATCH --ntasks-per-node=64
#SBATCH --cpus-per-task=1
#SBATCH --time=48:00:00
#SBATCH --mail-type=FAIL
#SBATCH --mail-user=rict@pml.ac.uk

set -u

if [ -z "${RUN_ID:-}" ]; then
    echo "ERROR: RUN_ID must be set (sbatch --export=RUN_ID=... run_chunk.slurm)" >&2
    exit 1
fi

# run_tracking.py refuses to guess a DB path, deliberately -- this script
# runs on whatever machine/cluster the job lands on, with whatever folder
# layout THAT machine has, so there is no path that's safe to assume as a
# default here either. Must be set on the sbatch command line:
#   sbatch --export=RUN_ID='...',OCEANICU_RUN_DB='...' run_chunk.slurm
# (SLURM's --export replaces the job's entire environment with just what's
# listed, so it has to be given on every submission, not just exported in
# the submitting shell -- every self-resubmission below already carries it
# forward for you once it's set on this first one.)
if [ -z "${OCEANICU_RUN_DB:-}" ]; then
    echo "ERROR: OCEANICU_RUN_DB must be set (sbatch --export=RUN_ID=...,OCEANICU_RUN_DB=... run_chunk.slurm)" >&2
    exit 1
fi

# Where THIS script (and chunk_runner.py/run_tracking.py alongside it)
# actually live -- resolved at runtime, not hardcoded, since this script
# is deployed independently on every production machine that runs it
# (different user, different home directory, quite possibly a different
# checkout location entirely -- a fixed path here would only ever be
# right for the one machine it was written on). SELF is also used below
# for self-resubmission instead of bare "$0", which SLURM doesn't
# guarantee stays a reliable absolute path across every configuration.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$SELF")"

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

# --- run the chunk -----------------------------------------------------
# chunk_runner.py itself invokes the driver script (via srun -- the
# preferred launcher, matching the real production SLURM scripts; see
# its own --launcher/the run's `launcher` column for the mpiexec
# fallback) and waits for it (it does not background the srun the way
# the old pml.slurm did) -- NaN monitoring runs alongside it here so this
# script can still scancel a runaway job, and records the NaN on the
# chunk's own DB row.

RUN_ROOT=$(python -c "
import run_tracking as rt
with rt.connect() as conn:
    print(rt.get_run(conn, '$RUN_ID')['run_root'])
")
POINTER_FILE="$RUN_ROOT/.current_chunk_dir"
rm -f "$POINTER_FILE"

python chunk_runner.py --run-id "$RUN_ID" --slurm-job-id "$SLURM_JOB_ID" &
RUNNER_PID=$!

# Wait for chunk_runner.py to tell us which directory it's using (written
# right before it launches the driver script), then tail that chunk's
# log for NaN for as long as the runner is alive.
for _ in $(seq 1 60); do
    [ -s "$POINTER_FILE" ] && break
    sleep 1
done
LOGFILE="$(cat "$POINTER_FILE" 2>/dev/null)/getm-0000.log"

( tail -n 0 -F "$LOGFILE" 2>/dev/null |
  while IFS= read -r line; do
      # \bnan\b, not a fixed 'salt:' prefix -- NaN can show up in any
      # logged variable (temp, u, v, elevation, ...), not just salinity;
      # word-boundaried so it only matches a standalone "nan" token, not
      # a substring inside some unrelated word.
      if echo "$line" | grep -qiE '\bnan\b'; then
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
    echo "$RUN_ID: nothing to do (already complete or paused) -- not resubmitting."
    exit 0
fi
if [ $EXIT_CODE -ne 0 ]; then
    echo "$RUN_ID: chunk failed (exit $EXIT_CODE) -- not resubmitting automatically."
    echo "Investigate, then either 'oceanicu_runs.py rerun --run-id $RUN_ID' to redo it,"
    echo "or resubmit this same script once fixed."
    exit $EXIT_CODE
fi

# --- self-chain for the next chunk --------------------------------------
STATUS=$(python -c "
import run_tracking as rt
with rt.connect() as conn:
    print(rt.get_run(conn, '$RUN_ID')['status'])
")

if [ "$STATUS" = "complete" ] || [ "$STATUS" = "complete_with_warnings" ]; then
    echo "$RUN_ID: reached stop_date (status=$STATUS)."
    NEXT_RUN_ID=$(python -c "
import run_tracking as rt
with rt.connect() as conn:
    r = rt.next_run_to_start(conn)
    print(r if r else '')
")
    if [ -z "$NEXT_RUN_ID" ]; then
        echo "No queued (not_started, unpaused) run waiting -- nothing to pick up. Stopping."
        exit 0
    fi
    echo "Picking up next queued run: $NEXT_RUN_ID"
    sbatch --export=RUN_ID="$NEXT_RUN_ID",OCEANICU_RUN_DB="$OCEANICU_RUN_DB",OCEANICU_RELAY_DIR="${OCEANICU_RELAY_DIR:-}" "$SELF"
    exit 0
fi

PAUSED=$(python -c "
import run_tracking as rt
with rt.connect() as conn:
    run = rt.get_run(conn, '$RUN_ID')
    print(rt.is_paused(conn, '$RUN_ID', run['run_root']))
")
if [ "$PAUSED" = "True" ]; then
    echo "$RUN_ID: paused -- not resubmitting. 'oceanicu_runs.py resume --run-id $RUN_ID' to continue."
    exit 0
fi

echo "$RUN_ID: submitting next chunk"
sbatch --export=RUN_ID="$RUN_ID",OCEANICU_RUN_DB="$OCEANICU_RUN_DB",OCEANICU_RELAY_DIR="${OCEANICU_RELAY_DIR:-}" "$SELF"
