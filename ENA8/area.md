---
name: "ENA8"
full_name: "Extended North Atlantic 1/8°"
description: "Northeast Atlantic regional configuration at ~1/8° resolution covering the European Atlantic margin"
coordinates:
  lat_min: 40.0
  lat_max: 70.0
  lon_min: -20.0
  lon_max: 13.5
resolution: "~1/8° (~12 km)"
depth_range: "0–4000 m"
---

# Extended North Atlantic 1/8° (ENA8)

## Domain Description

ENA8 is a medium-resolution configuration of the northeast Atlantic, covering
the full European continental margin from the Iberian Peninsula to the
Norwegian Sea.  It resolves the main shelf-break circulation features and
provides a nesting intermediate between coarse global models and the
high-resolution shelf domains (NS, NSe).

### Geographic Coverage

- **Latitude**: 40.0°N to 70.0°N
- **Longitude**: 20.0°W to 13.5°E
- **Resolution**: ~1/8° spherical grid (~12 km)
- **Vertical**: 40 generalised vertical coordinate (GVC) levels

### Key Features

- Resolves mesoscale eddies in the open ocean and continental slope
- Used as downscaling intermediate and boundary provider for NS / NSe
- Bathymetry from GEBCO with Beckmann–Haidvogel smoothing
- Open boundaries driven by AMM7 CMEMS reanalysis (historical) or
  CMIP6 delta-change (future projections)

### Observation Datasets

| Dataset | Variables | Period | Type |
|---|---|---|---|
| ICES hydrographic database | Temperature, Salinity | 1993– | Cruise CTD / bottle |
| ARGO floats (Ifremer/GDAC) | Temperature, Salinity | 2000– | Autonomous profilers |
| OSTIA / CMEMS SST | Sea surface temperature | 2003– | Level 4 satellite analysis |
| FES2014 / TPXO9 | Tidal constituents | — | Barotropic tidal model |
