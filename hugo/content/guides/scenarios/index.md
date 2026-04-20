---
title: "Scenario Analysis"
weight: 80
---

# Scenario Analysis Guide

## Overview

The scenario workflow has three stages:

```
CMIP6 forcing  ──► bc correct ──► bias-corrected forcing
                                        │
                               ocean model (future run)
                                        │
                         ┌─────────────┴─────────────┐
                   single-scenario              multi-scenario
                  (trends, changes)       (SSP comparison)
```

1. **Bias-correct** CMIP6 atmospheric forcing against ERA5 (or equivalent)
   so its statistical properties match the observational reference over the
   calibration period.
2. **Run the ocean model** with the corrected forcing for each SSP scenario.
3. **Analyse** the ocean output — trend maps, time series, regional means —
   for one scenario at a time or across multiple scenarios simultaneously.

---

## Step 1 — Bias correction

### Commands

| Command | Role |
|---------|------|
| `bc correct` | Apply bias correction → write corrected NetCDF |
| `bc diagnostics` | Inspect correction → maps, seasonal cycles, trend plots |

Aliases: `bc-correct`, `bc-diagnostics`.

### Correction methods

| Method | Flag | Description |
|--------|------|-------------|
| Quantile delta mapping | `qdm` | Preserves relative future trends; corrects full distribution |
| Quantile mapping | `qm` | Corrects distribution but suppresses climate change signal |
| Delta (additive) | `delta` | Fast; corrects mean only |

`qdm` is the recommended default for most variables.

### Config structure

```yaml
# config/bc.yaml

metadata:
  author:     "K. Bolding"
  institute:  "BB"
  project:    "NorthSea-MFC"
  area:       NS
  experiment: ssp245
  scenario:   ssp245

domain:
  lon_min: -5.0;  lon_max: 10.0
  lat_min: 50.0;  lat_max: 62.0

calibration:
  start: "1990-01-01"
  end:   "2014-12-31"

future:
  start: "2015-01-01"
  end:   "2100-12-31"

correction:
  method:      qdm
  n_quantiles: 100

variables:
  - name: tas
    kind: additive          # additive or multiplicative
  - name: pr
    kind: multiplicative

reference:                  # ERA5 or equivalent observational reference
  tas:
    source: local
    path: /data/era5/tas_{year}.nc
    variable: t2m
  pr:
    source: local
    path: /data/era5/pr_{year}.nc
    variable: tp

historical:                 # CMIP6 historical member
  tas:
    uri: "cmip6://MPI-ESM1-2-HR/historical/tas/day/r1i1p1f1"
  pr:
    uri: "cmip6://MPI-ESM1-2-HR/historical/pr/day/r1i1p1f1"

future_model:               # CMIP6 future scenario member
  tas:
    uri: "cmip6://MPI-ESM1-2-HR/ssp245/tas/day/r1i1p1f1"
  pr:
    uri: "cmip6://MPI-ESM1-2-HR/ssp245/pr/day/r1i1p1f1"

variables:
  - name: tas
    kind: additive
    regridding:
      method: bilinear   # bilinear | conservative | nearest_s2d
  - name: pr
    kind: multiplicative
    regridding:
      method: bilinear

output:
  dir: ./bc_output/{scenario}
  filename_pattern: "{variable}_bc_{year}.nc"
  compress: true
  clim_period: 1         # 1 = one file per year; >1 = multi-year means

cache:
  enabled: true
  dir: ./regrid_weights/ # weight files reused across variables and scenarios
```

Config on GitHub: [`config/bias_correction_example.yaml`](https://github.com/bolding/stats/blob/main/config/bias_correction_example.yaml)

### Running the correction

```bash
# Full run
bc correct --config config/bc.yaml

# Single variable / override method / override scenario
bc correct --config config/bc.yaml --variable tas
bc correct --config config/bc.yaml --method qdm
bc correct --config config/bc.yaml --scenario ssp585

# Stages: correction only or disaggregation only
bc correct --config config/bc.yaml --steps correct
bc correct --config config/bc.yaml --steps disaggregate

# Dry run — show which files would be read/written, no data loaded
bc correct --config config/bc.yaml --dry-run
```

**Disaggregation** distributes daily corrected output to sub-daily resolution
using a diurnal ERA5 climatology.  Requires a `disaggregation:` block in the
config.

### Correction output

```
bc_output/ssp245/
    tas_bc_2015.nc … tas_bc_2100.nc
    pr_bc_2015.nc  … pr_bc_2100.nc
```

### Running diagnostics

```bash
bc diagnostics --config config/bc.yaml
bc diagnostics --config config/bc.yaml --variable tas
bc diagnostics --config config/bc.yaml --output-dir ./diag/ssp245/
bc diagnostics --config config/bc.yaml --dry-run
```

Results land under `analyses/areas/<area>/scenarios/<scenario>/`:

```
analyses/areas/NS/scenarios/ssp245/
└── tas/
    ├── plots/
    │   ├── calibration_maps.png    — reference | CMIP6 raw | bias
    │   ├── seasonal_cycle.png      — monthly climatology comparison
    │   ├── future_trend.png        — annual means + linear trend to 2100
    │   └── monthly_breakdown.png   — decadal heatmap
    └── tables/
        └── statistics.txt          — per-month RMSE / bias / correlation
```

### Multiple scenarios

```bash
for scenario in ssp245 ssp370 ssp585; do
    bc correct     --config config/bc.yaml --scenario $scenario
    bc diagnostics --config config/bc.yaml --scenario $scenario
done
```

---

## Step 2 — Single scenario analysis

`single-scenario` analyses ocean model output from one future projection:
trends, long-term changes, regional means, and extreme-value analysis.

### Config

```yaml
# config/single_scenario.yaml
area: NS
experiment: ssp245

model:
  base_path: /data/model/NS/{experiment}/
  filename_pattern: "getm_{year}*.nc"

variables: [temp, salt]

scenario:
  experiment: ssp245
  reference_period:
    start: "1985-01-01"
    end:   "2014-12-31"
  future_period:
    start: "2070-01-01"
    end:   "2099-12-31"

output:
  base_dir: ./analyses
```

Config on GitHub: [`config/single_scenario.yaml`](https://github.com/bolding/stats/blob/main/config/single_scenario.yaml)

### Usage

```bash
single-scenario --config config/single_scenario.yaml
single-scenario --config config/single_scenario.yaml --scenario ssp585
```

### Output

```
analyses/areas/NS/scenarios/ssp245/
    plots/
        temp_trend_map.png          — linear trend per grid point
        temp_timeseries.png         — area-mean annual time series
        temp_change_map.png         — future − reference period mean
    tables/
        trend_statistics.txt
```

---

## Step 3 — Multi-scenario comparison

`multi-scenario` loads output from multiple SSP runs and overlays them —
time series, spatial change maps, spread envelopes.

### Config

```yaml
# config/multi_scenario.yaml
area: NS

model:
  base_path: /data/model/NS/{experiment}/
  filename_pattern: "getm_{year}*.nc"

variables: [temp, salt]

scenarios:
  - name: SSP1-2.6
    experiment: ssp126
  - name: SSP2-4.5
    experiment: ssp245
  - name: SSP5-8.5
    experiment: ssp585

reference_period:
  start: "1985-01-01"
  end:   "2014-12-31"

# comparison_name sets the output subdirectory under areas/<area>/scenarios/.
# If omitted, it is auto-derived by joining the experiment names:
# e.g.  ssp126_ssp245_ssp585
comparison_name: ssp_all

output:
  base_dir: ./analyses
```

Config on GitHub: [`config/multi_scenario.yaml`](https://github.com/bolding/stats/blob/main/config/multi_scenario.yaml)

### Usage

```bash
multi-scenario --config config/multi_scenario.yaml
```

### Output

Output goes to `analyses/areas/<area>/scenarios/<comparison_name>/`,
where `comparison_name` is set in the config or auto-derived from the
experiment names (e.g. `ssp126_ssp245_ssp585`).

```
analyses/areas/NS/scenarios/ssp_all/
    plots/
        temp_timeseries_comparison.png   — all SSPs on one axis
        temp_change_map_ssp585.png       — spatial change per scenario
        temp_spread_envelope.png         — min/max across scenarios
    tables/
        multi_scenario_summary.txt
```

---

## Performance notes

- Bias correction uses Dask lazy loading; memory stays bounded because
  output is written year by year.
- Diagnostics use a time-only chunk path — typically ~2 s/year.
- Regridding weights are cached under `cache.dir` and reused across
  variables and scenarios with the same grids.
