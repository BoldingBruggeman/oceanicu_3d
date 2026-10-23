# tas (and 6 other variables): ERA5 reference never resampled to daily (2026-10-23)

## The question

While adding a binned-data inset to the bias-correction slide deck
(`docs/bias_correction_slides/extract_point_example.py`), loading ERA5
`t2m` directly for January at the slide's example point (4E, 55N) turned
up n=4464 daily-samples-worth of values for 6 Januaries -- 186*24, i.e.
**hourly**, not daily. That raised the same question already answered for
`evspsbl` (see `evspsbl-amon-vs-daily-mismatch.md`), but in the opposite
direction: does the bias-correction pipeline resample ERA5 down to daily
before quantile-mapping it against daily CMIP6 `tas`?

## Scope: 7 of 10 meteo variables, not just one

Checked every `reference.<var>.resample` setting across all 6 real
production configs on bb-server1 (same set checked for the evspsbl note):

| Variable | ERA5 resample | CMIP6 source resolution |
|---|---|---|
| tas, pr, uas, vas, ps, psl, huss | **not set (`None`)** | `day` |
| net_sw, net_lw, evspsbl | `daily` | `day` (net_sw/net_lw), `Amon` (evspsbl) |

`_apply_scale_offset_resample()` (`ocean-prep/lib/bias_correction/
utils.py:449`) only resamples when `resample: daily|auto` is explicitly
set; the default is `source_cfg.get('resample', 'none')` → no resampling.
7 of the 10 meteo variables never set it, across all 6 configs, for every
model. So for those 7, ERA5 stays at its native **hourly** resolution all
the way into `_correct_per_month()`'s per-calendar-month quantile mapping,
while CMIP6 hist/future are daily -- a 24x resolution mismatch, in the
*opposite* direction from evspsbl's (evspsbl has too few hist/future
samples relative to ref; these 7 have *too many* ref samples relative to
hist/future).

## Why this is a different kind of problem than evspsbl's

evspsbl's issue is under-resolution: ~25 monthly hist samples can't
support `n_quantiles=100` without redundant interpolation of the same
handful of points (see the other note for the mechanism).

This one is not about resolution -- `nanquantile` has plenty of daily
hist/future samples (~750/month) to work with. It's that **ref's quantile
function is built from a different physical quantity than hist/future's**:
hourly instantaneous ERA5 values capture the diurnal swing (hot
afternoons, cold pre-dawns); daily CMIP6 means average that swing away.
QDM maps at matching *rank*, not matching physical meaning -- a day at
hist's 95th percentile (an unusually warm *day*) gets corrected using
ref's 95th percentile, which is pulled from a wider distribution that
also contains unusually warm *hours* within otherwise ordinary days. The
expected effect: corrected daily values should come out with inflated
day-to-day variance/extremes relative to what matched-resolution
quantile mapping would produce, with little effect on the mean/seasonal
cycle (which averages over exactly the diurnal variation that's causing
the mismatch).

## Tested it directly (2026-10-23)

GFDL-ESM4, historical, 2010-2014, `tas` only. Built a trimmed copy of
`bc_correct_core7_fixed.yaml` (`/tmp/bc_test_tas_resample_daily.yaml` on
bb-server1) identical in every other respect (same calibration window,
domain, `n_quantiles=100`, method) but with `reference.tas.resample:
daily` added. Ran via the same isolated-output-dir pattern as the
evspsbl tests:

```bash
/home/kb/miniconda3/envs/ocean-stack/bin/bc-correct \
  --config /tmp/bc_test_tas_resample_daily.yaml --model GFDL-ESM4 \
  --historical --variable tas --output-dir /tmp/tas_resample_daily_test \
  --steps correct
```

Compared against real production output (hourly ref) at the same North
Sea point (4E, 55N), 2010-2014, all months:

| | production (hourly ref) | test (daily ref) |
|---|---|---|
| mean | 10.279°C | 10.268°C |
| std (day-to-day spread) | 4.181°C | 4.144°C |
| p05 | 3.89°C | 3.94°C |
| p95 | 16.77°C | 16.73°C |

Per-month breakdown (°C):

```
month  mean(prod)  mean(test)  diff   std(prod)  std(test)  std-diff
    1        6.09        6.09  -0.00       2.09       1.97   -0.12
    2        5.63        5.60  -0.03       1.80       1.70   -0.11
    3        5.41        5.42  +0.00       1.69       1.60   -0.09
    4        7.13        7.12  -0.01       1.40       1.34   -0.06
    5        9.90        9.90  -0.00       1.73       1.67   -0.07
    6       12.76       12.76  +0.00       1.61       1.55   -0.06
    7       15.11       15.12  +0.01       1.70       1.62   -0.08
    8       16.17       16.17  +0.00       1.78       1.70   -0.09
    9       14.77       14.76  -0.02       1.49       1.40   -0.09
   10       12.59       12.56  -0.03       2.34       2.17   -0.18
   11       10.00        9.96  -0.04       2.05       1.95   -0.10
   12        7.45        7.42  -0.03       1.76       1.67   -0.09
```

Mean diff (RMSE of the *difference*, not of either series against
truth): 0.128°C, max single-day abs diff 1.86°C.

## Conclusion

The mean/seasonal-cycle prediction holds: the difference in the mean is
negligible (≤0.04°C every month). But the variance prediction also
holds, and consistently, not as noise -- **production (hourly ref) shows
higher day-to-day std than the daily-ref test in all 12 of 12 months**,
by 0.06-0.18°C (3-9% of the std itself), with p05/p95 narrower in the
daily-ref version too. Small in absolute terms for `tas`, but real,
systematic, and in the predicted direction every month -- not the kind
of result you'd get from chance.

**This does not, by itself, mean all historical/future `tas` (or
pr/uas/vas/ps/psl/huss) output needs to be redone.** A ~0.1-0.2°C
reduction in daily std is unlikely to matter for most downstream uses.
Whether it's worth fixing depends on whether anything downstream is
sensitive to daily-scale variance or extremes specifically (vs. monthly/
seasonal means, which are essentially unaffected) -- a judgement call,
not something this test resolves on its own. `pr`, being multiplicative
and more extreme-driven, is the variable most likely to show a larger
effect than `tas` did here; not yet tested.

## Fix, if pursued

Add `resample: daily` to `reference.<var>` for `tas`, `pr`, `uas`, `vas`,
`ps`, `psl`, `huss` in the 6 production configs -- the same mechanism
already used for `net_sw`/`net_lw`/`evspsbl`, no code change needed. See
`bias-correction-pipeline-performance.md` for why this fix is also a
prerequisite for a separate, independently-discovered performance fix.

Test artifacts (`/tmp/bc_test_tas_resample_daily.yaml`,
`/tmp/tas_resample_daily_test/`, `/tmp/compare_tas_resample.py`) left on
bb-server1 for now. No production config or output was touched.
