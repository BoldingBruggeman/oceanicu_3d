#!/bin/bash
# regen_and_deploy.sh -- regenerate the Hugo site from validation results and
# deploy it to gh-pages. Run on the relay host (bb-server1); from anywhere
# else it re-runs itself there over ssh.
#
#   ./regen_and_deploy.sh                      # dry run: both inner scripts' usage, nothing runs
#   ./regen_and_deploy.sh --apply              # regenerate all enabled areas, then deploy
#   ./regen_and_deploy.sh --apply --area NSe   # regenerate one area, then deploy
#
# All arguments go to regenerate_hugo.py; deploy_ghpages.py gets only
# --apply (it deploys the whole site). A failed regeneration exits non-zero
# before deploying.

set -euo pipefail

apply=false
for arg in "$@"; do
    if [ "$arg" = "--apply" ]; then
        apply=true
    fi
done

# This script's directory is the oceanicu_3d checkout (kb and the shared
# oceanicu account clone it at different paths).
HUGO_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Use an already-active ocean-stack env if there is one (any conda layout),
if command -v python3 >/dev/null 2>&1 && python3 -c "import ocean_data" 2>/dev/null; then
    : # already active and correct -- nothing to do
# else the shared /opt/conda-envs one (the oceanicu account has no
# miniconda3), else ~/miniconda3.
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

# Not on regen_hosts.yaml's relay_host: re-run the whole wrapper there, so
# the lock below is taken where hugo_out lives. --no-relay forces a local run.
relay_rc=0
python3 - "$HUGO_REPO" "$@" <<'EOF' || relay_rc=$?
import socket, subprocess, sys, yaml
from pathlib import Path
from ocean_data import host_config as oc
repo, argv = sys.argv[1], sys.argv[2:]
relay_host = yaml.safe_load(Path(repo, 'regen_hosts.yaml').read_text())['relay_host']
if socket.gethostname() == relay_host or '--no-relay' in argv:
    sys.exit(10)
try:
    relayed = oc.relay_if_needed(
        oc.load_host_config('oceanicu'), argv,
        repo_key='OceanICU/oceanicu_3d', entry_point='./regen_and_deploy.sh',
    )
except subprocess.CalledProcessError as e:
    sys.exit(e.returncode or 1)
if not relayed:
    sys.exit(f"ERROR: expected to relay to {relay_host} but relay_if_needed() declined -- check ~/.config/oceanicu/hosts.yaml")
EOF
case $relay_rc in
    0)  exit 0 ;;
    10) ;;
    *)  exit "$relay_rc" ;;
esac

# ocean-post's checkout differs per host: repos.ocean-post from
# ~/.config/oceanicu/hosts.yaml (this host, else relay_host's entry).
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

# One run at a time: content/ and the gh-pages push are shared state, and
# HPC may trigger two runs close together. Bail out rather than wait, so a
# second run never deploys the first one's half-written content. The lock
# lives in hugo_out (shared by both accounts, group bb), not the per-account
# checkout.
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
# fd 200 keeps the lock until this script exits.

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
