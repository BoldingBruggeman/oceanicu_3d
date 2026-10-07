# Atmospheric CO2/N2O forcing: historical file stops 2014, real runs now go past it

**Update (2026-10-07): Option A below is now built.** Set
`fabm.ERSEM.ghg_scenario` (e.g. `ssp245`) in any CMEMS/WOA config to
splice in that SSP's own CO2/N2O/N-deposition projection past the
historical file's 2014-12 end -- same mechanism CMIP6 runs already use,
now available independent of `boundaries.fabm`'s own source. Leaving it
unset now raises a loud, immediate error at FABM setup time if the run's
own stop date is past 2015-01-01, instead of crashing deep into the run.
Option B (real NOAA observations instead of a scenario projection for
2015-2024) is still open if preferred -- not built yet.

## What's there today

`bb-server1:/data/FABM/GHGConcentrations/co2_historical_15deg.nc` (and its
`n2o_historical_15deg.nc` sibling, same situation) is the standard CMIP6
input4MIPs historical GHG-concentrations product (`UoM-CMIP-1-2-0`,
Meinshausen et al. 2016), subset to the 3 15-degree latitude zonal bands
overlapping our AMM7/NSe domain (37.5N/52.5N/67.5N), built by
`download_ghg_15deg.py` sitting alongside it.

Confirmed directly (xarray): it covers **1990-01 to 2014-12 only** (300
monthly records). That's not a download gap -- the CMIP6 "historical"
experiment is defined by protocol to stop exactly at 2014-12; there is no
later-vintage "historical" product to fetch instead. 2015 onward is
covered by separate SSP **scenario** files
(`co2_ssp{126,245,370,585}_15deg.nc`, already downloaded, same folder,
same script).

`driver/scripts/fabm.py`'s `configure_fabm` uses the historical file
**alone, with no splice**, for any run whose `boundaries.fabm` source
isn't CMIP6 -- i.e. every CMEMS/WOA run. The CMIP6-scenario splice
(historical + `co2_{scenario}_15deg.nc`) only fires when
`boundaries.fabm.CMIP6.scenario` is set, a field that doesn't exist for
CMEMS/WOA at all.

## The actual risk

Checked `pygetm.input.TemporalInterpolation` directly
(`pygetm/input/__init__.py`, `_move_to_next`): once the simulation clock
passes the last time point in a non-climatology time series, it
**raises** --

    Cannot interpolate <...> because end of time series was reached (<last time>).

-- it does not hold the last value flat. So **any CMEMS/WOA run whose
stop date is past 2014-12 will crash once it gets there**, from this
dependency alone. That now includes the `validation`/`production`
`--kind` periods just added to `add_experiments.py` (e.g. `validation`'s
own 2010-01-01 to 2024-01-01 default) -- those will hit this wall
roughly 10 years into the run.

Same applies to N2O via the identical mechanism
(`n2o_historical_15deg.nc`).

## Two ways to extend it -- need a decision

**A. Quick, data already on disk**: splice in one of the existing
`co2_ssp{126,245,370,585}_15deg.nc` scenario files past 2014, the same
way CMIP6 runs already do. Needs a small config/code change (CMEMS/WOA
currently has no field to pick a scenario at all) -- no new data
download. Downside: it's a scenario *projection*, not an observation,
for a period (2015-2024) that is now actually real history.

**B. Real-observation extension**: NOAA GML's Marine Boundary Layer
(MBL) CO2 reference is a genuine observational product -- starts 1979,
continuously extended to present as new flask-network samples are
processed, and resolved finely enough by latitude (0.05 sine-latitude
steps) to extract the exact same 37.5/52.5/67.5N bands this file already
uses (https://gml.noaa.gov/ccgg/mbl/mbl.html). Matches this project's
own established preference for real data over a scenario proxy wherever
real data exists (same call made for the CMEMS DIC/TA historical bridge
this project already built). Catch: access is through an interactive
request form (https://gml.noaa.gov/ccgg/mbl/data.php), not a one-line
static URL like `download_ghg_15deg.py`'s ESGF/OPeNDAP pulls -- would
need NOAA's underlying bulk/FTP endpoint for this product to script it
reproducibly the same way.

Please let us know which direction to take (or any other input) before
we build either fix.
