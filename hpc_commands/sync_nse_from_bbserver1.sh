#!/bin/bash
# Mirror /data/OceanICU/oceanicu_3d/data/NSe/ (bb-server1) -> /work/shared/oceanICU/NSe/ (this HPC).
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
rsync -avh --progress --stats "$@" "$SRC" "$DEST"
