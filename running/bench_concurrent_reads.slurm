#!/bin/bash
#
# Real-cluster version of the bb-server1 concurrent-tile read benchmark in
# docs/chunk-pace-analysis.md ("Concurrent-reader benchmark: realistic
# per-MPI-rank tile sizes"). That benchmark's absolute numbers were flagged
# as non-transferable: bb-server1 is one box with one local disk and 64
# real cores (184 tasks there means 3x CPU oversubscription); THIS cluster
# is the real target -- 3 nodes, 184 real cores, a parallel filesystem
# (scyllapfs) instead of local disk. This script re-runs the same paired
# OLD-vs-NEW comparison there, for a number that actually transfers.
#
# No MPI/mpi4py needed or used -- see bench_concurrent_reads_task.py's own
# header for why (pygetm's real per-rank reads have no inter-rank
# communication either, so none is needed here to reproduce that pattern).
# Each `srun` step below IS the synchronisation point between phases: a
# step doesn't return until every task in it has exited, so the OLD-file
# tasks are guaranteed to have finished before the NEW-file tasks for the
# same N start -- no overlap between the two.
#
# Before submitting, stage the two matched test files onto scyllapfs (see
# the file-transfer note in the PR/chat this shipped with -- NOT the full
# archive, just this one matched pair, ~1GB each):
#   OLD (bad chunking, untouched):  GFDL-ESM4/ssp370/meteo/huss_bc_bilinear_ssp370_disagg_2098.nc
#   NEW (fixed chunking, already rewritten on bb-server1): GFDL-ESM4/ssp126/meteo/huss_bc_bilinear_ssp126_disagg_2098.nc
# Point OLD_FILE/NEW_FILE below at wherever they land, e.g. under
# /work/shared/oceanICU/tmp_chunk_bench/.
#
# Usage (from inside this directory, alongside bench_concurrent_reads_task.py
# and bench_collect_results.py -- deploy all three together):
#   sbatch --export=ALL,OLD_FILE='/work/shared/oceanICU/tmp_chunk_bench/huss_OLD.nc',NEW_FILE='/work/shared/oceanICU/tmp_chunk_bench/huss_NEW.nc' bench_concurrent_reads.slurm

#SBATCH --job-name=disagg_chunk_bench
#SBATCH --nodes=3
#SBATCH --ntasks-per-node=64
#SBATCH --cpus-per-task=1
#SBATCH --exclusive
#SBATCH --time=00:20:00
#SBATCH --output=disagg_chunk_bench_%j.out

set -u

if [ -z "${OLD_FILE:-}" ] || [ -z "${NEW_FILE:-}" ]; then
    echo "ERROR: OLD_FILE and NEW_FILE must be set, e.g.:" >&2
    echo "  sbatch --export=ALL,OLD_FILE='/work/shared/oceanICU/tmp_chunk_bench/huss_OLD.nc',NEW_FILE='/work/shared/oceanICU/tmp_chunk_bench/huss_NEW.nc' bench_concurrent_reads.slurm" >&2
    exit 1
fi
VAR="${VAR:-huss}"

SCRIPT_DIR="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR not set -- sbatch must be invoked from inside the directory these scripts live in}"
OUT_DIR="$SCRIPT_DIR/disagg_chunk_bench_${SLURM_JOB_ID}"

source ~/.bashrc
conda activate pygetm

module purge
prgenv=igi
vprgenv=2024.1
module load ${prgenv}/${vprgenv}
module load ucx/1.16.0.mlx
module load hdf5-parallel/1.14.5-${prgenv}-${vprgenv}
module load netcdf-hdf5parallel/4.9.2-${prgenv}-${vprgenv}
module load intel_ipp_intel64/latest

cd "$SCRIPT_DIR" || exit 1

for N in 16 64 184; do
    for LABEL_FILE in "OLD:$OLD_FILE" "NEW:$NEW_FILE"; do
        LABEL="${LABEL_FILE%%:*}"
        FILE="${LABEL_FILE#*:}"
        echo "Running N=$N label=$LABEL file=$FILE"
        srun --ntasks="$N" --exclusive python3 bench_concurrent_reads_task.py \
            --file "$FILE" --var "$VAR" --label "$LABEL" \
            --out-dir "$OUT_DIR/N${N}"
    done
done

echo
echo "=== Results ==="
python3 bench_collect_results.py "$OUT_DIR"
