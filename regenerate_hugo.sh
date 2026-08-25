#!/usr/bin/env bash
# Regenerate Hugo content from validation results and area metadata.
#
# Usage:
#   ./regenerate_hugo.sh              # all areas
#   ./regenerate_hugo.sh --area NS    # single area
#   ./regenerate_hugo.sh --serve      # regenerate then start dev server
#
# Must run on the relay (bb-server1) -- that's where the consolidated
# registry (run_registry.sqlite) and analyses/ output actually live, and
# generation is no longer synced/RPC'd anywhere else (see the DB
# consolidation commit). Running this from orca (or anywhere else)
# auto-detects that and re-execs itself over ssh on the relay instead,
# so you never have to remember to do that by hand. Set
# REGEN_HUGO_NO_RELAY=1 to disable this and force a genuinely local run
# (e.g. for testing non-DB-dependent page types on orca -- the status
# page/production filter won't reflect real data in that mode, since the
# registry only lives on the relay).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELAY_HOST="${REGEN_HUGO_RELAY_HOST:-bb-server1}"

if [ "$(hostname)" != "${RELAY_HOST}" ] && [ -z "${REGEN_HUGO_NO_RELAY:-}" ]; then
    echo "Not on ${RELAY_HOST} -- running there over ssh instead..." >&2
    remote_args=""
    for a in "$@"; do
        remote_args="${remote_args} $(printf '%q' "${a}")"
    done
    ssh "${RELAY_HOST}" "
        source ~/miniconda3/etc/profile.d/conda.sh &&
        cd ~/source/repos/OceanICU/oceanicu_3d &&
        REGEN_HUGO_ANALYSES_DIR=/data/OceanICU/oceanicu_3d/analyses \
        REGEN_HUGO_OUT=/data/OceanICU/oceanicu_3d \
        REGEN_HUGO_CONDA_ENV=ocean-stack \
        REGEN_HUGO_DB=/data/OceanICU/oceanicu_3d/experiments/run_registry.sqlite \
        REGEN_HUGO_NO_RELAY=1 \
        ./regenerate_hugo.sh${remote_args}
    "

    # The hugo binary (and hugo/config.yaml's contentDir/staticDir, which
    # point at this machine's own local paths) only exist here, not on the
    # relay -- sync the freshly-generated content/static back so a local
    # `hugo server`/`hugo` build actually has something current to read,
    # letting you preview before running deploy_ghpages.sh. Skippable with
    # REGEN_HUGO_NO_SYNC_BACK=1 (e.g. if you only wanted the DB/analyses
    # side-effects of generation and don't care about a local preview).
    if [ -z "${REGEN_HUGO_NO_SYNC_BACK:-}" ]; then
        LOCAL_CONTENT_DIR="${REGEN_HUGO_LOCAL_CONTENT_DIR:-/data/kb/OceanICU/oceanicu_3d}"
        echo "Syncing content/static back to ${LOCAL_CONTENT_DIR} for local preview..." >&2
        rsync -a --delete "${RELAY_HOST}:/data/OceanICU/oceanicu_3d/content/" "${LOCAL_CONTENT_DIR}/content/"
        rsync -a --delete "${RELAY_HOST}:/data/OceanICU/oceanicu_3d/static/"  "${LOCAL_CONTENT_DIR}/static/"
        echo ""
        echo "Synced. To preview locally before deploying:"
        echo "  cd ${SCRIPT_DIR}/hugo && hugo server"
        echo "Then open http://localhost:1313/oceanicu_3d/ in a browser."
    fi
    exit 0
fi

# Overridable per-machine: orca's dev checkout and the relay/bb-server1
# production location have different real paths (bb-server1 has no "kb"
# in the path) and different conda envs with cli.reporting installed
# (pygetm on orca, ocean-stack on bb-server1) -- defaults below preserve
# orca's existing behaviour unchanged when REGEN_HUGO_NO_RELAY forces a
# local run there; the ssh re-exec above already sets the right ones when
# actually running on the relay.
ANALYSES_DIR="${REGEN_HUGO_ANALYSES_DIR:-/data/kb/OceanICU/oceanicu_3d/analyses}"
HUGO_OUT="${REGEN_HUGO_OUT:-/data/kb/OceanICU/oceanicu_3d}"
HUGO_DIR="${SCRIPT_DIR}/hugo"
CONDA_ENV="${REGEN_HUGO_CONDA_ENV:-pygetm}"
# --db: the merged registry (runs/chunks + areas/experiments/analyses,
# consolidated so there's one DB, not two -- see run_registry.sqlite on
# the relay). Unset by default (orca falls back to cli.reporting's own
# <analyses-dir>/simulation_list.db discovery, unchanged); the relay
# invocation above sets REGEN_HUGO_DB explicitly.
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
