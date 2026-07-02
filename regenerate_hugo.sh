#!/usr/bin/env bash
# Regenerate Hugo content from validation results and area metadata.
#
# Usage:
#   ./regenerate_hugo.sh              # all areas
#   ./regenerate_hugo.sh --area NS    # single area
#   ./regenerate_hugo.sh --serve      # regenerate then start dev server

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ANALYSES_DIR="/data/kb/OceanICU/oceanicu_3d/analyses"
HUGO_OUT="/data/kb/OceanICU/oceanicu_3d"
HUGO_DIR="${SCRIPT_DIR}/hugo"

conda run -n pygetm python3 -m cli.reporting \
    --analyses-dir "${ANALYSES_DIR}" \
    --recursive \
    --hugo "${HUGO_OUT}" \
    "$@"

echo ""
echo "Content written to ${HUGO_OUT}/content/"
echo "To serve locally:  cd ${HUGO_DIR} && hugo server"
echo "To build static:   cd ${HUGO_DIR} && hugo"
