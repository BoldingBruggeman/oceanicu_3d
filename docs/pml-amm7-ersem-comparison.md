# PML's CO9p2 AMM7 NEMOv4.0.4 ERSEM configuration, and how it compares (2026-10-01)

Summary of Partridge, Artioli, Powley & Lessin (PML, December 2025),
*"A Reproducible CO9p2 AMM7 NEMOv4.0.4 ERSEM Configuration"*, and how it
relates to this project's own NSe/CMEMS+ERSEM work (today's session:
`driver/scripts/hydrography.py`'s vertical-remap perpetual spin-up,
`docs/conservative-vertical-remap.md`).

## Summary

Documents a fully-reproducible (river data excepted) NW European Shelf
setup: NEMO v4.0.4 physics at AMM7 (~7km), coupled to ERSEM
biogeochemistry (52 pelagic + 36 benthic state variables).

**Physics**: cold-start at rest, Jan 1993; T/S IC and boundaries from
GloSea6 (Met Office) via `pyBDY`; tides from FES2014 (15 constituents);
ERA5 atmospheric forcing (8 fields) via `pySBC`.

**ERSEM initial conditions**: nutrients/O2 from WOA23 (monthly to 800m,
annual splice below); NH4 = 0.25×NO3; DIC/TA from GLODAP annual
climatology; chlorophyll from OC-CCI split into 4 PFTs (Brewin ratios);
zooplankton/DOC/POM built from fixed empirical ratios and Redfield
stoichiometry. Benthic ICs are mostly spatially-uniform constants from
prior experience (shelf vs. open-ocean split), **except** porewater
variables, set to equilibrium with the deepest pelagic value.

**The spin-up**: benthic particulate organic matter "can take many
years, even decades" -- their fix is **six consecutive 5-year
simulations (1993-1998), each restarting from the previous one's final
tracer field**, to get a stable 1993 benthic IC. This is the same core
technique (restart-chained spin-up cycles) built in this session.

**Boundaries**: WOA climatology for nutrients/O2; for DIC/TA (GLODAP,
representative of 2002) they fit an **explicit secular trend**
(exponential in depth, separate Atlantic/Arctic fits) to account for
ongoing anthropogenic acidification, plus nitrate-anomaly-driven
seasonality -- layered on top of the static climatology, not just the
climatology alone. Most other biology is held at small fixed constants
at the boundary (deliberately, citing Polton et al. 2023, to avoid
WOA-vs-Neumann mismatch artifacts).

**The one real gap**: river biogeochemistry data is proprietary (CMEMS
NWS reanalysis river product, Lenhart et al.-derived) -- "available
upon request," not open/reproducible -- same category of gap as our own
EMORID river file.

## How it compares to our work

- **Same biogeochemistry, different hydrodynamic code**: ERSEM in both,
  but they run NEMO (fixed z/sigma-family coordinates); we run pyGETM
  (generalized/adaptive vertical coordinates). That difference is
  exactly *why* our vertical-remap problem exists at all -- NEMO's more
  rigid vertical coordinate likely drifts less between spin-up cycles
  than pyGETM's GVC/Adaptive schemes, which respond to the run's own
  stratification/surface-elevation history.
- **Same spin-up idea, one gap we found and they didn't document**:
  their write-up just says "use the final tracer field as restarts for
  the next simulation" -- no mention of handling a vertical-grid
  mismatch between cycles. We hit that gap directly (this session's own
  `_seed_fabm_state_from_restart` conservative-remap investigation) and
  fixed it; their paper is silent on whether/how they handled it, or
  whether NEMO's coordinate scheme makes it less of an issue for them.
- **Same DIC/TA scarcity problem, different fix**: they fit a
  physically-motivated secular trend (anthropogenic CO2 uptake) on top
  of a GLODAP annual climatology; our bridge-period solution (day-of-year
  climatology cycled from the real ~2-year CMEMS ANFC record,
  `bio_carbon_climatology_cycled_20100101_20141231.nc`) is more empirical
  and doesn't correct for any trend. Their approach is arguably the more
  defensible one for a genuinely trending quantity -- worth considering
  if our own bridge climatology gets revisited.
- **Same real conclusion on automation**: they ran their spin-up as six
  manually-sequenced simulations, not an automated pipeline. We reached
  the identical conclusion today (decided not to automate the
  round-to-round restart toggle, since it's only done a handful of
  times) -- independent confirmation that manual iteration is a
  reasonable choice at this scale, not a shortcut unique to us.
- **Different physics forcing products**: GloSea6 (Met Office) for them
  vs. real CMEMS NWS reanalysis/ANFC products for our CMEMS setup -- a
  different "best available" physics source family, though serving the
  same IC/boundary role.

## Source

`~/CO9-AMM7-NEMO-ERSEM-CFG-PML Publishing.pdf` (PML Publishing, December
2025). Companion repository:
https://github.com/pmlmodelling/NEMO_project_template/tree/AMM7
