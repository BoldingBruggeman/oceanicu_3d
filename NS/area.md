---
name: "NS"
full_name: "North Sea"
description: "North Sea modelling domain covering the northwest European shelf from the English Channel to the Norwegian coast"
coordinates:
  lat_min: 48.5
  lat_max: 60.8
  lon_min: -5.1
  lon_max: 13.4
resolution: "~1/20° (~5 km)"
depth_range: "0–700 m"
---

# North Sea (NS)

## Domain Description

The NS domain is a high-resolution configuration of the North Sea and adjacent
shelf seas, spanning from the English Channel in the south to the Norwegian
Trench in the north, and from the eastern Atlantic shelf break to the Danish
and German Bight.

### Geographic Coverage

- **Latitude**: 48.5°N to 60.8°N
- **Longitude**: 5.1°W to 13.4°E
- **Resolution**: ~1/20° spherical grid (~5 km)
- **Vertical**: 40 generalised vertical coordinate (GVC) levels

### Key Features

- Strong tidal dynamics with M2 amplitudes up to 3 m in the Southern Bight
- Seasonal stratification in the central and northern North Sea
- Significant freshwater influence from Rhine, Elbe, Thames, and Scottish rivers
- Norwegian Trench provides the deepest bathymetry (~700 m)
- Open boundaries driven by AMM7 CMEMS reanalysis (historical) or
  CMIP6 delta-change (future projections)

### Observation Datasets

| Dataset | Variables | Period | Type |
|---|---|---|---|
| ICES hydrographic database | Temperature, Salinity | 1993– | Cruise CTD / bottle |
| ARGO floats (Ifremer/GDAC) | Temperature, Salinity | 2000– | Autonomous profilers |
| OSTIA / CMEMS SST | Sea surface temperature | 2003– | Level 4 satellite analysis |
| GESLA tide gauges | Sea level | — | High-frequency coastal gauge records |
| FES2014 / TPXO9 | Tidal constituents | — | Barotropic tidal model |
