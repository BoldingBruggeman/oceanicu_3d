# bc-correct: ERA5 reference re-read 12x per variable, not once (2026-10-23)

## Context

Triggered by the `tas` hourly-vs-daily ERA5 resample investigation
(`tas-era5-hourly-vs-daily-resample.md`): if that fix (or any full core-7
redo) means rerunning bias correction for multiple models/scenarios/
variables, it's worth knowing first whether the pipeline is wasting time
that a redo would otherwise multiply. It is, substantially, and the fix
is small, isolated, and doesn't change any output.

## Root cause

`_correct_per_month()` (`ocean-prep/lib/bias_correction/algorithms.py:178`)
loops over the 12 calendar months. For each one it does `ref_m =
ref.sel(...)`, then, if `ref_m` is still Dask-backed,
`ref_m.compute(scheduler='threads')`. `ref` (the ERA5 reference, loaded
via `_load_local_files` with `chunks={'time': 365}`) is **never
materialized before this loop**.

By contrast, `hist`/`future_all` are already eagerly materialized --
`_regrid_to_target()` (`utils.py:903-908`) calls `.compute()` on its
`source` argument before regridding, for an unrelated reason (xESMF on a
chunked Dask array multiplies the chunk count by the input/output grid
ratio, blowing up the task graph). So by the time `_correct_per_month`
runs, the `_is_dask_backed(hist_m)`/`_is_dask_backed(fut_m)` checks in
the loop are already always `False` -- dead code for the current call
sites. Only `ref` -- the one array that's never passed through
`_regrid_to_target` (it's the regrid *target*, not a regrid source) --
stays lazy into the loop.

Each month's `.sel()` + `.compute()` re-executes the full Dask graph
behind `ref`, which for a local-file source means re-opening and
re-reading every one of the ~25 underlying yearly ERA5 files -- once per
calendar month. 25 files x 12 months = 300 file reads where 25 would do.

This is the exact bug class already fixed in `ocean-post`'s
`gridded_2d_validation.py` (`2b2d2b9`, `f988bec`): a cheap-to-hold lazy
array left lazy through multiple downstream consumers, each one
re-triggering disk I/O independently.

## Confirmed with real timing, not estimated

Per-month log lines (`algorithms.py:218`) carry timestamps. Checked
across three independent real runs, on different variables and models,
run on different days this session:

```
GFDL-ESM4 / evspsbl:        month  1 14:38:43 → month 12 15:01:47   (~2min/month, 12 months)
CNRM-ESM2-1 / evspsbl:      month  1 15:23:14 → month 12 15:46:16   (~2min/month, 12 months)
GFDL-ESM4 / tas (resample test): month 1 06:03:19 → month 10 06:22:18  (~2min/month, consistent)
```

All three: a dead-flat ~2 minutes per month, regardless of variable or
model -- the signature of a fixed-cost operation repeated 12 times, not
work that scales with the data actually being extracted. 12 x ~2min =
~24 min, matching every observed `correct()`-stage runtime this session.

## Fix tried, and why the obvious version failed first

**First attempt** (`ref.load()` once, on the full 25-year lazy array,
before the loop) got stuck -- still running after 14+ minutes with no
progress and had to be killed. This matches a documented, pre-existing
lesson already encoded in this same codebase:
`build_diurnal_climatology`'s own comment (`algorithms.py:484-488`):
*"Loading all years at once ... plus resample/reindex Dask graphs is
very slow and memory-intensive."* `ref` here carries exactly that kind
of graph -- `_apply_scale_offset_resample` builds a `resample(time=
'1D').mean()` step into the lazy array before it ever reaches
`_correct_per_month` (for variables with `resample: daily` set, which
is the case this was tested against) -- and materializing that whole
chain in one `.compute()` call hits the same known Dask resample/reindex
slowness, rather than being a simple bulk file read.

**Working fix**: materialize `ref` once, but **year-by-year** --
exactly the pattern `build_diurnal_climatology` already uses for the
same underlying reason, just not yet applied here. Load each of the 25
calibration years eagerly (`chunks=None`, no Dask at all), apply
`_apply_scale_offset_resample` per year (cheap on a single year, no
graph-scale blowup), concatenate, then run the per-month loop against
the now fully in-memory `ref`.

## Measured result

GFDL-ESM4, historical, `tas`, same test config as the resample note
(`/tmp/bc_test_tas_resample_daily.yaml`, `reference.tas.resample:
daily`), same point/period as every other test this session:

```
Reference materialization (25 years, one at a time): ~4-8s/year, ~150s total
hist/future load + regrid:                            ~15s
Per-month loop (now against in-memory ref):            85.0s  (7.0-7.1s/month, flat)
TOTAL:                                                256.1s  (4.3 min)
```

Baseline (current production code, same config, same data): ~24 min
(1440s). **Speedup: ~5.6x** for the `correct()` stage.

**Correctness check**: diffed the fixed-path output
(`/tmp/tas_materialize_once_test/`) against the earlier, real
per-month-lazy run (`/tmp/tas_resample_daily_test/`, run through the
unmodified CLI) at every grid point, all 5 years. Max abs diff:
9.15e-05 °C -- exactly float32 rounding noise (the storage dtype), not a
real difference. The fix changes nothing about the output, only how
long it takes to produce.

## A second, smaller finding noticed in passing

Even after fixing the `ref` materialization, the per-month loop itself
still costs ~7s/month (85s total) -- not free, because
`_correct_per_month`'s `xr.apply_ufunc(..., vectorize=True)` uses
`numpy.vectorize` under the hood, which loops in pure Python over every
one of the 20,301 spatial grid cells (101 x 201), calling `_qdm_1d`
individually each time. That's a second, independent inefficiency --
not investigated further here, since it's a much smaller fraction of
total runtime than the re-read bug, but worth knowing about if someone
later chases the remaining ~85s.

## Scope and what this does NOT establish

- This was tested on `tas` with `resample: daily` already applied (the
  config from the resample note's A/B test). For a variable still at
  **native hourly** ERA5 resolution -- i.e. `tas`/`pr`/`uas`/`vas`/`ps`/
  `psl`/`huss` in production today, per the resample note -- year-by-year
  materialization would hold ~24x more data resident (one year of hourly
  ERA5 at this domain is still small, ~60-70 MB/year, so almost
  certainly still safe, but not measured here). This is one more reason
  the two fixes (resample-to-daily, and materialize-once) are easiest to
  land together rather than separately.
- Only the `correct()` stage was benchmarked. The `disaggregate` stage
  (`build_diurnal_climatology` + `apply_diurnal_correction`) already
  processes year-by-year deliberately and wasn't touched or re-tested
  here; the documented Sep 2026 OOM incident happened during
  disaggregation, not during `correct()`, and is unrelated to this fix.
- **No production code or config was changed.** This is a benchmark
  result on isolated test output (`/tmp/tas_materialize_once_test/`),
  confirming the fix is both correct and worth landing -- not yet
  implemented in `ocean-prep/lib/bias_correction/algorithms.py` itself.

## If a full core-7 redo happens

This cost is fixed per variable/model/scenario run, independent of
output-period length (`ref` is always the 25-year calibration window).
A full redo -- up to 3 models x 3 scenarios x 10 variables ~= 90 runs --
would otherwise pay ~24 min of pure re-reading per run, ~36 hours total,
before landing this fix. At the measured ~5.6x, that drops to roughly
6-7 hours of equivalent work for the same 90 runs' `correct()` stage.
