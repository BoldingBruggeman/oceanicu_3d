---
title: "Data Preparation"
date: 2026-07-02
summary: "How bathymetry, boundary conditions, and initial conditions are generated for each modelling area."
weight: 20
---

The ocean model inputs — bathymetry, open boundary conditions, and initial
conditions — are produced by two specialised repositories:

| Repository | Role |
|---|---|
| `bathymetry` | Grid creation and GEBCO interpolation |
| `ocean-prep` | Boundary conditions and initial conditions |

Each area's configuration files are shown on the area page under
**How Input Files Were Generated**.

---

## Bathymetry

Bathymetry is generated with `bathymetry-regrid` (`ocean-prep/cli/bathymetry_regrid.py`),
which applies conservative area-averaged interpolation from GEBCO 2025 to the
model grid.  The workflow:

1. Define the model grid in a YAML config (lat/lon extents, resolution, rotation).
2. Conservatively interpolate GEBCO 2025 ice-surface elevation onto the model grid
   (each model cell gets the area-weighted average of source elevation).
3. Mask cells below `min_depth` (default 2 m) and cells whose ocean fraction is
   below `min_wet_fraction` (default 0.4).
4. Remove disconnected sub-basins, keeping only the largest connected ocean basin
   (or a specified subset).
5. Apply Beckmann–Haidvogel smoothing to the depth field so that the slope
   parameter *r*x0 ≤ 0.2 everywhere (suppresses pressure-gradient errors in
   sigma-coordinate models).
6. Apply manual `fixes` (point-level depth overrides) for narrow straits and
   channels that the automatic smoother cannot resolve at the model resolution.
7. Write `bathymetry_<area>.nc` with variables `lon`, `lat`, `mask`, `H`, plus
   a diagnostic report with cross-section and strand-analysis plots.

**Key parameter:** `smooth.rx0` (default 0.2).  Lower values mean more smoothing
at the cost of reduced depth accuracy.

---

## Open Boundary Conditions

Three distinct types of open boundary conditions are produced, each by a
separate `ocean-prep` processor:

### 1 · CMEMS historical boundaries

Covers the historical period (1993 – present) and near-future (ANFC
analysis/forecast).  Produced by `run_cmems_boundaries.py`.

| Product | Period | Resolution | Variables |
|---|---|---|---|
| NWS AMM7 MY reanalysis | 1993 – 2026-04-30 | 7 km, 33 lev | T, S, SSH (hourly), u, v |
| NWS AMM15 ANFC | 2026-05-01 – | 1.5 km, 33 lev | T, S, SSH (hourly), u, v |
| Baltic MY reanalysis | 1993 – 2022-12-31 | — | T, S, u, v (+ SSH as *sla*) |
| Baltic ANFC | 2023-01-01 – | — | T, S, u, v, SSH detided |

Segment-level overrides are used where the NWS product is masked (e.g.
eastern Kattegat for the NSe domain).  The processor flood-fills land cells
with the nearest valid value before interpolating to the boundary points.

**Output:** one NetCDF per dataset/period under `boundary_data/<area>/daily/`
and `boundary_data/<area>/hourly/`.

### 2 · Tidal boundaries

Barotropic tidal sea level and transports combined with a CMIP6 mean state.
Produced by `run_tidal_boundaries.py`.

- **Tidal constituents:** TPXO9-atlas (13 major constituents).
- **Mean state correction:** CMIP6 monthly climatology (zos + depth-integrated
  uo, vo) from a chosen model and scenario.  Added on top of the tidal
  prediction to provide realistic mean SSH and barotropic transport in future
  projections.
- **Output:** `bdy_2d_{start}_{end}.nc` under
  `CMIP6/<source_id>/<experiment_id>/` (dims: time × nbdyp).

### 3 · 3-D delta-change boundaries (future projections)

Future 3-D boundary conditions (temperature, salinity, and optionally
velocity) produced by `run_delta_boundaries.py`.

**Method:**

```
corrected(t) = AMM7/AMM15_ref(t_analog)
             + [CMIP6_future_clim(month) − CMIP6_hist_clim(month)]
```

where `t_analog` maps each future date to the corresponding day-of-year in
the historical reference period (cycling), and the CMIP6 change signal is
a 12-month climatology.  The method preserves fine-scale spatial structure
from the high-resolution CMEMS reanalysis while adding the large-scale
climate-change signal from CMIP6.

- **Historical reference:** AMM7/AMM15 CMEMS reanalysis (same as above).
- **CMIP6 change signal:** any CMIP6 model / scenario combination; the tool
  searches local CMIP6 tree (`local_root`) and ESGF cache before querying
  ESGF directly.
- **Output:** `bdy_3d_{variable}_{start}_{end}.nc` under
  `CMIP6/<source_id>/<experiment_id>/` (dims: time × nbdyp × depth).

---

## Initial Conditions

Produced by `download_init_conditions.py` (ocean-prep).  For each source
year, 12 monthly snapshots (1st of each month) are downloaded from CMEMS NWS
and saved as a single NetCDF per variable.

| Source | Period | Product |
|---|---|---|
| AMM7 | 1993 – 2026-04-30 | NWS MY reanalysis + Baltic MY |
| AMM15 | 2024-03-01 – | NWS ANFC + Baltic ANFC |

The primary (NWS) product defines the output grid.  The Baltic product fills
the masked halo in the southern Kattegat / Belt Sea where the NWS data are
invalid.  An additional flood-fill step (`_ff` variables) propagates valid
values into any remaining NaN cells on the model grid.

**Output:** `{source}_{variable}_{year}_monthly_ic.nc` under `init_data/<area>/`.
