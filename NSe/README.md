# NSe

North Sea/eastern Atlantic domain setup. This file is the entry point for
"what do I run to (re)generate the inputs" — each topic below links to a
deeper doc where one already exists. Expected to grow as more of the setup
gets documented (river discharge, meteo, running the model itself, ...).

## Bathymetry

```bash
cd /home/kb/source/repos/OceanICU/oceanicu_3d/NSe
./generate_nse_bathymetry.py                # full run (fixes applied by default), writes Bathymetry/bathymetry_nse.nc
./generate_nse_bathymetry.py --dryrun        # print resolved config, write nothing
./generate_nse_bathymetry.py --no-fixes      # skip --fixes-file/--accept-fixes
./generate_nse_bathymetry.py --extract-only  # (re)run the ncks GEBCO extraction step only
```

Behaviour (coarse source, depth/mask variable names, `fixes:`,
`mask_regions:`, `thalweg:` waypoints, `smooth.local_filters`,
`smooth.local_rx0`) is controlled by `NSe/config/nse_bathymetry.yaml` — edit
that and re-run, no flags needed for config changes. **Overwrites
`bathymetry_nse.nc` in place, no automatic backup** — copy it first if you
want to diff against the previous version.

**After any regeneration**, re-verify `NSe/Bathymetry/nse_bdy_lonlat.txt`
against the new mask — a bathymetry fix can silently put boundary points on
land with no error anywhere else. See
[`BOUNDARY_GENERATION.md`](BOUNDARY_GENERATION.md)'s "§0 — read this first"
for the check to run and why it matters.

Runs fine on either **orca** or **bb-server1** — no external data
dependency beyond the local GEBCO source and `ocean-prep`'s bathymetry
tooling.

## Boundaries

**Run on bb-server1, not orca** (2026-08-24): the CMEMS/CMIP6/TPXO9
download caches these tools need (`/data/cache/cmems`, `/data/cache/cmip6`,
`/data/cache/tpxo9`, plus the `/data/CMIP6` and `/data/TPXO9` source
archives) live on bb-server1, not orca — orca's copies were migrated over
and removed. All `output.base_directory` paths below are bb-server1
absolute paths (`/data/OceanICU/oceanicu_3d/data/NSe/...`) accordingly.

Full runbook: [`BOUNDARY_GENERATION.md`](BOUNDARY_GENERATION.md) (point-file
provenance, the three-stage pipeline, post-generation sanity checks, known
copies of `nse_bdy_lonlat.txt`). Short version:

```bash
# Stage 1 — historical reference (CMEMS)
cd /home/kb/source/repos/OceanICU/oceanicu_3d/NSe
run-cmems-boundaries --config config/nse_bdy_create.yaml --dryrun
run-cmems-boundaries --config config/nse_bdy_create.yaml

# Stage 2a — future scenario, temperature/salinity (delta-change)
cd /home/kb/source/repos/OceanICU/oceanicu_3d/NSe
run-delta-boundaries --config config/nse_delta_bdy.yaml --dryrun
run-delta-boundaries --config config/nse_delta_bdy.yaml --scenario ssp126 ssp370 ssp585

# Stage 2b — future scenario, SSH/currents (tidal + CMIP6)
cd /home/kb/source/repos/OceanICU/oceanicu_3d/NSe
run-tidal-boundaries --config config/nse_tidal_bdy.yaml --dryrun
run-tidal-boundaries --config config/nse_tidal_bdy.yaml
```

Stage 1 must exist before stage 2a can run (2a inherits its boundary-point
layout from stage 1's own NC files). Stage 2b is independent of stage 1.

## Initial conditions

**Run on bb-server1, not orca** — same reason as boundaries above (CMEMS
download cache lives there now).

Two source options — pick with `--source`; `AMM7` is the default/primary
(1993–2026-04-30 coverage), `AMM15` covers 2024-03-01 onward:

```bash
cd /home/kb/source/repos/OceanICU/oceanicu_3d/NSe
download-init-conditions --config config/nse_init_create.yaml --source AMM7
download-init-conditions --config config/nse_init_create.yaml --source AMM15
download-init-conditions --config config/nse_init_create.yaml --source AMM7 --year 2015
download-init-conditions --config config/nse_init_create.yaml --source AMM7 --dryrun
download-init-conditions --config config/nse_init_create.yaml --source AMM7 --check-coverage
```

Downloads temperature/salinity on the source product's native grid and
writes 12 monthly snapshots (1st of each month) per variable to
`/data/OceanICU/oceanicu_3d/data/NSe/CMEMS/init/` (`output.base_directory`
in `config/nse_init_create.yaml`). The NWS product (AMM7/AMM15) is primary and
defines the output grid; the corresponding Baltic product fills the masked
southern Kattegat/Belt Sea halo. `flood_fill: true` in the config also
writes a `{variable}_ff` flood-filled variant of each field.

## Model config

See [`config/README.md`](config/README.md) for how `nse_from_oceanicu.yaml`
(the `pygetm-config`-schema domain config actually used to run the model)
relates to `nse_model_config.yaml`, and
[`driver/README.md`](../driver/README.md) for the driver itself
(`--print-config`, `--dump-python`, data-path portability, the TUI editor).

## More to follow

River discharge, meteo forcing, and running the model itself aren't
documented here yet.
