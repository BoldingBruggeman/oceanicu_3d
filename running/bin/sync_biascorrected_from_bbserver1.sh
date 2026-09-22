#!/bin/bash
# Mirror one model/scenario's full set of external forcing from
# bb-server1 into this HPC's /work/shared/oceanICU/: DISAGGREGATED meteo
# files, the scenario's rivers files (no disagg/non-disagg split for
# those), AND the boundaries tree for one SETUP (data/<SETUP>/ -> <SETUP>/,
# all models/scenarios at once for that setup).
#
# Boundaries are setup-specific (NSe/AMM7/ENA4/ENA8/NS each have their own
# boundary_points/domain -- meteo/rivers are NOT, same CMIP6 model/scenario
# files apply regardless of which regional setup reads them), so this
# needs its own --setup flag rather than being folded silently into
# MODEL/SCENARIO the way meteo/rivers are. Resolves to a sibling
# sync_<setup, lowercased>_from_bbserver1.sh in this same directory --
# only sync_nse_from_bbserver1.sh actually exists today; add the
# equivalent for another setup there if/when one is needed, this script
# will pick it up automatically by name.
#
# Always pulls historical's meteo too, alongside the requested scenario
# -- any real run needs both spliced together (see scripts/meteo.py's
# own historical/scenario splicing), so there is no case where you'd want
# the scenario without it.
#
# Joined into one script 2026-09-22 (per user request) -- previously two
# separate scripts (this one for meteo/rivers, sync_nse_from_bbserver1.sh
# for boundaries) that had to be run separately before every real
# production run; easy to forget one. This script still calls the
# per-setup one internally (not duplicated) so its own real mirror
# logic/excludes stay in one place.
#
# Run THIS SCRIPT ON THE HPC (scylla) -- bb-server1 has no outbound route
# to the HPC, but the HPC can reach out to bb-server1, so this must be a
# pull, not a push.
#
# Usage:
#   ./sync_biascorrected_from_bbserver1.sh <MODEL> <SCENARIO> [--setup NAME] [rsync flags...]
#   ./sync_biascorrected_from_bbserver1.sh GFDL-ESM4 ssp126             # --setup defaults to NSe
#   ./sync_biascorrected_from_bbserver1.sh GFDL-ESM4 ssp126 --setup AMM7
#   ./sync_biascorrected_from_bbserver1.sh GFDL-ESM4 ssp126 -n          # dry run
#   ./sync_biascorrected_from_bbserver1.sh GFDL-ESM4 ssp126 --no-boundaries
#       # meteo/rivers only, skip the (whole-tree, model/scenario-independent)
#       # boundaries mirror -- e.g. when you know it's already current
set -euo pipefail

MODEL="${1:-}"
SCENARIO="${2:-}"
if [ -z "$MODEL" ] || [ -z "$SCENARIO" ]; then
    echo "Usage: $0 <MODEL> <SCENARIO> [--setup NAME] [rsync flags...]" >&2
    echo "  e.g.: $0 GFDL-ESM4 ssp126" >&2
    exit 1
fi
shift 2 || true

SYNC_BOUNDARIES=1
SETUP="NSe"
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --no-boundaries) SYNC_BOUNDARIES=0; shift ;;
        --setup) SETUP="$2"; shift 2 ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
set -- "${ARGS[@]}"

SRC_ROOT="bb-server1:/data/BiasCorrected/CMIP6"
DEST_ROOT="/work/shared/oceanICU/BiasCorrected/CMIP6"

sync_meteo_disagg() {
    local experiment="$1"
    shift
    local dest="$DEST_ROOT/$MODEL/$experiment/meteo/"
    mkdir -p "$dest"
    echo "--- $MODEL/$experiment/meteo (disagg only) ---"
    rsync -avh --progress --stats \
        --include='*_disagg_*.nc' --exclude='*' \
        "$@" \
        "$SRC_ROOT/$MODEL/$experiment/meteo/" "$dest"
}

sync_rivers() {
    local experiment="$1"
    shift
    local dest="$DEST_ROOT/$MODEL/$experiment/rivers/"
    # Not every scenario has a rivers/ folder (e.g. MPI-ESM1-2-HR's
    # ssp245/ssp585) -- skip quietly rather than aborting the whole sync.
    if ! ssh bb-server1 "[ -d /data/BiasCorrected/CMIP6/$MODEL/$experiment/rivers ]"; then
        echo "--- $MODEL/$experiment/rivers: not present on source, skipping ---"
        return 0
    fi
    mkdir -p "$dest"
    echo "--- $MODEL/$experiment/rivers (full, no disagg split for these) ---"
    # Exclude backup/temp files -- real ones exist today under
    # BiasCorrected/CMIP6/*/rivers/ from 2026-09-21/22 debugging
    # (.bak_pre_*, .bak_no_qmean_*, .tmp_*), and a plain sync with no
    # exclude would otherwise ship them to the HPC too.
    rsync -avh --progress --stats \
        --exclude='*.bak_*' --exclude='*.tmp_*' \
        "$@" "$SRC_ROOT/$MODEL/$experiment/rivers/" "$dest"
}

sync_meteo_disagg historical "$@"
sync_meteo_disagg "$SCENARIO" "$@"
sync_rivers "$SCENARIO" "$@"

if [ "$SYNC_BOUNDARIES" = "1" ]; then
    setup_lc=$(echo "$SETUP" | tr '[:upper:]' '[:lower:]')
    boundary_script="$(dirname "${BASH_SOURCE[0]}")/sync_${setup_lc}_from_bbserver1.sh"
    if [ ! -x "$boundary_script" ]; then
        echo "ERROR: no boundary sync script for setup '$SETUP' (expected $boundary_script)." >&2
        echo "       Boundaries are setup-specific -- pass --setup to match the real setup," >&2
        echo "       or add that script (copy sync_nse_from_bbserver1.sh and repoint SRC/DEST)." >&2
        exit 1
    fi
    echo "--- boundaries (setup=$SETUP, data/$SETUP/ -> $SETUP/, all models/scenarios) ---"
    "$boundary_script" "$@"
else
    echo "--- boundaries: skipped (--no-boundaries) ---"
fi
