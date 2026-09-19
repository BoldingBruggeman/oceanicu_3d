#!/bin/bash
# Mirror one model/scenario's DISAGGREGATED meteo files (plus the
# scenario's rivers files, which have no disagg/non-disagg split) from
# bb-server1's /data/BiasCorrected/CMIP6/ into this HPC's
# /work/shared/oceanICU/BiasCorrected/CMIP6/. Always pulls historical's
# meteo too, alongside the requested scenario -- any real run needs both
# spliced together (see scripts/meteo.py's own historical/scenario
# splicing), so there is no case where you'd want the scenario without it.
#
# Run THIS SCRIPT ON THE HPC (scylla) -- bb-server1 has no outbound route
# to the HPC, but the HPC can reach out to bb-server1, so this must be a
# pull, not a push.
#
# Usage:
#   ./sync_biascorrected_from_bbserver1.sh <MODEL> <SCENARIO> [rsync flags...]
#   ./sync_biascorrected_from_bbserver1.sh GFDL-ESM4 ssp126
#   ./sync_biascorrected_from_bbserver1.sh GFDL-ESM4 ssp126 -n   # dry run
set -euo pipefail

MODEL="${1:-}"
SCENARIO="${2:-}"
if [ -z "$MODEL" ] || [ -z "$SCENARIO" ]; then
    echo "Usage: $0 <MODEL> <SCENARIO> [rsync flags...]" >&2
    echo "  e.g.: $0 GFDL-ESM4 ssp126" >&2
    exit 1
fi
shift 2 || true

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
    rsync -avh --progress --stats "$@" "$SRC_ROOT/$MODEL/$experiment/rivers/" "$dest"
}

sync_meteo_disagg historical "$@"
sync_meteo_disagg "$SCENARIO" "$@"
sync_rivers "$SCENARIO" "$@"
