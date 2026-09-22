#!/bin/bash
# Mirror /data/OceanICU/oceanicu_3d/data/NSe/ (bb-server1) -> /work/shared/oceanICU/NSe/ (this HPC).
#
# Called internally by sync_biascorrected_from_bbserver1.sh (which also
# does meteo/rivers, per model/scenario) -- that's the one real
# production runs should normally use for "get everything this run
# needs" in one command; run this one standalone only when you
# specifically want just the boundaries refreshed.
#
# Run THIS SCRIPT ON THE HPC (scylla) -- bb-server1 has no outbound route to
# the HPC, but the HPC can reach out to bb-server1, so this must be a pull,
# not a push.
#
# Usage:
#   ./sync_nse_from_bbserver1.sh          # real transfer
#   ./sync_nse_from_bbserver1.sh -n       # dry run -- shows what would change, transfers nothing
#   ./sync_nse_from_bbserver1.sh --delete # also remove dest files no longer present on the source
#                                          # (a real mirror, not just an add-only sync -- use once
#                                          # you trust the diff a plain -n run shows you)
set -euo pipefail

SRC="bb-server1:/data/OceanICU/oceanicu_3d/data/NSe/"
DEST="/work/shared/oceanICU/NSe/"

mkdir -p "$DEST"
# Exclude backup/temp files -- real ones exist today under data/NSe/ from
# 2026-09-21/22 debugging (.bak_pre_*, .wiped_*_mistake_*, .tmp_*), and a
# plain mirror with no exclude would otherwise ship them to the HPC too.
rsync -avh --progress --stats \
    --exclude='*.bak_*' --exclude='*.tmp_*' --exclude='*.wiped_*' \
    "$@" "$SRC" "$DEST"
