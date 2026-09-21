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

## 2026-09-21: production OOM on NSe/CMIP6/GFDL-ESM4/ssp126/run01 (bias-corrected)

**Trigger:** chunk 0 of `NSe/CMIP6/GFDL-ESM4/ssp126/run01` (bias-corrected meteo, not raw), a single **10-year** chunk (`chunk_multiplier: 10`, 2010-01-02 → 2020-01-02, 183 ranks, SLURM job 11720, submitted 2026-09-20T18:14:46), was killed mid-run on 5 specific ranks across 2 nodes:

```
srun: error: scyllacpu20: tasks 62,78: Killed
srun: error: scyllacpu21: tasks 130,149,154: Killed
```

Plain `Killed` (no traceback, no application error) on a handful of ranks out of 183, not the whole step — the classic Linux OOM-killer signature (node ran low on memory, kernel picked specific victim processes by `oom_score`), not a SLURM-enforced uniform step memory limit. The registry chunk row was left `status: running`, `exit_code: None` — the process died too abruptly to report back.

This is notable because the **raw** CMIP6 run for the same model/scenario (`NSe/CMIP6_raw/GFDL-ESM4/ssp126/run01`) ran the *entire* 2010–2099 period to completion across 30 chunks, every one `exit_code: 0`, with **no OOM at any point** — but using `chunk_multiplier: 3` (3-year chunks) rather than 10. That's a real confound worth separating: chunk *length* (single process lifetime), not just meteo source (raw vs. bias-corrected), differs by >3× between the run that never had a problem and the run that OOM'd.

Prior to this, on 2026-09-21, `ocean-prep`'s bias-correction storage encoding was fixed (commit `0bdd332`) to unconditionally write float32/contiguous/uncompressed — the fix the user had traced the earlier chunking/compression HDF5-cache concerns to. A full audit of the entire `BiasCorrected` archive (11 model/scenario/period combinations, disagg files only — the ones actually read in production) found **0/7145 non-conforming**: every disagg file, including all of GFDL-ESM4/ssp126, was already contiguous/uncompressed/float32 (rewritten 2026-09-17) *before* this OOM occurred. So the OOM happened against already-fixed input data — the two redo tests below were run specifically to check whether the meteo-reading path is really clean now, and whether some other mechanism explains this OOM.

## 2026-09-21: meteo-reading redo, bias-corrected GFDL-ESM4/ssp126 (post storage-encoding fix)

Redo of the "is the leak in the meteo interpolation path?" test above, this time against the **bias-corrected** archive instead of raw CMIP6, using the real, now-fixed (contiguous/uncompressed/float32) GFDL-ESM4/ssp126 disagg files, and matching `NSe/CMIP6/GFDL-ESM4/ssp126/run01`'s actual production config exactly rather than a stand-in:

| | value |
|---|---|
| Simulation-facing meteo variables | 8 (`t2m`←`tas`, `qa`←`huss`, `u10`←`uas`, `v10`←`vas`, `sp`←`psl`, `tp`←`pr`, `swr`←`net_sw`, `ql`←`net_lw`) — `radiation_source: net`, confirmed from the real generated config, not `components`/`pseudo_tcc` |
| Target grid | real NSe grid, 251×257, built from the real `bathymetry_nse.nc` via `pygetm.domain.create_spherical` (not a synthetic stand-in) |
| Historical/scenario splice | historical (2010-2014) → ssp126 (2015-2020), matching `driver/scripts/meteo.py`'s `_spliced_paths`/`HIST_CUTOFF_YEAR=2014` exactly |
| Native cadence | 3-hourly (disagg resolution), same `pygetm.input.from_nc` + `horizontal_interpolation` + `temporal_interpolation` pipeline as production |
| Period driven | 2010-01-02 → 2020-12-31 (matches production chunk 0's own 10-year span), ~32,000 `.update()` iterations, 10 year-boundary crossings including the historical→ssp126 splice |

**Result:** RSS held flat at **+1.0 MB delta** across *every single one* of the 10 year-boundary crossings (a new per-year disagg file first accessed each time), including the historical→ssp126 splice itself. No drift anywhere across the full 10-year run.

Separately, reading one real production file directly (`tas_bc_bilinear_ssp126_disagg_2016.nc`, 711 MB, float32, confirmed contiguous/uncompressed) showed opening it costs +2.0 MB (metadata only) and reading a single timestep costs +1.1 MB (essentially just the timestep's own data) — confirming there's no per-file HDF5 chunk-cache allocation left to pay now that storage is contiguous.

Also confirmed directly from `pygetm/input/__init__.py`'s `TemporalInterpolation`: the per-timestep output buffer (`self._current`) is allocated exactly once in `__init__` and every `.update()` writes into it **in place** — never a fresh allocation per call. The raw bracketing-timestep read (`self._next`) genuinely is reassigned on each native-timestep advance, but at a constant size every time, which a normal allocator recycles without growing RSS.

**Verdict: the meteo-reading path is clean, confirmed at full 10-year production scale against the real fixed archive.** The 2026-09-21 storage-encoding fix was correct and necessary (removes a real per-file-per-rank HDF5 chunk-cache cost that no longer exists), but this OOM is not coming from here — same conclusion as the original raw-CMIP6 test above, now confirmed for the bias-corrected path too.

## 2026-09-21: output-writing redo, real production field size (the "not yet done" item above, completed)

Redo of the "output writing (netCDF4/HDF5)" diagnostic above, this time at the real 251×257×40 domain size and the real `NSe/CMIP6/GFDL-ESM4/ssp126/run01` output configuration (read directly from `generated_nse_cmip6.py`'s `configure_output()`), rather than the smaller 60×60×40 stand-in:

| file | vars | shape | cadence | averaging |
|---|---|---|---|---|
| `nse_2d.nc` | 5 (`Ht`,`zt`,`Dt`,`u1`,`v1`) | (251,257) | daily | instantaneous |
| `nse_3d.nc` | 9 (`temp`,`salt`,`rho`,`nuh`,`uk`,`vk`,`ww`,`SS`,`num`) | (40,251,257) | monthly | `time_average=True` |
| `surf_bott_daily.nc` | 4 (`temp_surf`,`salt_surf`,`temp_bott`,`salt_bott`) | (251,257) | daily | instantaneous |

All float32, `NETCDF4`, unlimited time dimension, `sync_interval=1` (matching production exactly). Files opened once, held open, written to, and `sync()`'d for the entire 10-year run — never closed until the very end, matching production's real per-chunk lifetime.

| day | writes (2d/surf-bott) | writes (3d) | RSS (MB) | Δ from baseline |
|---|---|---|---|---|
| 0 | 1 | 1 | 153.6 | +0.0 |
| 500 | 501 | 17 | 1192.2 | +1038.7 |
| 1000–3600 | up to 3601 | up to 121 | 1192.2–1202.1 | +1038.7 to +1048.6 (essentially flat) |
| 3650 (end, files closed) | 3650 | 122 | 1161.2 | +1007.6 |

**Result:** ramps fast for the first ~500 days (~1.4 years), then stays essentially flat for the remaining ~8.6 years — same ramp-then-plateau shape as the smaller-scale test above, now confirmed at real production scale: **a bounded ~1.0-1.05 GB per process**, not unbounded growth. The small drop at the very end happens exactly at `nc.close()`.

**Root cause, confirmed by inspecting the actual on-disk chunk shapes:** netCDF4 auto-selected `chunking = [1, ...]` for every variable in all three files — i.e. the chunk size along the unlimited time dimension is exactly **one timestep**. Every write therefore creates a brand-new, never-revisited chunk (2D: ~258 KB/chunk; 3D: ~10.3 MB/chunk). Because these files are opened once and never closed for the run's whole duration, HDF5 has to keep growing its **chunk index** (the B-tree/metadata structure recording where every chunk lives in the file) in memory for as long as the file stays open — this is what ramps up and then plateaus (once HDF5's metadata cache reaches whatever size it auto-grew to for that many chunks), and what gets released only at `nc.close()`. The small, fixed-size *raw data* chunk cache (for re-reading/re-writing the same chunk) isn't implicated here — with chunk shape 1 along time, nothing is ever revisited, so that cache stays small regardless.

**Connecting to the OOM — corrected:** initially assumed this ~1 GB applied per-rank (×183 ≈ 190 GB aggregate). That's wrong. Confirmed directly in `pygetm/output/netcdf.py`: `self.is_root = rank == 0`, and the file is only actually opened (`netCDF4.Dataset(path, "w", ...)`) `if self.is_root or self.sub` — every non-root rank gathers its local data to rank 0 and never opens a file handle itself. So this ~1 GB chunk-index cost is carried by **one rank only** (root), not multiplied across the job.

That means this mechanism, while real and now confirmed at production scale, does **not** explain the actual SLURM kill: the ranks killed were 62, 78 (scyllacpu20) and 130, 149, 154 (scyllacpu21) — none of them rank 0, spread across two different nodes. A cost that only rank 0 carries can't be what pushed five non-root ranks on two other nodes over the edge. Output writing is still worth the `chunksizes` fix below (it's a real, avoidable cost on whichever node holds rank 0), but it is **not** the explanation for this specific OOM. Since the meteo-reading path is also confirmed clean (identical for every rank, each reading only its own local subdomain), the actual cause of the rank-62/78/130/149/154 kill remains open.

Domain-decomposition imbalance was considered and ruled out by design: subdomains are all the same size, so per-rank memory footprint from the model's own state should be essentially identical across all 183 ranks. That's actually the more puzzling part of this incident: if every rank's own memory use is nominally the same, why would only 2 of ~60-odd ranks on one node and 3 of ~60-odd on another get killed, rather than none or all of a node's ranks together? The likely explanation shifts from *code* to *environment*: these two specific nodes (scyllacpu20, scyllacpu21) plausibly had less actual free memory available than other nodes in the allocation at that moment — e.g. a shared/non-exclusive allocation with other jobs already resident, or some other node-level difference — and the kernel's OOM killer, faced with genuinely near-identical candidate processes, picked whichever few crossed its threshold check first/had the least favorable `oom_score` at that instant, which with uniform per-rank memory doesn't have to be a deterministic subset. This needs someone with real HPC shell access to check (`sacct`/`sstat` for this job's actual per-rank peak memory, `scontrol show node scyllacpu20 scyllacpu21` for exclusivity/other jobs at that time) — not diagnosable further from bb-server1/orca alone.

**Not yet done:** confirm the fix — setting an explicit, larger `chunksizes` along the time dimension (e.g. ~30 timesteps, a month's worth) on `add_netcdf_file`/the underlying `createVariable` calls should cut the number of distinct chunks (and therefore the chunk-index memory) by a proportional factor, without changing what's stored on disk. Rerun this same test with that change to confirm the ceiling actually drops as predicted before touching the real production code path.
