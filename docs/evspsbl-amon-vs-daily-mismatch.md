# evspsbl: CMIP6 Amon (monthly) vs. ERA5 daily reference mismatch (2026-10-22)

## The question

CMIP6's `Amon` tables are monthly means. ERA5, as used by the bias-correction
pipeline, is daily (or sub-daily, resampled to daily). What happens when a
variable's CMIP6 source is monthly but its reference is daily?

## Scope: evspsbl only

Checked every real production config on bb-server1
(`/data/BiasCorrected/bc_correct_core7_fixed.yaml`,
`bc_correct_core7_fixed_histdiag.yaml`, `bc_correct_cnrm_core7_fixed.yaml`,
`bc_meteo_gfdl_core7_recalib.yaml`, `bc_meteo_mpi_ssp126_core_diag.yaml`,
`bc_meteo.yaml`) for which CMIP6 table each variable's `historical`/
`future_model` block uses:

| Variable | Table |
|---|---|
| tas, pr, uas, vas, ps, psl, huss, rsds, rsus, rlds, rlus, hfls | `day` |
| **evspsbl** | **`Amon`** |

`evspsbl` is the only variable anywhere using `Amon`. Its ERA5 reference
(`era5_e_{year}.nc`) is still `resample: daily` in every config -- so for
every other variable, ref and hist/future are both daily (matched
resolution); for `evspsbl` alone, ref is daily and hist/future is monthly.

## Why this matters mechanically

`_correct_per_month()` (`ocean-prep/lib/bias_correction/algorithms.py:409`)
slices ref/hist/future independently by `.dt.month`, with no requirement
that sample counts match -- `nanquantile` doesn't error on mismatched
sample counts, it just estimates a quantile function from whatever samples
it's given. For a 25-year calibration window, that means:

- Every `day`-table variable: ref and hist each get ~750 samples/calendar
  month (25 years x ~30 days). Comparable distributions, comparable
  quantile-function resolution.
- `evspsbl`: ref gets ~750 samples/month (daily ERA5), hist/future get
  only ~25 samples/month (one Amon value per year). The ref quantile
  function is resolved from the real daily-value spread; the hist/future
  quantile function is resolved from an already-smoothed monthly-mean
  distribution, estimated from 30x fewer points. At the production
  `n_quantiles: 100`, most of those 100 points are interpolated from only
  ~25 real values -- exactly the situation `bias_correction_guide.md`
  §4.4 already warns about ("with monthly data you have only 30 samples;
  use n_quantiles=20 or switch to delta"), but the production configs
  don't special-case `n_quantiles` down for `evspsbl`.

## How the quantiles are actually filled

No `method=` override is passed anywhere (`algorithms.py:123,153`), so both
`nanquantile` calls use numpy's default `linear` interpolation. That method
doesn't fit a distribution -- it sorts the real sample values (the order
statistics) and connects them with straight lines, then reads off the
value at each requested probability in `tau = np.linspace(0, 1,
n_quantiles)`. For a sample of size *n*, probability *p* maps to a
"virtual index" `h = (n-1)*p`; if *h* isn't an integer, the result is a
linear blend of the two neighbouring sorted values at `floor(h)` and
`ceil(h)`. No extrapolation, no resampling, no statistical model -- just
piecewise-linear interpolation of the empirical CDF.

Consequence for `n_quantiles: 100` vs. sample size:

- Daily variables, ~750 samples/month: 100 requested probabilities land on
  100 genuinely distinct interpolation segments between ~750 real values
  -- each of the 100 quantile points is informative.
- `evspsbl`, ~25 monthly samples/month: the *same* 25 sorted values get
  revisited roughly 4x each (100/25) by the 100 requested probabilities.
  Most of those 100 "quantiles" are redundant re-interpolations of the
  same handful of staircase steps -- they add smoothness to the stored
  curve, not real information. This is the concrete mechanism behind the
  guide's own rule of thumb (§4.4): past ~n_quantiles=20 for a 25-sample
  month, you are not resolving more quantiles, you are oversampling the
  same 25 points.

It compounds downstream too: `fut_tau = np.interp(future, np.sort(hist),
np.linspace(0, 1, len(hist)), left=0.0, right=1.0)` (`algorithms.py:157`)
ranks each future value against that same 25-point sorted `hist` array --
so a future value above `hist`'s single highest of 25 monthly values gets
clamped to `fut_tau=1` outright (and below the lowest, clamped to `0`),
losing any sense of *how* extreme it actually is relative to the
correction.

## Confirmed, worse, compounding bug: disaggregation

`apply_diurnal_correction()` (`algorithms.py:583`) assumes every timestamp
in its input array is one **day**, and expands it via
`np.repeat(daily_bc_chunk.values, n_steps, axis=0)` (`n_steps=24` for
hourly). Fed a monthly-mean array, each monthly value gets repeated into
24 hours -- i.e. it is silently treated as "one day," not "one month."

Verified directly against the real files on bb-server1
(GFDL-ESM4/historical/2012):

```
evspsbl_bc_bilinear_historical_2012.nc:         time=12   (one value/month, mid-month timestamps)
evspsbl_bc_bilinear_historical_disagg_2012.nc:  time=288  (= 12 x 24)
```

288 hours is **12 single days** (Jan 16, Feb 15, ... Dec 16 -- the
mid-month timestamps carried straight through), not the ~8760 hours a
full year of hourly forcing should have. The file is named and shaped
like a plausible hourly-forcing file; nothing in the pipeline raises an
error. Anyone consuming `evspsbl_bc_*_disagg_*.nc` as a complete year of
hourly evspsbl forcing is actually getting real data for 12 scattered
days and nothing (or a reindex artefact) for the other ~353.

## Checked PANGEO directly: daily evspsbl doesn't exist, anywhere (2026-10-22)

Queried the PANGEO CMIP6 intake-esm catalog directly on bb-server1
(`pangeo-cmip6.json`, 514,818 entries) -- not just for GFDL-ESM4/
CNRM-ESM2-1/MPI-ESM1-2-HR, but across every model in the whole catalog:

```python
sorted(df[df.variable_id == 'evspsbl'].table_id.unique())
# ['Aclim', 'Amon']
```

`evspsbl` has **no `day`-table entries at all, for any model**. This is a
CMOR/CMIP6-protocol fact, not a per-model data gap: daily evaporation flux
was simply never requested in the CMIP6 experiment design, so there is no
daily source to switch to. This rules out "pull the day table instead" as
an option -- see the fix list below, updated accordingly.

## Not yet fixed -- options

1. ~~Override `evspsbl` to pull CMIP6's `day` table instead of `Amon`~~ --
   **ruled out**: confirmed via direct PANGEO catalog query that daily
   `evspsbl` does not exist for any CMIP6 model, so there is nothing to
   override to.
2. Aggregate the ERA5 reference to monthly too (apples-to-apples) and drop
   `n_quantiles` for this variable specifically (e.g. ~20, or `delta`).
   `_apply_scale_offset_resample` (`utils.py:449`) currently only supports
   `resample: daily|auto|none` -- a `monthly` option would need to be added.
3. Disable diurnal disaggregation for `evspsbl` rather than silently
   emitting a 288-hour file labelled as a full year -- or add a frequency
   check that errors instead of silently repeating monthly means as single
   days.

Given (1) is off the table, (2) and (3) are the only real paths forward.
No code or config has been changed; this is a documented finding only.
