---
name: "NSe"
full_name: "North Sea Extension"
description: "NSe modelling domain covering the North Sea and adjacent shelf seas including the Kattegat and English Channel"
coordinates:
  lat_min: 47.5
  lat_max: 62.0
  lon_min: -8.0
  lon_max: 13.0
resolution: "~1/12°"
depth_range: "0–500m (Viking Bank reference)"
---

# North Sea Extension (NSe)

## Domain Description

The NSe domain is a high-resolution regional configuration covering the
northwest European shelf and the southern North Sea, from the English Channel
in the south to the Norwegian coast in the north.  It extends eastward into
the Kattegat and the entrance to the Baltic Sea.

### Geographic Coverage

- **Latitude**: 47.5°N to 62.0°N
- **Longitude**: 8°W to 13°E
- **Resolution**: ~1/12° spherical grid (~7–9 km)
- **Vertical**: 40 generalised vertical coordinate (GVC) levels

### Key Features

- 9 open boundary segments, auto-detected from coordinate file
  (min gap 0.5°); segment 7 (eastern Kattegat) uses Baltic product override
  throughout because the NWS domain is masked in that region
- Bathymetry from GEBCO 2025 with Beckmann–Haidvogel smoothing (rx0 ≤ 0.2)
  and manual corrections for Belt Sea and Skagen straits
- Future climate projections use TPXO9 tidal forcing + CMIP6 mean-state
  correction (zos, uo, vo) plus a 3-D delta-change signal for temperature
  and salinity

### Observation Datasets

| Dataset | Variables | Period | Type |
|---|---|---|---|
| ICES hydrographic database | Temperature, Salinity | 1993– | Cruise CTD / bottle |
| ARGO floats (Ifremer/GDAC) | Temperature, Salinity | 2000– | Autonomous profilers |
| OSTIA / CMEMS SST | Sea surface temperature | 2003– | Level 4 satellite analysis |
| FES2014 / TPXO9 | Tidal constituents | — | Barotropic tidal model |
