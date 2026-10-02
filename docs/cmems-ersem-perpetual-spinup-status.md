# Perpetual ERSEM spin-up — status (2026-10-02)

## Built and verified

- `hydrography.py`'s `set_hydrography_ic` owns the one-time FABM IC:
  restart-based seed when `fabm.ERSEM.restart_file` is set, climatology
  fallback otherwise -- gated so it never re-applies on continuation
  chunks.
- Conservative vertical remap (`_seed_fabm_state_from_restart`) handles
  the round-to-round grid mismatch (physics resets to climatology each
  round, so layer thickness won't match the prior round's save-time
  layout) -- see `docs/conservative-vertical-remap.md`.
- `NSe/config/nse_cmems.yaml` has `restart_file: null` explicit and
  visible in the generated config, editable between rounds without
  regenerating.
- Scripts generated at
  `/data/kb/OceanICU/oceanicu_3d/experiments/NSe/CMEMS/spinup/` (orca),
  synced to bb-server1, committed and pushed.
- A real run completed on bb-server1 (`nse_3d.nc`, `nse_2d.nc`,
  `surf_bott_daily.nc`, 20 log files) -- but that was the 3-month
  2024-01-01 to 2024-04-01 config, used to validate the machinery, not
  an actual spin-up round.
- Registered in the DB as `NSe/CMEMS/spinup`: annual chunking,
  multiplier 1, dates now 2010-01-01 to 2011-01-01.

## Open / not done yet

- No `restart_ersem.nc` exists for this experiment -- round 1 hasn't
  run with `--save-restart`.
- FABM tracers currently get no explicit IC in round 1 (no grid-shaped
  CMEMS bio product exists) -- falls back to `fabm_ersem.yaml`'s own
  `initial_value`. Worth a decision if that's acceptable.
- Automation decided against -- manual round-to-round toggling, same
  as Ricardo's existing process (per user, 2026-10-01: only done a
  handful of times, not worth automating).

## Steps to take

1. Confirm the queued registry commands (`add` annual/1,
   `set-start-date`/`set-stop-date`) have landed after the last
   `UPDATE`.
2. **Round 1**: run 2010-01-01 to 2011-01-01 with `--save-restart
   <path>/restart_ersem.nc`, `restart_file` still null.
3. Sanity-check the output restart (pelagic/benthic ERSEM state present
   and finite).
4. **Round 2+**: edit `generated_nse_cmems_config.yaml` directly -- set
   `fabm.ERSEM.restart_file` to round 1's output, rerun same
   dates/`--save-restart` target.
5. Repeat until benthic state stops drifting year-over-year (PML needed
   six 5-year rounds for AMM7, see `docs/pml-amm7-ersem-comparison.md`;
   ours is 1-year rounds, likely more iterations needed).
6. Decide a convergence check -- e.g. year-over-year benthic integrals,
   similar to what `running/analyze_logs.py` already does.
