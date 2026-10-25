# Pre-production check family: status + DIC/TA future-signal suggestion

## Status of the "final check" family

| Check | Status | Scope |
|---|---|---|
| `final-boundary-check` (ocean-prep) | Done, clean | Bio open boundaries (no3/po4/si/o2/dissic/talk) across models/scenarios/methods. One real issue found and fixed this session: a single negligible negative `po4` value (`bdy_3d_po4_rawcmip6_20150116_21001216.nc`, time idx 438/nbdyp 200/surface, -0.00052 mmol/m3), patched to its absolute value, re-verified clean. |
| `river-final-check` (ocean-prep, `cli/river_final_check.py`) | In active development (another session), 5 commits so far, latest "fix report readability" | River discharge (Q) + the six FABM nutrient tracers (NO3/NH4/PO4/Si/TALK/DIC) rivers.py reads: historical EMORID file, nutrient climatology files, projected future flow files. Already found a real, independent issue on its first real run: 155/446 Danish stations go flat zero from 2023-01-01 through the historical record's end (2024-12-31), flagged `Q_flag=1` ("interpolated"), not "missing" -- so a missing-data filter alone would not catch this. |
| `meteo-final-check` | Not started | Requested in the other session; no file yet. |

This mirrors the pattern already proven twice now (`final-boundary-check`, `river-final-check`): a cheap, automated sanity check against the real production files catches a genuine data-quality issue a human would only notice from an odd-looking plot, well before anyone has to.

## Suggested addition: a DIC future-signal assurance check

Separately, this session investigated a screenshot showing a considerable
CMIP6-vs-CMEMS difference in open-boundary DIC/TA near the Kattegat/Baltic
segment (write-up: `NSe/DIC_TA_BOUNDARY_PLAN.md`). Alongside that, a
direct question came up: how do we know the boundary files actually carry
a real, growing future DIC signal (expected to be a strong one, as
atmospheric CO2 rises) rather than a flat or buggy delta?

Verified directly against the real `bdy_3d_dissic_deltachange_*.nc` /
`bdy_3d_talk_deltachange_*.nc` files (GFDL-ESM4, surface, area-mean
across all boundary points):

| | 2015 | 2100 | linear trend |
|---|---|---|---|
| DIC, ssp126 | 2171 mmol/m3 | 2200 mmol/m3 | +0.34/yr |
| DIC, ssp370 | 2157 mmol/m3 | 2274 mmol/m3 | +1.39/yr |
| TALK, ssp126 | 2356 mmol/m3 | 2353 mmol/m3 | -0.05/yr |
| TALK, ssp370 | 2355 mmol/m3 | 2345 mmol/m3 | -0.14/yr |

This is the right signature of a correctly-working trending delta-change
(`ocean-prep/lib/boundaries/delta_change.py:compute_cmip6_delta_trend`):
DIC rises continuously and is clearly scenario-differentiated (ssp370 ~4x
ssp126's slope, matching the two pathways' CO2 divergence); TALK stays
essentially flat, as expected since alkalinity isn't directly CO2-driven.
A regression to the OTHER available method in that same module
(`compute_cmip6_delta`, a single fixed historical-to-future-period shift)
would silently flatten this trend with no error anywhere.

**Suggestion**: add this exact check -- yearly area-mean + linear slope
per scenario, asserting ssp370's DIC slope clearly exceeds ssp126's while
TALK's slope stays near zero for both -- to `final-boundary-check` (or a
new standing diagnostic alongside it), the same way `river-final-check`'s
zero-flow-tail detector caught something no one was specifically looking
for. Also worth watching: `compute_cmip6_delta_trend`'s own docstring
notes it clips a small fraction of cells (~0.001-9% per segment) where
the fitted delta would otherwise go unphysical -- the Kattegat/Baltic
segment (lowest absolute DIC/TA) is the most likely to hit that clip, so
a per-segment clip-rate report would be a useful companion to the
trend check itself.

## Logged, deferred (not acted on)

Fish-class (mizer) output generation: `_landings` needs a further guard
when fish classes are generated for output. Flagged by the user
mid-conversation with an explicit "wait with this last one" -- noted here
so it's in the loop for a future session, not forgotten, but deliberately
not investigated or implemented yet.
