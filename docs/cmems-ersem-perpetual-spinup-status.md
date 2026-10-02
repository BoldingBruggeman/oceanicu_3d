# Perpetual ERSEM spin-up — status (2026-10-02, updated)

## Built and verified

- `hydrography.py`'s `set_hydrography_ic` owns the one-time FABM IC:
  restart-based seed when `fabm.ERSEM.restart_file` is set, climatology
  fallback otherwise -- gated so it never re-applies on continuation
  chunks.
- Conservative vertical remap (`_seed_fabm_state_from_restart`) handles
  the round-to-round grid mismatch -- see
  `docs/conservative-vertical-remap.md`.
- `NSe/config/nse_cmems.yaml` has `restart_file` left unset (round-1
  default) -- generated config shows it as explicit `null`, editable
  between rounds without regenerating (but see "Known footgun" below).
- **O3_c (DIC) / O3_TA (alkalinity) boundary gap found and fixed**
  (2026-10-02, real run on HPC crashed/ran with no DIC/TA boundary at
  all): `nse_cmems.yaml` never had `O3_c`/`O3_TA` tracers -- added,
  pointing at the real `bio_carbon_new_2024-07-29_to_2026-08-17.nc`
  product (confirmed real coverage starts 2024-07-29, not 2024-02-28 as
  an earlier comment assumed).
- **Historical-bridge splice extended to cover CMEMS too** (previously
  CMIP6-only): `oceanicu_providers.py`'s `dic_ta_historical_method`
  field is now shared between CMIP6 and CMEMS; `_fabm_hist_files` is
  built per-source. New bridge file
  `bio_carbon_pml_trend_20100101_20240728.nc` generated (ocean-prep's
  `derive_historical_dic_ta.py`) to close CMEMS's own real ~10-year gap
  (2015-01-01 to 2024-07-28) with zero overlap against the real
  product. `nse_cmems.yaml` sets `dic_ta_historical_method: pml_trend`
  explicitly (the `cycled_climatology` method has no CMEMS-range
  equivalent file -- selecting it for CMEMS now raises a loud
  `ValueError` at generation time instead of crashing deep inside with
  `Path / None`).
- **PML's own nitrate-anomaly seasonality (Eq. 7-8) added** to both
  bridge files (CMIP6's 2010-2014 one, regenerated, and CMEMS's new
  2010-2024-07-28 one) -- real `no3` anomaly from its own monthly
  climatology, applied to both the Atlantic-trend branch and the
  Baltic salinity-regression branch. See
  `docs/pml-amm7-ersem-comparison.md` and
  `ocean-prep/cli/derive_historical_dic_ta.py`.
- Scripts regenerated at
  `/data/kb/OceanICU/oceanicu_3d/experiments/NSe/CMEMS/spinup/` (orca)
  for the real registered run period (2010-01-01 to 2011-01-01),
  committed and pushed.
- Registered in the DB as `NSe/CMEMS/spinup`: annual chunking,
  multiplier 1, dates 2010-01-01 to 2011-01-01.
- Round 1 ran on scylla with `restart_file: null` (correct, expected).

## Open / unresolved -- pick up here Monday

**Round 2's restart seed isn't being picked up, cause not yet found.**
Round 2 was run on scylla with `fabm.ERSEM.restart_file` set to a real
(non-null) path, copied from the bb-server1-side config -- but the run
still "starts a normal run initializing from the FABM yaml file", as if
no restart were configured at all. Confirmed facts so far:

- The code path has NO silent fallback and NO try/except anywhere
  between the `restart_file` check and `_seed_fabm_state_from_restart`
  actually running (`hydrography.py:519-521`) -- if `restart_file` is
  truthy, that function unconditionally runs and logs a real
  `"_seed_fabm_state_from_restart: seeded pelagic [...] from <path>"`
  line on success, or should raise a loud error (e.g. `FileNotFoundError`
  from `xr.open_dataset`) if the path is wrong -- NOT fail silently.
- **Not yet confirmed**: whether that log line (or any crash) actually
  appears in round 2's real log on scylla. This is the next concrete
  thing to check -- `grep` the log for `_seed_fabm_state_from_restart`
  or `seeded pelagic`.
- **Not yet confirmed**: the exact `restart_file` value used, and
  whether that exact file really exists at that resolved path on scylla
  (right size, right timestamp -- i.e. genuinely round 1's own output,
  not an empty/stale/wrong file).
- Earlier in this same session, regenerating `generated_nse_cmems_
  config.yaml` (for the O3_c/O3_TA fix) reset `restart_file` back to
  `null` at least once, since the SOURCE `nse_cmems.yaml` still has it
  unset -- confirmed this is a real, reproducible footgun, not
  necessarily THIS round's actual problem (round 2 was reportedly run
  with a real, non-null value already) but worth guarding against: any
  future regenerate+push+`UPDATE` cycle will silently wipe a
  hand-edited `restart_file` back to `null` unless the edit is also
  made to `nse_cmems.yaml` itself (which would make it the new default
  for every future regeneration, including round 1's -- a tradeoff to
  weigh deliberately, not yet decided).

### Known footgun: where a path resolves to at runtime

`chunk_runner.py` runs each chunk with CWD set to a **per-chunk**
subdirectory (`experiment_root/<chunk_index>_<start>_<stop>/`,
`chunk_runner.py:367`), not the experiment root. `resolve_data_path`
passes a bare relative filename through unchanged (no `$VAR`), so a
bare filename resolves against that per-chunk CWD, not the experiment
root where the generated scripts/config live. Round 1's own
`--save-restart` output also lands inside that SAME per-chunk
subdirectory (`chunk_runner.py:419`:
`chunk_dir / f"restart_{setup_name}_{stop:%Y%m%d}.nc"`), i.e. for this
experiment, round 1's real restart file should be at:
```
<experiment_root>/001_20100101_20110101/restart_generated_nse_cmems_config_20110101.nc
```
The robust fix is to always use an absolute or `${VAR}`-expanding path
(e.g. `${FABM_RESTART_FOLDER}/restart_ersem.nc`, matching Ricardo's own
existing perpetual-ERSEM-restart convention) -- confirmed
`scylla_data_roots.yaml` defines `FABM_RESTART_FOLDER: /work/shared/
oceanICU/NSe/FABM/init`. **Not yet confirmed**: whether round 1's real
output was actually copied to that fixed location before round 2 ran,
or whether round 2's `restart_file` pointed somewhere else (e.g.
directly at round 1's own chunk-local path, or at Ricardo's own
existing `restart_ersem.nc`, which is a DIFFERENT, unrelated perpetual
snapshot, not this experiment's own round-1 output).

## Steps to take, resuming Monday

1. On scylla (or via SSH from shark -> orca -> wherever scylla's logs
   are reachable from), grep round 2's real run log for
   `_seed_fabm_state_from_restart` / `seeded pelagic`. If present:
   something else is wrong (maybe partial tracer coverage, read the
   full message). If absent: the function never ran despite the
   edit -- check `sim.fabm` is truthy for this run and that the exact
   config file being read is the one that was actually edited.
2. Confirm round 1's real output file exists at
   `<experiment_root>/001_20100101_20110101/restart_generated_nse_
   cmems_config_20110101.nc` (real size, right timestamp).
3. Copy that file to `${FABM_RESTART_FOLDER}/restart_ersem.nc`
   (`/work/shared/oceanICU/NSe/FABM/init/restart_ersem.nc` on scylla) --
   NOT Ricardo's own existing file at that path, if one is already
   there (back it up first, or use the experiment-specific filename,
   to avoid clobbering his own perpetual-run state).
4. Edit the real `generated_nse_cmems_config.yaml`'s `fabm.restart_file`
   line directly to `${FABM_RESTART_FOLDER}/restart_ersem.nc`, and
   re-run round 2 WITHOUT any intervening regenerate/`UPDATE` cycle.
5. Once round 2 is confirmed to actually seed from the restart (real
   log line present), continue the round-by-round cycle: repeat until
   benthic state stops drifting year-over-year (PML needed six 5-year
   rounds for AMM7, see `docs/pml-amm7-ersem-comparison.md`; ours is
   1-year rounds, likely more iterations needed).
6. Decide a convergence check -- e.g. year-over-year benthic integrals,
   similar to what `running/analyze_logs.py` already does.
7. Separately, still open: the real data files this fix depends on
   (`bio_carbon_pml_trend_20100101_20141231.nc` regenerated,
   `bio_carbon_pml_trend_20100101_20240728.nc` new) live under bb-
   server1's `/data/OceanICU/oceanicu_3d/data/NSe/CMEMS/bdy/` -- NOT
   git-tracked, and scylla's own copy lives at a different physical
   path (`/work/shared/oceanICU/NSe/CMEMS/bdy`). Confirm these have
   actually been rsynced to scylla before assuming the O3_c/O3_TA fix
   is live there at all.
