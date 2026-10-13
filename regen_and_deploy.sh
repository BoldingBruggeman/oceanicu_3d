#!/bin/bash
# regen_and_deploy.sh -- regenerate the Hugo site from validation results and
# deploy it to gh-pages, in one call, without having to remember where
# regenerate_hugo.py / deploy_ghpages.py live or re-find their flags.
#
# Run this directly on the relay host (bb-server1), logged in as whichever
# account owns that account's own checkouts (kb or oceanicu) -- it does a
# local run itself, not a relay hop (regenerate_hugo.py/deploy_ghpages.py
# each auto-relay internally if NOT run on the relay, so running this
# wrapper from anywhere else would still work, just via two separate
# relay hops instead of one direct run).
#
# Usage (--apply is required to actually run anything; without it, both
# inner scripts are still invoked, but each WITHOUT --apply -- so each
# shows its own real --help/usage and does nothing, the same
# confirmation gate regenerate_hugo.py/deploy_ghpages.py each already
# require individually, which this wrapper must not silently bypass
# just because it used to hardcode the call):
#   ./regen_and_deploy.sh                      # dry-run: show both inner --help texts, do nothing
#   ./regen_and_deploy.sh --apply              # regenerate all enabled areas, then deploy
#   ./regen_and_deploy.sh --apply --area NSe   # regenerate one area only, then deploy
#
# All arguments given (including --apply itself, if present) are
# forwarded as-is to regenerate_hugo.py (e.g. --area NAME). deploy_ghpages.py
# always runs with just --apply (or nothing, in the dry-run case), since
# it builds+pushes the Hugo site as a whole -- there's no "deploy one
# area" equivalent.
#
# Exits non-zero (without deploying) if regeneration fails, so a broken
# regen never gets force-pushed to the live site.

set -euo pipefail

apply=false
for arg in "$@"; do
    if [ "$arg" = "--apply" ]; then
        apply=true
    fi
done

# This script's own directory IS the oceanicu_3d checkout -- not a fixed
# ~/source/repos/OceanICU/oceanicu_3d guess (that broke for real: the
# shared "oceanicu" account on bb-server1 clones this repo flat, to
# ~/source/repos/oceanicu_3d, with no "OceanICU/" parent -- see
# bb_server1_shared_account_setup.txt). Resolving via this script's own
# location works for either layout automatically.
HUGO_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# A single shared checkout (kb:bb, pulled only by kb -- see
# ~/.config/oceanicu/hosts.yaml's own note on this), not per-account --
# ocean-post is a pure library/script source this pipeline never pushes
# to, so there's no reason for oceanicu to have (and keep in sync) its
# own separate copy.
DEPLOY_REPO="/opt/ocean-src/ocean-post"

# Prefer the shared conda env location over ~/miniconda3 -- not every
# account has its own miniconda3 install at all (the shared "oceanicu"
# account doesn't; confirmed live: `source ~/miniconda3/...` failed
# outright with "No such file or directory"). A plain PATH prepend works
# for any account with read+execute on it, same mechanism
# host_config.relay_if_needed()'s own python_bin already uses.
if [ -x /opt/conda-envs/ocean-stack/bin/python3 ]; then
    export PATH="/opt/conda-envs/ocean-stack/bin:$PATH"
elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
    conda activate ocean-stack
else
    echo "ERROR: no ocean-stack python3 found (checked /opt/conda-envs/ocean-stack and ~/miniconda3)" >&2
    exit 1
fi

if ! $apply; then
    echo "==> --apply not given -- dry run: showing both inner scripts' own usage, nothing will run"
    echo
    echo "--- regenerate_hugo.py ($HUGO_REPO) ---"
    cd "$HUGO_REPO"
    python3 regenerate_hugo.py "$@"   # no --apply in "$@" here -- each just prints its own help and exits 0
    echo
    echo "--- deploy_ghpages.py ($DEPLOY_REPO) ---"
    cd "$DEPLOY_REPO"
    python3 deploy_ghpages.py
    exit 0
fi

echo "==> Regenerating Hugo content ($HUGO_REPO)"
cd "$HUGO_REPO"
python3 regenerate_hugo.py "$@"   # "$@" already contains --apply, checked above

echo
echo "==> Deploying to gh-pages ($DEPLOY_REPO)"
cd "$DEPLOY_REPO"
python3 deploy_ghpages.py --apply
