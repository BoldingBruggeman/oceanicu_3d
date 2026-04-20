---
title: "Validations"
date: 2026-04-17T00:00:00Z
---

Validation results for all model experiments, organised by analysis type.
Each type compares model output against a different class of observations and
produces plots and statistics tables stored under
`analyses/areas/<AREA>/validations/<EXPERIMENT>/`.

## Analysis Types

---

### Tidal Analysis

Harmonic analysis of sea surface elevation. Extracts tidal constituents (M2, S2,
N2, K1, O1, …) from model output and validates them against GESLA tide gauge
observations and/or satellite tidal atlases (TPXO9, FES2014).

**Outputs:** amplitude/phase maps, scatter plots per constituent, station
comparison maps (semi-diurnal / diurnal).

```bash
tidal-analysis --config config/tidal_analysis.yaml
```

> Full documentation: [Tidal Analysis guide](/guides/tidal-analysis/)

---

### Gridded 2D — Surface

Horizontal validation of surface fields (SST, SSS) against gridded satellite
products (CCI-SST, CCI-SSS, CMEMS). Produces annual-mean comparison maps and
spatial statistics for each variable.

**Outputs:** comparison maps, spatial statistics plots, Taylor diagrams.

```bash
gridded-2d-validation --config config/gridded_2d_validation_surface.yaml
```

> Full documentation: [Gridded 2D Validation guide](/guides/gridded-2d-validation/)

---

### Gridded 2D — Bottom

Horizontal validation of bottom-layer fields (bottom temperature, bottom
salinity) against gridded observation products (e.g. NWS climatology).

**Outputs:** comparison maps and spatial statistics for bottom variables.

```bash
gridded-2d-validation --config config/gridded_2d_validation_bottom.yaml
```

> Full documentation: [Gridded 2D Validation guide](/guides/gridded-2d-validation/)

---

### Gridded 2D — Depth Layers

Horizontal validation at fixed depth levels (e.g. 5 m, 50 m, 100 m, 200 m,
500 m). Useful for checking subsurface temperature and salinity structure against
Argo/WOA climatologies interpolated onto the model grid.

**Outputs:** per-depth comparison maps, spatial statistics, monthly statistics
and Taylor diagrams.

```bash
gridded-2d-validation --config config/gridded_2d_validation_depths.yaml
# Override layers at run time:
gridded-2d-validation --config config/gridded_2d_validation_depths.yaml --layers 50 100 200
```

> Full documentation: [Gridded 2D Validation guide](/guides/gridded-2d-validation/)

---

### Gridded 3D

Full water-column validation of temperature and salinity. Regrids gridded
observation products (WOA, Copernicus reanalysis, CMIP6) onto the model grid and
compares cell-by-cell at all depth levels.

**Outputs:** depth-layer comparison maps, statistics vs depth profiles,
Hovmöller diagrams.

```bash
gridded-3d-validation --config config/gridded_3d_validation.yaml
```

> Full documentation: [Gridded 3D Validation guide](/guides/gridded-3d-validation/)

---

### Cruise CTD Profiles

Validates scattered ship-based CTD profiles (ICES, EMODnet Chemistry) against
model output. Profiles are matched spatially and temporally to the nearest model
grid cell and time step.

**Outputs:** station map, Hovmöller diagrams, statistics vs depth.

```bash
cruise-ctd-profiles --config config/cruise_ctd_profiles.yaml
```

> Full documentation: [Profile Validation guide](/guides/profile-validation/)

---

### Argo Float Profiles

Validates freely drifting Argo float profiles against model output. Profiles are
matched to the model domain and the float dive locations in space and time.

**Outputs:** station map, Hovmöller diagrams, statistics vs depth.

```bash
argo-profiles --config config/argo_profiles.yaml
```

> Full documentation: [Profile Validation guide](/guides/profile-validation/)

---

### GLODAP Profiles

Validates quality-controlled bottle data from the GLODAP v2.2023 database
against model output. GLODAP covers 1957–2021 with global cruise coverage and
includes physical as well as biogeochemical variables (DIC, alkalinity, oxygen,
nutrients).

**Outputs:** station map, overview figure, Hovmöller diagrams per variable.

```bash
glodap-profiles --config config/glodap_profiles.yaml
```

> Full documentation: [Profile Validation guide](/guides/profile-validation/)

---

### ICES Point Observation Profiles

Validates model output against the ICES/ECOVAL point observation database — up
to 55 years of in-situ profiles covering 14 physics and biogeochemical variables
(temperature, salinity, oxygen, nitrate, phosphate, silicate, chlorophyll, pH,
pCO₂, alkalinity, ammonium, POC, DOC, PFT) for the North-West European Shelf.

Observation data are prepared by the
[OceanVal toolkit](https://oceanval.readthedocs.io/en/latest/recipes.html) and
stored as feather files under `<data_dir>/<variable>/*_<variable>_<year>.feather`.

**Outputs:** station map, Hovmöller diagrams per variable.

```bash
ices-profiles --config config/ices_profiles.yaml
ices-profiles --config config/ices_profiles.yaml --no-model
ices-profiles --config config/ices_profiles.yaml --variables temperature salinity
```

> Full documentation: [Profile Validation guide](/guides/profile-validation/)

---

### Fixed Platform Profiles

Discovers fixed platforms (moorings, bottom landers, coastal buoys) from the
EMODnet Chemistry ERDDAP service within a model domain. Iterative workflow:
first survey all platforms in the domain, then analyse one selected platform
in depth. Unlike Station Time Series, the platform list is queried live from
the remote service rather than local cached files.

**Outputs:** platform inventory table, station overview map, Hovmöller
diagrams, seasonal depth profiles, time series at fixed depth levels.

```bash
fixed-platform --config config/fixed_platform.yaml
fixed-platform --config config/fixed_platform.yaml --list-platforms   # survey
fixed-platform --config config/fixed_platform.yaml --platform 3       # by rank
```

> Full documentation: [Profile Validation guide](/guides/profile-validation/)

---

### Station Time Series

Validates model output against a single named long-term monitoring station
(e.g. Stonehaven, L4, Helgoland, BY15). Data is read from locally cached
files downloaded with `download-station`. Unlike Fixed Platform Profiles,
the station and its data format are registered in a catalogue and the
observation files live on disk.

**Catalogue stations** — 22 stations registered; download status:

![Station catalogue map — orange = automatic download, red = manual required](/station_map.png)

| Type | Stations | Download |
|------|----------|----------|
| SHARK (SMHI/HELCOM Baltic) | BY2, BY5, BY15, BY31, BY38, AnholtE, LasoTrindel | Automatic |
| PANGAEA | Helgoland, F3 | Automatic |
| Marine Scotland | Stonehaven | Automatic |
| EMODnet/ERDDAP (ICES CTD) | ArendalSt1, ArendalSt2 | Automatic |
| BODC / WCO | L4, E1 | Manual |
| HI (Norway) | Torungen, Utsira | Manual |
| DMI / ICES | GreatBelt | Manual |
| BSH MARNET | NorderneyBuoy | Manual |
| CEFAS SmartBuoy | WestGabbard, Warp | Manual |
| SYKE (Finland) | LL7, LL17 | Manual |

**Outputs:** station map, Hovmöller diagrams, statistics vs depth.

```bash
# First download the station data
download-station download --station Stonehaven --output-dir /data/stations
download-station report   --station Stonehaven --data-dir /data/stations/Stonehaven
download-station inventory --output-dir /data/stations

# Then validate against model
station-timeseries --config config/station_timeseries.yaml
station-timeseries --config config/station_timeseries.yaml --no-model
```

> Full documentation: [Profile Validation guide](/guides/profile-validation/)

---

### MLE Multi-Experiment Comparison

Ranks experiments by overall fit to observations using Maximum Likelihood
Estimation in SVD-reduced residual space. Combines residuals from multiple
observation types (Argo profiles, fixed platforms, cruise CTD, …) into a
single scalar skill score per experiment. Supports log/log10/sqrt transforms
for log-normal biogeochemical variables such as chlorophyll or phytoplankton.

Unlike the per-type validation pages, MLE comparison is a **cross-experiment**
tool — it reads the pre-computed residual Parquet files from all experiments
and produces a ranking table and skill-score plots.

**Outputs:** experiment ranking table, MLE skill-score bar chart, SVD
explained-variance plot.

```bash
mle-comparison --config config/mle_comparison.yaml
mle-comparison --analyses-dir ./analyses --area NS \
    --experiments Baseline Sensitivity1 --obs-types argo fixed_platform
mle-comparison --analyses-dir ./analyses --area NS \
    --experiments Baseline Sensitivity1 --obs-types argo \
    --transforms CPHL:log10 PHY:log10
```

> Full documentation: [MLE Comparison guide](/guides/mle-comparison/)

---

## Most Recent Updates
