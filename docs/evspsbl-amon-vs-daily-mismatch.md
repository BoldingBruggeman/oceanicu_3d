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

## Tested the n_quantiles half of option 2 (2026-10-22)

Option 2 has two independent parts: resampling the ERA5 reference to
monthly (needs a code change -- not done), and dropping `n_quantiles` for
`evspsbl` specifically (needs no code change -- already exposed via
`bc-correct --n-quantiles N`, and `--variable evspsbl` already restricts a
run to one variable). Only the second part was tested, in isolation, to
see whether `n_quantiles` alone makes a measurable difference even with
the reference still daily (i.e. without fixing the representativity
mismatch -- this isolates the "100 quantiles from 25 samples is mostly
redundant interpolation" effect from the "daily ref vs monthly hist"
effect).

```bash
cd /data/BiasCorrected
/home/kb/miniconda3/envs/ocean-stack/bin/bc-correct \
  --config bc_correct_core7_fixed.yaml --model GFDL-ESM4 --historical \
  --variable evspsbl --n-quantiles 20 \
  --output-dir /tmp/evspsbl_n20_test --steps correct
```

GFDL-ESM4, historical, 2010-2014, `evspsbl` only, written to
`/tmp/evspsbl_n20_test` (not the production path) -- ran clean, exit 0,
~25 min per model (dominated by the daily-ERA5 reference load/regrid per
month, not by the quantile count itself). Repeated for CNRM-ESM2-1
(`/tmp/evspsbl_n20_test_cnrm`, member `r1i1p1f2` picked up automatically
from the config's `member_defaults`) and MPI-ESM1-2-HR
(`/tmp/evspsbl_n20_test_mpi`), run sequentially to avoid contending for
ERA5 disk I/O on the shared machine. All three ran clean, exit 0.

Compared against each model's real production output (`n_quantiles=100`)
at the same North Sea point used in the slide-deck figures (4°E, 55°N),
both converted to mm/day:

| Model | mean, prod (n=100) | mean, test (n=20) | max abs diff (mm/day) |
|---|---|---|---|
| GFDL-ESM4    | 7.50 | 7.65 | 2.17 |
| CNRM-ESM2-1  | 7.35 | 7.57 | 3.26 |
| MPI-ESM1-2-HR| 8.34 | 8.45 | 2.88 |

All three: the mean shifts up by a small, consistent amount (1-3%)
dropping `n_quantiles` 100->20, plus occasional single-month-year outliers
of 2-3 mm/day. So `n_quantiles` alone measurably changes the correction at
this sample size, confirming the "100 quantiles from only ~25 samples is
mostly redundant re-interpolation of the same staircase" mechanism
described above is not just theoretical -- it holds for all 3 models.

**Correction to an earlier draft of this note**: GFDL-ESM4's outliers
happened to land on October in all 5 years (+1.33 to +2.17 mm/day), which
an earlier version of this note described as "October stands out" -- that
was checked against CNRM and MPI and does **not** generalise. CNRM's
biggest outliers are Aug 2010 (+3.26), Jul 2013 (+2.67), Sep 2013 (+2.69);
MPI's are Sep 2013 (+2.88), Oct 2013 (+2.40), Jan 2012 (+1.94) -- different
months, different years, no shared calendar-month pattern across models.
The real mechanism is simpler and more general: whichever calendar
month's ~25-year sample happens to contain an unusually extreme single
year, for that particular model, is the one where `n_quantiles=100` vs
`20` diverges most -- not a property of any specific month.

This test only covers the `n_quantiles` half of option 2 -- the ERA5
resample-to-monthly half, and option 3 (the disaggregation fix), are
still untouched. `/tmp/evspsbl_n20_test{,_cnrm,_mpi}` are left on
bb-server1 for now. No production config or output was changed by this
test -- only `evspsbl`, only `historical`, only these three test output
directories.

## Separate, confirmed bug: all production evspsbl output was 3.6x too large (2026-10-23)

Discovered while testing a `resample: daily` fix for `pr`'s ERA5
reference (see `tas-era5-hourly-vs-daily-resample.md`): the test's output
mean dropped from a plausible ~2.7 mm/day to an implausible ~0.1 mm/day.
That turned out not to be a real physical effect -- it was a bug in
`_apply_scale_offset_resample`'s `resample: daily` option, and `evspsbl`
has had `resample: daily` set in every real production config all along.

**The bug**: ERA5 `e` (evaporation, like `tp`/precipitation) is an
**hourly-accumulated depth in metres**, not a rate. `_apply_scale_offset_
resample` does `da.resample(time='1D').mean()` to produce a "daily"
value -- averaging 24 hourly accumulated depths instead of summing them,
with no prior conversion from an accumulated depth to a rate (unlike
`net_sw`/`net_lw`, whose `scale_factor: 0.000277778` = 1/3600 already
converts their own hourly-accumulated J/m² into average W/m² *before*
`.mean()` runs -- that case is correct; `evspsbl`'s `scale_factor: -1` is
only a sign flip, nothing else).

Checked directly against real ERA5 `e` data (4E, 55N, 2010): the
resulting scale error is an exact, data-independent constant --
`86400 / (24 * 1000) = 3.6` -- because it only depends on "24 hours in a
day" and "1000 kg/m3 water density", not on the actual evaporation
values. Verified the day's worth of hourly ERA5 files have complete,
gap-free 24-sample coverage (spot-checked 1990/2000/2010/2014), so the
constant holds exactly, not approximately.

Confirmed this constant propagates through the *entire* correction
unchanged: `evspsbl`'s correction `kind` is multiplicative (`'*'`,
confirmed in every production config that includes it) and `fut_tau`/
`q_hist_at_fut` in `_qdm_1d` don't depend on `ref` at all -- only
`q_ref_at_fut` does, and both `nanquantile` and `np.interp` are exactly
linear under positive scalar multiplication. So a uniform 3.6x inflation
of every `ref` value produces a uniform 3.6x inflation of every
corrected output value, with no distortion to shape -- confirmed
numerically too: dividing a real production file by 3.6 exactly matches
a fresh run with a properly rate-converted reference (max abs diff 0.0,
not just close).

**Fixed, retroactively, on the already-existing files** (not yet fixed
in the code -- see below): all 1374 real production `evspsbl_bc_*.nc`
files across all 3 models/all scenarios (687 daily `correct()`-stage
files + 687 disaggregated files, which inherit the same 3.6x from the
daily files they're built from) were divided by exactly 3.6, in place,
on bb-server1. Originals backed up first to
`/data/BiasCorrected/evspsbl_3p6x_bug_backup_20261023/` (17GB, verified
1374/1374 files present before touching anything). Ran via
`/tmp/fix_evspsbl_3p6_scale.py` (temp-file-then-atomic-move per file, no
partial-write risk), validated on a throwaway copy first (exact 3.6
ratio, shape/dims/coords/attrs all preserved), then applied to all 1374
real files: 1374 fixed, 0 failed. Spot-checked 5 files afterwards across
all 3 models, both historical and an SSP scenario, both daily and
disagg types, against the backups -- all exactly 3.6x, shapes intact.

**This does NOT fix**:
- The disaggregated files' separate, independent, already-documented
  288-hours-not-~8760 shape bug above -- their magnitude is now correct,
  but they still only cover 12 scattered days per year, not a full year.
- The Amon-vs-daily sample/quantile-resolution issue documented
  elsewhere in this note -- entirely separate mechanism.
- **The code itself.** `_apply_scale_offset_resample`
  (`ocean-prep/lib/bias_correction/utils.py:449`) still has this exact
  bug for any future `bc-correct` run that uses `resample: daily` on an
  accumulated (not rate) local variable without a scale_factor that
  already converts it to a rate -- `evspsbl` today, and `pr`/any other
  accumulated ERA5 variable if `resample: daily` is ever added to it (as
  was being tested when this was found). The retroactive file patch
  fixes existing output only; the next real `evspsbl` run will reproduce
  the bug unless the code is fixed too -- not yet done, pending a
  decision on the right general fix (sum instead of mean, or require an
  explicit rate-converting `scale_factor` before allowing `resample:
  daily` on a non-rate variable).
