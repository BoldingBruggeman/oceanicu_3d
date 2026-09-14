# NSe CMIP6-raw memory investigation

Ongoing investigation into the steady real-time memory decline observed while running `generated_nse_cmip6_raw.py` (30 MPI ranks) on bb-server1.

## Experiment 1: 2010-01-01 → 2016-01-01 (6 years, 30 ranks)

- Started ~2026-09-12 19:35, finished 2026-09-13 15:15:51 (reached `--stop 2016-01-01` normally, no crash).
- Total wall time in main loop: 70559 s (~19.6 h).
- System free memory (`free -m`, sampled every 15 min via `memory.dat`) declined from **18.2 GB → 5.3 GB** over the monitored window (07:34 → 14:02, 6h28m), an average rate of **−2.0 GB/hour**, essentially linear throughout (one flagged anomalous sample early on, see the Memory Depletion Watch artifact for detail).
- Process exited cleanly; free memory recovered to ~49 GB immediately after exit, confirming nothing was permanently lost at the OS level — the decline tracked this one process's own footprint.

Plot: see the published **Memory Depletion Watch** artifact (single continuous `free`-memory series, dMemory/dt chart, raw sample table).

## Experiment 3: in progress — no output at all

Started 2026-09-14 07:37, `--start 2014-01-01 --stop 2016-01-01` (same 2-year/30-rank setup as Experiment 2), this time with **no `output:` section at all** in the generated config (confirmed absent) — the decisive test of the output-writing hypothesis. If output writing is the main driver, this run should show a much flatter decline than Experiment 2's ~2.4 GB/h underlying rate. Sampling (`free`-based, every 15 min) into `memory_run3.dat`.

| sim date | clock | mem free (GB) |
|---|---|---|
| (starting) | 7:37 | 14.6 |
| 2014-02-08 | 7:52 | 14.0 |
| 2014-03-17 | 8:07 | 13.5 |
| 2014-04-23 | 8:22 | 13.0 |
| 2014-05-30 | 8:37 | 12.5 |
| 2014-07-06 | 8:52 | 12.0 |
| 2014-08-12 | 9:07 | 11.6 |
| 2014-09-18 | 9:22 | 11.0 |
| 2014-10-26 | 9:37 | 10.6 |
| 2014-12-02 | 9:52 | 1.1 ⚠ |
| 2015-01-07 | 10:07 | **0.7** ⚠⚠ |

11 samples. Rate held steady at **−1.6 to −2.4 GB/h** across the first eight intervals, then a sudden, severe drop: 10.6 → 1.1 GB in one 15-min interval (**≈ −38 GB/h**), far outside the steady band and NOT at the known historical→ssp126 boundary (that boundary is 2015-01-01; the anomalous drop happened earlier, 2014-10-26→2014-12-02, still mid-historical — unexplained). The *next* interval (2014-12-02→2015-01-07, 1.1→0.7 GB, −1.6 GB/h) DID cross the 2015-01-01 boundary, but only cost the usual small amount — nowhere near explaining the −9.5 GB anomaly in the prior interval. Rate has returned to the normal band after the anomaly; the drop looks like a one-time event, not a change in the underlying leak rate. Free memory is now critically low (0.7 GB) with the process still alive (30 ranks + mpiexec, no crash) — monitoring at 3-5 min intervals until this resolves (recovers, OOMs, or finishes).

## Diagnostic: is the leak in the meteo interpolation path?

**Hypothesis** (user): the leak traces to meteo interpolation specifically — CMIP6-raw meteo is read and re-interpolated onto the model grid every 3 *simulated* hours (the raw data's native resolution), across many variables, so repeated allocation there was suspected.

**Relevant sizing, confirmed directly:**

| | value |
|---|---|
| Simulation-facing meteo variables | 8 (`t2m`, `qa`, `u10`, `v10`, `sp`, `tp`, `swr`, `ql`) |
| Raw CMIP6 files read | 10 (`rsds`/`rsus` → `swr`, `rlds`/`rlus` → `ql` via subtraction) |
| Raw source grid (GFDL-ESM4) | 30 × 40 = 1,200 cells (float32, 4.8 KB/timestep/variable) |
| NSe target grid (after `HorizontalInterpolation`) | 251 × 257 = 64,507 cells (float64, ~500 KB/timestep/variable) |
| Upsampling factor per read | **~54×** |
| New source timestep | every 3 simulated hours |

**Test:** built the real `pygetm.input.horizontal_interpolation` + `temporal_interpolation` pipeline for all 10 raw files against the actual NSe target grid (same code path as production, via `pygetm.input.from_nc`), then drove it directly with `.update(time)` calls advancing 3 simulated hours per iteration — bypassing the rest of the model (no hydrodynamics, no output writing) to isolate this one code path. Tracked process RSS (`/proc/self/status`, `VmRSS`) every 200 iterations.

**Result — 3,200 iterations (32,000 total `.update()` calls across the 10 files, spanning ~400 simulated days):**

| iter | sim date | RSS (MB) |
|---|---|---|
| 0 | 2015-06-01 | 777.2 |
| 200 | 2015-06-26 | 793.0 |
| 600 | 2015-08-15 | 793.9 |
| 1000 | 2015-10-04 | 794.0 |
| 1600 | 2015-12-18 | 793.2 |
| 2200 | 2016-03-03 | 752.1 |
| 2800 | 2016-05-17 | 793.9 |
| 3200 | 2016-07-06 | 794.0 |

RSS oscillated in a tight **752–799 MB band** with **zero net drift** across the entire run.

**Verdict: the meteo interpolation path, in isolation, does not leak.** This is fairly strong evidence *against* the meteo-interpolation hypothesis — if repeated `HorizontalInterpolation`/`TemporalInterpolation` calls were the cause, this test would have shown it. The leak most likely sits elsewhere in the full simulation (candidates being actively checked: output-file writing/buffering, restart handling, the hydrodynamics/tracer `advance()` step itself, or an interaction between components that this isolated test doesn't reproduce).

## Diagnostic: the 2014→2015 (historical→ssp126) transition

A second, more targeted isolation test, sampling RSS at **every** 3-hour iteration (not every 200) across 2014-12-20 → 2015-01-10, to check what happens exactly when `Concatenate` switches from the historical raw file to the `ssp126` scenario file for all 10 variables.

| iter | sim time | RSS (MB) | Δ |
|---|---|---|---|
| 94 | 2014-12-31 18:00 | 738.75 | +0.00 |
| 95 | 2014-12-31 21:00 | 738.75 | +0.00 |
| **96** | **2015-01-01 00:00** | **794.71** | **+55.96** ← boundary |
| 97 | 2015-01-01 03:00 | 794.71 | +0.00 |
| 98-115 | (into Jan) | 754-804 | oscillating |

**Real, confirmed finding:** there is a genuine **one-time +56 MB step** exactly at the historical→ssp126 boundary, across all 10 files together. After the step, RSS settles into a new plateau (~754-804 MB, up from ~736-739 MB before) and oscillates normally rather than continuing to climb — i.e., a one-time cost, not an ongoing leak. Explanation: once the `Concatenate` source first touches the `ssp126` file, both the historical *and* scenario file handles/metadata stay open simultaneously (previously only the historical file was touched), which costs a fixed ~56 MB across all 10 variables combined.

**This does not explain the sustained multi-GB/hour decline** — 56 MB is far too small, and it only happens once per run (there's only one historical→scenario crossing). But it's a real, now-explained anomaly, and confirms the earlier flagged sample-6 anomaly in the phase-1 `free`-memory data (2014-12-18 → 2014-01-02 date reversal) was likely coincidental timing with this same real boundary-crossing effect, not a data artifact.

## Diagnostic: output writing (netCDF4/HDF5)

**Python-level check** (`pygetm.output.__init__`, `netcdf.py`): `_field2nc` (dict) and `_varying_fields` (list) are populated once at file creation (`start_now`), not growing per write. `save_now` writes into pre-existing `netCDF4.Variable` slices and calls `.sync()` per `sync_interval` (the production config uses `sync_interval: 1` everywhere, so every write is flushed immediately — no unflushed-buffer accumulation at that level). Nothing alarming in the Python object graph.

**But**: every write does `self.nctime[self.itime] = ...` — writing into an **unlimited time dimension** at an ever-increasing index. HDF5's own internal chunk/metadata cache for growing unlimited dimensions is a well-documented, C-level (invisible to Python object inspection) source of memory growth, untested by the read-only meteo reproduction above.

**Test:** standalone netCDF4 file, unlimited time dimension, 4 variables (2× 3D `(time,40,60,60)`, 2× 2D `(time,60,60)` — smaller grid than the real 251×257×40 domain, deliberately, to isolate the *mechanism* cheaply), `sync()` every write (matching production `sync_interval: 1`), 10,000 sequential writes.

| iter | RSS (MB) | file size (MB) |
|---|---|---|
| 0 | 50.8 | 1.2 |
| 2000 | 118.7 | 2363 |
| 4500 | 122.5 | 5316 |
| 7000 | 126.0 | 8269 |
| 8500 | 128.2 | 10040 |
| 9000 | 128.2 | 10631 |
| 9999 | 128.2 | 11811 |

**Result:** RSS grows monotonically from 50.8→128.2 MB over the first ~8500 writes, then **plateaus exactly flat** for the remaining 1500 — a real, confirmed, saturating HDF5-cache ramp-up, not an unbounded leak, at least at this scale.

**Interpretation:** unlike the meteo path (flat oscillation from the start, no ramp at all), output writing shows genuine, monotonic growth — a real and distinct mechanism. Whether it's the *whole* explanation depends on where the ceiling sits at production scale: the real domain fields are ~18× larger per 3D variable (251×257×40 vs this test's 60×60×40) and the real run writes many more variables across several files (`nse_2d.nc`, `nse_3d.nc`, `surf_bott_daily.nc`, boundary/restart files) over far more total timesteps in a multi-year run. If the ceiling scales with variable size × total chunk count, production could plausibly have a ceiling in the multi-GB range — meaning the observed decline in Experiment 1 may just be the **ramp-up portion** of a much higher plateau that a partial (or even a full 6-year) run never reaches. This is currently the strongest, most concrete lead — output writing remains the top suspect.

**Not yet done:** rerun this isolation test at the *real* production field size (251×257×40, matching variable count and files) to see where the ceiling actually sits, and whether it's large enough to plausibly account for the multi-GB/hour decline.

## Experiment 2: complete

Started 2026-09-13 18:16, `--start 2014-01-01 --stop 2016-01-01` (2 years, 30 ranks, two debug options disabled). Sampling (`free`-based, every 15 min) into `memory_run2.dat`.

| sim date | clock | mem free (GB) |
|---|---|---|
| 2014-01-01 | 18:17 | 21.4 |
| 2014-02-06 | 18:32 | 20.3 |
| 2014-03-13 | 18:47 | 19.5 |
| 2014-04-17 | 19:02 | 18.8 |
| 2014-05-22 | 19:17 | 18.1 |
| 2014-06-26 | 19:32 | 17.4 |
| 2014-07-31 | 19:47 | 16.6 |
| 2014-09-04 | 20:02 | 15.7 |
| 2014-10-10 | 20:17 | 14.9 |
| 2014-11-14 | 20:32 | 14.3 |
| 2014-12-19 | 20:47 | 13.6 |
| 2015-01-19 | 21:02 | 11.5 ⚠ |
| 2015-02-16 | 21:17 | 11.1 |
| 2015-03-16 | 21:32 | 10.5 |
| 2015-04-13 | 21:47 | 10.1 |
| 2015-05-11 | 22:02 | 9.7 |
| 2015-06-08 | 22:17 | 9.3 |
| 2015-07-06 | 22:32 | 8.8 |
| 2015-08-03 | 22:47 | 8.4 |
| 2015-08-31 | 23:02 | 8.1 |
| 2015-09-28 | 23:17 | 7.7 |
| 2015-10-25 | 23:32 | 7.2 |
| 2015-11-23 | 23:47 | 6.7 |
| 2015-12-21 | 00:02 (+1d) | 6.2 |

**Run finished**: reached `2016-01-01 00:00:00`, `Time spent in main loop: 21080.035 s` (~5.86 h total wall time — vs. Experiment 1's 19.6 h for a 6-year span, consistent with covering only 2 years). No crash; free memory recovered to ~42 GB immediately after exit.

### Final analysis

Total decline across the run: 21.4 → 6.2 GB (**15.2 GB** over 5h45m real time, 18:17 → 00:02+1d).

Splitting out the one-time historical→ssp126 boundary step (2014-12-19 20:47 → 2015-01-19 21:02, a real, distinct +2.1 GB event — much bigger in the full simulation than the isolated meteo-only test's +56 MB, since output writing/hydrodynamics/other state also react to the transition) from the underlying continuous decline:

| segment | span | Δmem | real time | rate |
|---|---|---|---|---|
| pre-boundary (historical files) | 18:17 → 20:47 | 21.4 → 13.6 GB | 2.50 h | **−3.12 GB/h** |
| *(boundary step)* | 20:47 → 21:02 | 13.6 → 11.5 GB | 0.25 h | *(one-time, ≈ −2.1 GB)* |
| post-boundary (ssp126 files) | 21:17 → 00:02+1d | 11.1 → 6.2 GB | 2.75 h | **−1.78 GB/h** |

(12.7 GB from the two segments + 2.1 GB one-time step ≈ 14.8 GB, close to the observed 15.2 GB total, confirming this breakdown accounts for essentially all of the decline.)

### Comparison to Experiment 1 (debug options on, ~−2.0 GB/h)

Experiment 2's underlying rate (excluding the one-time boundary step) averages to about **−2.4 GB/h** (blending the −3.12 pre- and −1.78 post-boundary segments) — **not materially lower** than Experiment 1's −2.0 GB/h, despite the two debug options being disabled. If anything, the pre-boundary (historical-file) segment ran *faster* than Experiment 1. **Conclusion: disabling these two debug options does not appear to meaningfully reduce the leak.** The rate genuinely varies between the historical and scenario phases of the run (−3.1 vs. −1.8 GB/h) independent of the debug setting — a real, separate observation worth keeping in mind when comparing future runs, since "which phase of the run was sampled" matters as much as "which options were set."

Also notable: Experiment 1's *monitored* window stopped at 5.3 GB free with ~13 more hours of simulation left to run, yet it finished successfully. Experiment 2, monitored end-to-end, finished with a healthy 6.2 GB cushion, and its rate visibly slowed in the back half (post-boundary) rather than accelerating. Combined with the output-writing diagnostic's plateauing behavior, this is consistent with the leak being real and substantial but *not* runaway/unbounded at these run lengths — it likely continues to grow but at a decreasing rate, which is reassuring for run stability even though the underlying cause (most likely output writing, per the diagnostics above) remains worth fixing for longer runs or lower-memory machines. Rate since the boundary holding steady around −1.6 to −2.4 GB/h — consistently at or below Experiment 1's −2.0 GB/h. The interval crossing the historical→ssp126 boundary showed a jump: 13.6 → 11.5 GB in 15 minutes (≈ −8.4 GB/h). **Confirmed as a one-time step, not a rate change**: the very next interval settled to just −1.6 GB/h — *lower* than the pre-boundary steady band (−2.8 to −3.6 GB/h) and lower than Experiment 1's rate. This is a clean, decisive result: crossing the historical→ssp126 file boundary costs a real, one-time memory hit in the full simulation (bigger than the ~56 MB seen in the isolated meteo-only test, likely because output writing / hydrodynamics / other state also reacts to the transition), after which the underlying decline resumes at its normal rate — it does not accelerate the ongoing leak.
