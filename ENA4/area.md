---
name: "ENA4"
full_name: "Extended North Atlantic 1/4°"
description: "Northeast Atlantic regional configuration at ~1/4° resolution covering the European Atlantic margin"
coordinates:
  lat_min: 40.0
  lat_max: 70.0
  lon_min: -20.0
  lon_max: 13.7
resolution: "~1/4° (~25 km)"
depth_range: "0–4000 m"
---

# Extended North Atlantic 1/4° (ENA4)

## Domain Description

ENA4 is a coarse-resolution configuration of the northeast Atlantic, covering
the full European continental margin from the Iberian Peninsula to the
Norwegian Sea.  It serves as a parent model for downscaling experiments and
provides open boundary conditions to the higher-resolution NS and NSe domains.

### Geographic Coverage

- **Latitude**: 40.0°N to 70.0°N
- **Longitude**: 20.0°W to 13.7°E
- **Resolution**: ~1/4° spherical grid (~25 km)
- **Vertical**: 40 generalised vertical coordinate (GVC) levels

### Key Features

- Covers shelf and open-ocean regions including the shelf break
- Used primarily as a boundary provider / downscaling intermediate
- Bathymetry from GEBCO with Beckmann–Haidvogel smoothing

### Observation Datasets

| Dataset | Variables | Period | Type |
|---|---|---|---|
| ICES hydrographic database | Temperature, Salinity | 1993– | Cruise CTD / bottle |
| ARGO floats (Ifremer/GDAC) | Temperature, Salinity | 2000– | Autonomous profilers |
| OSTIA / CMEMS SST | Sea surface temperature | 2003– | Level 4 satellite analysis |
| FES2014 / TPXO9 | Tidal constituents | — | Barotropic tidal model |
