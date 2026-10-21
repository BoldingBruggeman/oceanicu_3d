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

# First choice: the caller already has a working ocean-stack env active
# (any conda layout at all -- module-based HPC conda, ~/.conda/envs,
# anaconda3, miniforge3, ...). Caught live: scylla's own env lives at
# ~/.conda/envs/ocean-stack, neither of the two hardcoded guesses below --
# a third hardcoded path would just be the same brittleness again for the
# next machine. Checking "is python3 already right" instead of "where
# might it be" handles every layout, including ones we've never seen.
if command -v python3 >/dev/null 2>&1 && python3 -c "import ocean_data" 2>/dev/null; then
    : # already active and correct -- nothing to do
# Prefer the shared conda env location over ~/miniconda3 -- not every
# account has its own miniconda3 install at all (the shared "oceanicu"
# account doesn't; confirmed live: `source ~/miniconda3/...` failed
# outright with "No such file or directory"). A plain PATH prepend works
# for any account with read+execute on it, same mechanism
# host_config.relay_if_needed()'s own python_bin already uses.
elif [ -x /opt/conda-envs/ocean-stack/bin/python3 ]; then
    export PATH="/opt/conda-envs/ocean-stack/bin:$PATH"
elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
    conda activate ocean-stack
else
    echo "ERROR: no ocean-stack python3 found on PATH, and neither" >&2
    echo "/opt/conda-envs/ocean-stack nor ~/miniconda3 exists here --" >&2
    echo "activate your own ocean-stack env before running this script." >&2
    exit 1
fi

# Where ocean-post lives is NOT the same on every host -- the shared
# /opt/ocean-src/ocean-post checkout only exists on bb-server1/oceanicu-
# relay; orca still has its own ~/source/repos/ocean-post. Hardcoding
# /opt/ocean-src/ocean-post here broke for real on orca ("cd: No such
# file or directory"). Resolve it the same way resolve_hugo_dir()/
# resolve_env_python() already do in the Python scripts: read this
# host's own repos.ocean-post from ~/.config/oceanicu/hosts.yaml,
# falling back to relay_host's entry only if THIS host has no entry at
# all (not merely a missing key) -- same semantics, same reason (see
# regenerate_hugo.py's resolve_env_python() docstring). Must run after
# the PATH/conda setup above so python3 here is the right interpreter
# with ocean_data importable.
DEPLOY_REPO="$(python3 -c "
import socket
from pathlib import Path
from ocean_data import host_config as oc
cfg = oc.load_host_config('oceanicu')
hostname = socket.gethostname()
entry = cfg.get('hosts', {}).get(hostname)
if entry is None:
    entry = cfg.get('hosts', {}).get(cfg.get('relay_host'))
repo = (entry or {}).get('repos', {}).get('ocean-post')
if repo:
    print(Path(repo).expanduser())
")"
if [ -z "$DEPLOY_REPO" ]; then
    echo "ERROR: no repos.ocean-post entry for host $(hostname) (or relay_host) in ~/.config/oceanicu/hosts.yaml" >&2
    exit 1
fi

# Refuse to run a second time concurrently -- this wrapper is the real
# production trigger point (no bb-server1 cron by design; HPC fires
# this directly when a new analysis is ready), and two experiments
# finishing close together can trigger two runs around the same time.
# Both the Hugo content tree (regenerate_hugo.py writes there) and the
# gh-pages push (deploy_ghpages.py) are shared mutable state -- two
# interleaved runs could race writing content/ or pushing the same
# branch. Bail out immediately rather than queueing/waiting -- a
# blocked second run would otherwise build+push using whatever content
# the FIRST run happened to leave on disk at that exact moment (its own
# regenerate_hugo.py step may not be done yet), silently deploying a
# half-regenerated site instead of either run's real intended content.
#
# Lock file lives in the Hugo OUTPUT dir (hugo_out), not $HUGO_REPO --
# $HUGO_REPO is a per-account checkout on bb-server1 (kb and the shared
# oceanicu account each clone this repo separately), so a lock there
# would never actually serialize the two accounts against each other.
# hugo_out, by contrast, is the one resource regen_hosts.yaml already
# has both accounts sharing for real (/data/OceanICU/oceanicu_3d, group
# `bb`, confirmed write-accessible by both, 2026-10-21) -- resolved the
# same way DEPLOY_REPO is above, from this host's own regen_hosts.yaml.
HUGO_OUT="$(python3 -c "
import socket, yaml
from pathlib import Path
cfg = yaml.safe_load(Path('$HUGO_REPO/regen_hosts.yaml').read_text())
hostname = socket.gethostname()
entry = cfg.get('hosts', {}).get(hostname)
if entry is None:
    entry = cfg.get('hosts', {}).get(cfg.get('relay_host'))
out = (entry or {}).get('hugo_out')
if out:
    print(Path(out).expanduser())
")"
if [ -z "$HUGO_OUT" ]; then
    echo "ERROR: no hosts.$(hostname).hugo_out entry (or relay_host's) in $HUGO_REPO/regen_hosts.yaml" >&2
    exit 1
fi

LOCKFILE="$HUGO_OUT/.regen_deploy.lock"
[ -f "$LOCKFILE" ] || { mkdir -p "$HUGO_OUT" && touch "$LOCKFILE"; } 2>/dev/null || true
exec 200<"$LOCKFILE"
if ! flock -n 200; then
    echo "ERROR: another regen_and_deploy.sh run is already in progress on $(hostname) ($LOCKFILE is locked) -- not starting a second one. Re-run this again in a few minutes, once it has finished." >&2
    exit 1
fi
# fd 200 stays open (and the lock held) for the rest of this script's
# life, released automatically on exit -- no explicit unlock needed.

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
