#!/bin/bash
# Loop over chunks of a simulation using the SAME chunk_runner.py/
# oceanicu_experiments.py the real HPC uses, but driven by a plain bash
# loop instead of SLURM self-resubmission -- for test runs on bb-server1
# (or any other non-SLURM machine).
#
# Run this FROM the experiment's own directory (the one holding its
# generated_*.py / generated_*_config.yaml pair) -- experiment-id and
# experiment-root are derived from that directory (relative to
# $OCEANICU_EXPERIMENT_ROOT_BASE), not passed on the command line.
#
# Usage:
#   cd $OCEANICU_EXPERIMENT_ROOT_BASE/NSe/CMIP6_raw/run01/3
#   /path/to/running/run_chunks_local.sh --start 2014-01-01 --stop 2016-01-01
#   /path/to/running/run_chunks_local.sh --start 2014-01-01 --stop 2016-01-01 --chunk-kind monthly --chunk-multiplier 3 --np 20
#
# Registers into a THROWAWAY /tmp/ registry, never the real HPC one --
# oceanicu_experiments.py add refuses a direct add anywhere else (bb-server1's
# mirror sits at the same DB path as the authoritative HPC registry; see
# EXPERIMENT_TRACKING.md "Set up an experiment"). Re-running this script
# against an already-registered experiment-id just resumes the loop --
# --start/--stop/--chunk-kind/--chunk-multiplier are ignored that time.
#
# See run_chunks_local.py / running/bin/run-chunks-local for the same tool,
# reimplemented in Python -- both kept for now.
set -uo pipefail

RUNNING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_ROOT="$(pwd)"

START=""
STOP=""
CHUNK_KIND=""
CHUNK_MULTIPLIER=""
NP=30
DATA_ROOTS_FILE="/data/OceanICU/oceanicu_3d/experiments/NSe/bb-server1_data_roots.yaml"
while [ $# -gt 0 ]; do
    case "$1" in
        --start) START="$2"; shift 2 ;;
        --stop) STOP="$2"; shift 2 ;;
        --chunk-kind) CHUNK_KIND="$2"; shift 2 ;;
        --chunk-multiplier) CHUNK_MULTIPLIER="$2"; shift 2 ;;
        --np) NP="$2"; shift 2 ;;
        --data-roots-file) DATA_ROOTS_FILE="$2"; shift 2 ;;
        *) echo "ERROR: unknown argument $1" >&2; exit 1 ;;
    esac
done
if [ -z "$START" ] || [ -z "$STOP" ]; then
    echo "Usage: $0 --start YYYY-MM-DD --stop YYYY-MM-DD [--chunk-kind annual|monthly|daily] [--chunk-multiplier N] [--np N] [--data-roots-file PATH]" >&2
    exit 1
fi

: "${OCEANICU_EXPERIMENT_ROOT_BASE:?OCEANICU_EXPERIMENT_ROOT_BASE must be set (see running/experiment_defaults.yaml / kb.bash)}"
EXPERIMENT_ID="$(realpath --relative-to="$OCEANICU_EXPERIMENT_ROOT_BASE" "$EXPERIMENT_ROOT")"

SCRIPT="$(ls generated_*.py 2>/dev/null | grep -v '_utils\.py$' | head -1)"
if [ -z "$SCRIPT" ]; then
    echo "ERROR: no generated_*.py found in $EXPERIMENT_ROOT (run this from the experiment's own directory)" >&2
    exit 1
fi
CONFIG="${SCRIPT%.py}_config.yaml"
if [ ! -f "$CONFIG" ]; then
    echo "ERROR: expected companion config $CONFIG next to $SCRIPT, not found" >&2
    exit 1
fi

# Never the real registry -- see header comment. oceanicu_experiments.py's own
# add-refusal check exempts /tmp/ paths specifically for this.
export OCEANICU_EXPERIMENT_DB="${OCEANICU_EXPERIMENT_DB:-/tmp/oceanicu_local_registry.sqlite}"
case "$OCEANICU_EXPERIMENT_DB" in
    /tmp/*) ;;
    *)
        echo "ERROR: OCEANICU_EXPERIMENT_DB must be a /tmp/ scratch path for local test runs" >&2
        echo "  (got $OCEANICU_EXPERIMENT_DB) -- never point this at the real HPC registry from here." >&2
        exit 1
        ;;
esac

ALREADY_REGISTERED="$(PYTHONPATH="$RUNNING_DIR" python3 -c "
import experiment_tracking as rt
with rt.connect('$OCEANICU_EXPERIMENT_DB') as conn:
    print('yes' if rt.get_experiment(conn, '$EXPERIMENT_ID') else 'no')
")"

if [ "$ALREADY_REGISTERED" = "no" ]; then
    echo "Registering $EXPERIMENT_ID (root: $EXPERIMENT_ROOT) in local test registry $OCEANICU_EXPERIMENT_DB ..."
    ADD_ARGS=(--experiment-id "$EXPERIMENT_ID" --experiment-root "$EXPERIMENT_ROOT"
              --script "$SCRIPT" --config "$CONFIG"
              --initial-date "$START" --stop-date "$STOP"
              --data-roots-file "$DATA_ROOTS_FILE"
              --np "$NP" --launcher mpiexec --db "$OCEANICU_EXPERIMENT_DB")
    [ -n "$CHUNK_KIND" ] && ADD_ARGS+=(--chunk-kind "$CHUNK_KIND")
    [ -n "$CHUNK_MULTIPLIER" ] && ADD_ARGS+=(--chunk-multiplier "$CHUNK_MULTIPLIER")
    python3 "$RUNNING_DIR/oceanicu_experiments.py" add "${ADD_ARGS[@]}" || exit $?
else
    echo "$EXPERIMENT_ID already registered in $OCEANICU_EXPERIMENT_DB -- resuming (ignoring any --start/--stop/--chunk-kind/--chunk-multiplier given now)."
fi

echo "Looping chunk_runner.py for $EXPERIMENT_ID (Ctrl-C to stop; re-run this script to resume) ..."
while true; do
    python3 "$RUNNING_DIR/chunk_runner.py" --experiment-id "$EXPERIMENT_ID" --db "$OCEANICU_EXPERIMENT_DB"
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 1 ]; then
        echo "$EXPERIMENT_ID: nothing to do (already complete or paused). Stopping."
        exit 0
    fi
    if [ $EXIT_CODE -ne 0 ]; then
        echo "$EXPERIMENT_ID: chunk failed (exit $EXIT_CODE). Stopping -- fix the issue, then re-run this script to resume." >&2
        exit $EXIT_CODE
    fi
done
