#!/bin/bash
# regen_and_deploy.sh -- regenerate the Hugo site from validation results and
# deploy it to gh-pages, in one call, without having to remember where
# regenerate_hugo.py / deploy_ghpages.py live or re-find their flags.
#
# Usage:
#   ./regen_and_deploy.sh              # regenerate all enabled areas, then deploy
#   ./regen_and_deploy.sh --area NSe   # regenerate one area only, then deploy
#
# Any arguments given are forwarded as-is to regenerate_hugo.py (e.g.
# --area NAME). deploy_ghpages.py always runs with just --apply, since it
# builds+pushes the Hugo site as a whole -- there's no "deploy one area"
# equivalent.
#
# Exits non-zero (without deploying) if regeneration fails, so a broken
# regen never gets force-pushed to the live site.

set -euo pipefail

HUGO_REPO="$HOME/source/repos/OceanICU/oceanicu_3d"
DEPLOY_REPO="$HOME/source/repos/ocean-post"
CONDA_ENV="ocean-stack"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

echo "==> Regenerating Hugo content ($HUGO_REPO)"
cd "$HUGO_REPO"
python3 regenerate_hugo.py --apply "$@"

echo
echo "==> Deploying to gh-pages ($DEPLOY_REPO)"
cd "$DEPLOY_REPO"
python3 deploy_ghpages.py --apply
