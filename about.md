This site presents validation results and future-scenario projections
for [pyGETM](https://github.com/BoldingBruggeman/getm-rewrite) ocean
model setups developed within the [OceanICU](https://ocean-icu.eu/)
project.

## Modeling areas

- **NSe** — an extended North Sea domain, covering the North Sea and
  its approaches down to the Baltic entrance.
- **NS** — a GETM setup similar to the
  [legacy GETM North Sea setup](https://sourceforge.net/p/getm/getm-setups/ci/master/tree/NorthSea/),
  updated with GEBCO 2025 bathymetry and current forcing.
- **AMM7** — a well-tested shelf-seas configuration, also run with
  [NEMO](https://www.nemo-ocean.eu/) as the model, from
  [PML](https://pml.ac.uk/).

Each area's own page lists its real experiments, validation results,
and (where applicable) future-scenario boundary/forcing data.

## Forcing and boundary data

Historical atmospheric forcing, ocean boundaries and river discharge
come from reanalysis products (ERA5, CMEMS, EMORID). Future scenarios
add CMIP6 model output as the climate-change signal, either bias-
corrected directly against ERA5 (atmospheric forcing) or as a delta-
change applied on top of the historical reanalysis (ocean boundaries).
See [Data Sources](/data-sources/) for exactly what feeds each area,
and [Scenarios](/scenarios/) for the resulting projections.

## Source

The model setups and the tooling that generates this site are open
source: [github.com/BoldingBruggeman/oceanicu_3d](https://github.com/BoldingBruggeman/oceanicu_3d).
