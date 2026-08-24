---
name: "AMM7"
full_name: "Atlantic Margin Model 7 km"
description: "NWS regional configuration at ~7 km resolution covering the northwest European continental shelf"
coordinates:
  lat_min: 40.1
  lat_max: 64.9
  lon_min: -19.8
  lon_max: 12.9
resolution: "~1/12° (~7 km)"
depth_range: "0–4000 m"
---

# Atlantic Margin Model 7 km (AMM7)

## Domain Description

AMM7 is a regional ocean model covering the northwest European continental
shelf, from the Iberian Peninsula in the south to the Norwegian Sea in the
north.  It encompasses the North Sea, English Channel, Celtic Sea, Bay of
Biscay, and the shelf break into the northeast Atlantic.

### Geographic Coverage

- **Latitude**: 40.1°N to 64.9°N
- **Longitude**: 19.8°W to 12.9°E
- **Resolution**: ~1/12° spherical grid (~7 km)
- **Vertical**: 40 generalised vertical coordinate (GVC) levels

### Key Features

- Bathymetry from GEBCO with Beckmann–Haidvogel smoothing
- Open boundaries driven by CMEMS NWS AMM7 MY reanalysis (historical) or
  CMIP6 delta-change (future projections)
- Rivers from EMORID (historic) or CMIP6 (future)
- Tidal forcing from TPXO9-atlas (13 constituents)

### Observation Datasets

| Dataset | Variables | Period | Type |
|---|---|---|---|
| ICES hydrographic database | Temperature, Salinity | 1993– | Cruise CTD / bottle |
| ARGO floats (Ifremer/GDAC) | Temperature, Salinity | 2000– | Autonomous profilers |
| OSTIA / CMEMS SST | Sea surface temperature | 2003– | Level 4 satellite analysis |
| FES2014 / TPXO9 | Tidal constituents | — | Barotropic tidal model |
