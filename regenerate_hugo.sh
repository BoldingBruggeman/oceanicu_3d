#!/usr/bin/env bash
# Regenerate Hugo content from validation results and area metadata.
#
# Usage:
#   ./regenerate_hugo.sh              # all areas
#   ./regenerate_hugo.sh --area NS    # single area
#   ./regenerate_hugo.sh --serve      # regenerate then start dev server

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Overridable per-machine: orca's dev checkout and the relay/bb-server1
# production location have different real paths (bb-server1 has no "kb"
# in the path) and different conda envs with cli.reporting installed
# (pygetm on orca, ocean-stack on bb-server1) -- defaults below preserve
# orca's existing behaviour unchanged.
ANALYSES_DIR="${REGEN_HUGO_ANALYSES_DIR:-/data/kb/OceanICU/oceanicu_3d/analyses}"
HUGO_OUT="${REGEN_HUGO_OUT:-/data/kb/OceanICU/oceanicu_3d}"
HUGO_DIR="${SCRIPT_DIR}/hugo"
CONDA_ENV="${REGEN_HUGO_CONDA_ENV:-pygetm}"
# --db: the merged registry (runs/chunks + areas/experiments/analyses,
# consolidated so there's one DB, not two -- see run_registry.sqlite on
# the relay). Unset by default (orca falls back to cli.reporting's own
# <analyses-dir>/simulation_list.db discovery, unchanged); bb-server1's
# invocation sets REGEN_HUGO_DB explicitly.
DB_ARGS=()
if [ -n "${REGEN_HUGO_DB:-}" ]; then
    DB_ARGS=(--db "${REGEN_HUGO_DB}")
fi

conda run -n "${CONDA_ENV}" python3 -m cli.reporting \
    --analyses-dir "${ANALYSES_DIR}" \
    --recursive \
    --hugo "${HUGO_OUT}" \
    "${DB_ARGS[@]}" \
    "$@"

echo ""
echo "Content written to ${HUGO_OUT}/content/"
echo "To serve locally:  cd ${HUGO_DIR} && hugo server"
echo "To build static:   cd ${HUGO_DIR} && hugo"
