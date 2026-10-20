# CLAUDE_ATTRIBUTION.md

Commits below predate this repo's convention of a `Co-Authored-By`/
`Claude-Session` trailer on Claude-assisted commits. Recorded here
instead of rewriting already-pushed history (claude branch, pulled onto
bb-server1 and orca).

All from the `regen_and_deploy.sh --apply` deploy-fix session, 2026-10-13:

- `5606c62` regenerate_hugo.py: resolve conda env python3 from hosts.yaml, not ~/miniconda3
- `e3055f8` regen_and_deploy.sh: resolve its own repo dir, don't require ~/miniconda3
- `05a71a6` regen_and_deploy.sh: DEPLOY_REPO points at the shared /opt/ocean-src copy
- `60dc200` regenerate_hugo.py: shebang points directly at the shared conda env
- `bb2ce65` Revert: regenerate_hugo.py shebang back to #!/usr/bin/env python3
- `c0bb4b7` regen_and_deploy.sh: require --apply itself, forward it through
- `755503a` regen_and_deploy.sh: dry-run both inner commands when --apply is omitted

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJvCih7wVg55GLwRBhtWFv
