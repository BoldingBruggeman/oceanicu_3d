# Updating scylla after the 2026-11-15 refactor

All ocean-* repos and oceanicu_3d are pushed (`claude` branch). bb-server1 is
updated (code, analyses layout v4, rebuilt `statistics.db`, regenerated site).
Commands to copy: `scylla_update_20261115.txt`.

`<stack>` below is the folder holding the ocean-* checkouts on scylla (with
`ocean-stack/` in it); `<o3d>` is the oceanicu_3d checkout.

## 1. Code (required)

| Step | Why |
|---|---|
| `conda activate ocean-stack`, then `bash <stack>/ocean-stack/update-and-test.sh` | Pulls ocean-stack and the five packages, reinstalls every package whose `pyproject.toml` changed (all five did), runs the tests |
| `pip install --no-deps -e <stack>/ocean-run` (only if ocean-run is installed) | The script does not cover ocean-run; its entry points changed too |
| `git -C <o3d> pull` (branch `claude`) | Callers of the renamed modules, NSe page plugin, `tools/`, `NSe/config/final_checks.yaml` |

Check: `python -c "import ocean_post.cli.reporting, ocean_prep.cli.bc"` works and
`python -c "import cli"` fails.

## 2. Things that may need edits on scylla

| What | Change |
|---|---|
| Own scripts, Slurm job files with `python -m cli.X` | `python -m ocean_<pkg>.cli.X` (e.g. `ocean_prep.cli.bathymetry_regrid`). Installed commands (`gridded-2d-validation`, ...) are unchanged |
| `final-boundary-check`, `meteo-final-check`, `river-final-check` | No built-in OceanICU paths any more: pass `--config` with a copy of `NSe/config/final_checks.yaml` using scylla's paths |
| ocean-prep download/regridding caches | Default `/data/cache/<source>`; set `CACHE_ROOT` in scylla's ocean-prep data roots if they live elsewhere |
| `bdy_diagnostics.py`, `derive_historical_dic_ta.py` | Now `python <o3d>/tools/<name>.py` (were in ocean-prep's `cli/`) |

## 3. Analyses tree on scylla (only if analyses are written there)

| Step | Effect |
|---|---|
| `manage-analyses migrate --analyses-dir <A>` (dry run), then add `--apply` | Layout v4: observation plots to `areas/<AREA>/observations/` |
| `manage-analyses import-stats --analyses-dir <A> --apply --rebuild` | Rebuilds `statistics.db` from the YAML statistics files (a copy is made first) |

## 4. Data to re-sync from bb-server1

| Data | Reason |
|---|---|
| `sync_biascorrected_from_bbserver1.sh GFDL-ESM4 ssp126` | Meteo, P-E and rivers regenerated with the regrid fix |
| `sync_biascorrected_from_bbserver1.sh MPI-ESM1-2-HR ssp126` | net_lw regenerated (was the mirrored product) |
| `sync_nse_from_bbserver1.sh --exclude='bdy_old_*' --exclude='regen_delta_*'` | MPI-ESM1-2-HR ssp126/ssp370 delta-change bio boundaries (no3 po4 si o2 dissic talk) regenerated 2026-11-15: the NaN at 42 boundary points (104-400 m) are filled, all other values unchanged. The excludes skip the old files kept on bb-server1 and the run logs; run with `-n` first to see the transfer |

Runs made with the old GFDL-ESM4 ssp126 forcing (p01E_avc, t01EM0xF,
nse-ssp126-ssp126) need a rerun after the re-sync.

## 5. Changed results when analyses are rerun

- Correlations are exact over the period (were means of 30-day correlations;
  e.g. 0.9645 -> 0.9931 for an SST case); means and stds are on paired points.
- Profile matching leaves observations more than 1.5 model output steps from
  any model time (or undated) without a model value.
- Map plots of 3-D pyGETM output default to the surface layer (index 0 was
  the bottom).
