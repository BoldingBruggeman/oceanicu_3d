# NSe/CMIP6_raw run01: chunk pace analysis (2026-09-16)

## What this is

`bb-server1:/data/OceanICU/oceanicu_3d/experiments/NSe/CMIP6_raw/run01/`
runs one 3-year chunk per subdirectory (`000_20100101_20130101`,
`001_20130101_20160101`, ...). Each chunk's `getm-0000.log` reports the
simulated date periodically, with a wall-clock timestamp on every line --
enough to measure real wall-clock pace per simulated year and compare
chunks against each other.

**These logs are rsynced down from the HPC, not written locally**, so an
early pass of this analysis (chunks 000-002 only) carried an explicit
caveat about drawing conclusions past chunk 002. Re-run
(`running/analyze_chunk_pace.py`, see below) whenever more has synced.

## Findings (chunks 000-013, 2010-2050)

Baseline = first 5 complete simulated years, fixed in the script itself
(not a CLI flag -- there's only ever one right answer for this
experiment: its own historical-forced period, 2010-2014; see "Re-running
this analysis" for why this has to be a fixed year-count, not a calendar
cutoff). Every other year is compared against that fixed baseline mean:

| Year | Chunk | Wall time | vs. baseline mean |
|---|---|---|---|
| 2010 | 000 | 37.73 min | -0.9% |
| 2011 | 000 | 38.43 min | +0.9% |
| 2012 | 000 | 38.58 min | +1.3% |
| 2013 | 001 | 37.02 min | -2.8% |
| 2014 | 001 | 38.62 min | +1.4% |
| 2015 | 001 | 43.25 min | +13.6% |
| 2016 | 002 | 42.33 min | +11.2% |
| 2017 | 002 | 43.22 min | +13.5% |
| 2018 | 002 | 42.98 min | +12.9% |
| 2019 | 003 | 41.80 min | +9.8% |
| 2020 | 003 | 42.40 min | +11.4% |
| 2021 | 003 | 43.72 min | +14.8% |
| 2022 | 004 | 40.87 min | +7.3% |
| 2023 | 004 | 41.55 min | +9.1% |
| 2024 | 004 | 41.78 min | +9.7% |
| 2025 | 005 | 42.60 min | +11.9% |
| 2026 | 005 | 43.10 min | +13.2% |
| 2027 | 005 | 42.62 min | +11.9% |
| 2028 | 006 | 42.50 min | +11.6% |
| 2029 | 006 | 42.55 min | +11.7% |
| 2030 | 006 | 43.00 min | +12.9% |
| 2031 | 007 | 41.97 min | +10.2% |
| 2032 | 007 | 42.57 min | +11.8% |
| 2033 | 007 | 42.77 min | +12.3% |
| 2034 | 008 | 42.38 min | +11.3% |
| 2035 | 008 | 42.35 min | +11.2% |
| 2036 | 008 | 42.03 min | +10.4% |
| 2037 | 009 | 42.43 min | +11.4% |
| 2038 | 009 | 42.90 min | +12.7% |
| 2039 | 009 | 42.85 min | +12.5% |
| 2040 | 010 | 42.75 min | +12.3% |
| 2041 | 010 | 43.63 min | +14.6% |
| 2042 | 010 | 43.42 min | +14.0% |
| 2043 | 011 | 42.60 min | +11.9% |
| 2044 | 011 | 43.17 min | +13.4% |
| 2045 | 011 | 42.72 min | +12.2% |
| 2046 | 012 | 41.47 min | +8.9% |
| 2047 | 012 | 42.50 min | +11.6% |
| 2048 | 012 | 43.23 min | +13.5% |
| 2049 | 013 | 41.82 min | +9.8% |
| 2050 | 013 | 42.70 min | +12.1% |

- **Baseline** (2010-2014, n=5): mean 2284.6 s (38.08 min), every year
  within ±3% of it.
- **Everything since** (2015-2050, n=36): mean 2554.2 s (42.57 min) --
  **+11.8% vs. the baseline mean** -- every individual year within ±3
  points of that +11.8%, no trend up or down across 30+ years.

2015-01-01 is exactly the historical -> SSP-scenario forcing boundary for
this CMIP6-raw setup (`meteo.source: CMIP6-raw`, model `GFDL-ESM4`,
scenario `ssp126` -- see `driver/scripts/meteo.py`'s historical/scenario
splicing, one whole-period file per variable per experiment, read
straight from `/data/CMIP6/{model}/{scenario}/` on bb-server1, no bias
correction). Same boundary marked on the raw-CMIP6 decadal plots on the
ocean-post Hugo site's `/scenarios/` page.

## Root cause, confirmed

Two of the ten forcing files GETM reads every meteo update --
`/data/CMIP6/GFDL-ESM4/ssp126/{uas,vas}_3hr_ssp126_2015-2099_onfly.nc`
(eastward/northward wind) -- had a bad internal chunk shape:
`[49640, 6, 8]`. The spatial domain is 30 lat x 40 lon, so a chunk only
covers a 6x8 tile of it -- reading one full timestep's spatial field
(GETM's own access pattern) meant decompressing 25 separate chunks
(5x5 spatial tiling) instead of 1. Every other file in the whole
GFDL-ESM4 archive -- the historical file, every other scenario
(ssp245/ssp370/ssp585), and every other variable in ssp126 itself -- is
either plain contiguous/uncompressed or chunked with the chunk's spatial
extent matching the *full* domain (e.g. historical's radiation variables,
`[30, 30, 40]`). These two files were the only anomaly in the entire
archive (checked exhaustively, one scan across every model/experiment/
variable present).

Measured impact, single random-timestep full-spatial-slice read
(`v[i, :, :]`, netCDF4-python, bb-server1):

| File | ms/read |
|---|---|
| historical `uas` (contiguous) | 2.4 |
| **ssp126 `uas` (bad chunking, before fix)** | **530.8** |
| historical `rsds` (chunked, full spatial extent) | 6.2 |
| ssp126 `rsds` (contiguous) | 8.5 |

**220x slower**, isolated to exactly the two files that had the bad
chunk shape. That alone is enough to explain the observed ~13% *average*
wall-clock slowdown (the two bad reads are a fraction of ten variables'
worth of I/O per step; the other eight are unaffected or, if anything,
slightly cheaper post-2015).

A second benchmark measured *sequential* single-timestep reads (closer
to GETM's real access pattern, and the case where HDF5's chunk cache
should help most) on the same **pre-fix** files:

| File | ms/read (sequential) |
|---|---|
| historical `uas` (contiguous) | 0.20 |
| **ssp126 `uas` (bad chunking, before fix)** | **477.3** |
| historical `rsds` (chunked, full spatial extent) | 0.23 |
| ssp126 `rsds` (contiguous) | 0.19 |

The well-formed files all got ~10x *faster* under sequential access
versus random (page-cache/prefetch benefit) — but the badly-chunked
`uas` file got essentially **no benefit at all** (477 ms vs 531 ms
random): each read still needs several of the 25 large (~9.5 MB)
spatial-tile chunks decompressed, with negligible reuse across nearby
timesteps. Confirms the severity independent of access pattern; not a
random-access-only artifact.

## Fix applied (2026-09-16)

Backed up both files (`.bak_20260916_123451`) and rewrote them
contiguous/uncompressed -- matching the convention every other file in
the archive already uses for `uas`/`vas` (including this same model's
own historical file, and every other SSP scenario). Verified the
rewritten data is bit-identical to the backup (`numpy.array_equal`), and
re-measured the same random-access read benchmark:

| File | ms/read (after fix) |
|---|---|
| ssp126 `uas` | 6.1 |
| ssp126 `vas` | 8.3 |

Back to the same ballpark as every other file in the archive. File size
grew from ~964 MB to ~1193 MB each (contiguous costs more disk than the
zlib-compressed original, ~230 MB x 2 -- trivial against 812 GB free on
`/data` at the time).

**Not yet confirmed**: whether this actually closes most of the observed
~13% chunk-pace gap in a real run -- chunks 000-002 already ran against
the *old*, badly-chunked files, so their recorded pace reflects the bug.
Re-run `running/analyze_chunk_pace.py` once chunk 003 (or a re-run of an
affected chunk) has synced down with data generated *after* this fix, and
compare.

## A much bigger version of the same bug: the bias-corrected disagg archive

Prompted by the CMIP6-raw finding above: does `/data/BiasCorrected/CMIP6/*/*/meteo/*disagg*`
(the disaggregated, hourly, bias-corrected forcing actually used by production
runs) have the same problem? Yes -- systemically, not as an isolated pair of
files this time.

### Scope

Scanned **all 7,205** disagg files (every model x scenario x variable, 6.4 TB
total). **All 7,205** have a bad spatial chunk shape:

| Variable group | Shape | Chunking | Spatial tiles per full-domain read |
|---|---|---|---|
| `evspsbl` (288 steps) | (288, 101, 201) | `[144, 51, 101]` | 2x2 = 4 |
| everything else, hourly (8760/8784 steps) | (8760, 101, 201) | `[1752, 21, 41]` | 5x5 = **25** -- same severity as the CMIP6-raw bug |

This is the standard output convention of `ocean-prep`'s own bc-correct/
disaggregation pipeline (a different tool from the raw-CMIP6 fetcher, so an
independent instance of the same mistake), not a one-off. It very likely
explains a meaningful share of the memory-leak report's own "boundary-
crossing cost, larger at production grid size, not yet measured" note --
this 101x201 grid is that production grid.

Unexpected upside: the current compressed files are actually **1.37-1.48x
*larger*** than the equivalent uncompressed data (e.g. 968-1055 MB on disk
vs 711 MB raw) -- the mismatched chunk shape makes zlib+shuffle compression
counterproductive.

### Root cause, in `ocean-prep`

`lib/bias_correction/utils.py`'s `_write_output`/`_write_chunk_output` called
`ds.to_netcdf(out_path, encoding={v: {'zlib': True, 'complevel': 4}})` --
**no `chunksizes`**. Without one, xarray bakes whatever Dask chunk shape the
array happens to have at write time directly into the file's on-disk HDF5
chunking. `_SPATIAL_CHUNK = 25` (a 25x25 in-memory processing tile, sized so
`apply_ufunc` only materialises one small block at a time during bias
correction -- a perfectly reasonable *compute* chunk size) was ending up as
the *storage* chunk size too, further reshaped by the disaggregation step's
own daily->hourly repeat into the `[1752, 21, 41]` seen on disk.

**Fix**: added `_storage_encoding()` (explicit `chunksizes`, full spatial
extent, time axis capped at `_STORAGE_TIME_CHUNK = 240`), used by both write
functions -- decouples the in-memory processing chunk shape from the on-disk
storage chunk shape, which is what should have been happening all along.

Chose 240 (not the full time axis, ~8760/8784) deliberately: production
reads one timestep at a time, sequentially, once per simulated hour, to
interpolate and calculate fluxes. A single giant time-chunk would force
decompressing (and caching) the *entire* ~700 MB-1 GB file on its first read,
for every variable read that way concurrently -- a real memory cost, not
just a one-time latency hit. A bounded time-chunk pays the decompression
cost once per chunk, amortised across every sequential read within it
(confirmed by the earlier sequential-vs-random CMIP6-raw benchmark: ~10x
faster for well-shaped chunks under sequential access, no benefit at all for
badly-shaped ones). Full spatial extent is the part that actually matters;
the time chunk only needs to be "not tiny, not the whole file".

### Rewrite of the existing archive: in progress, real disk-contention finding

Rewriting 6.4 TB with no space for a full duplicate backup (812 GB free vs
6.4 TB total) -- same temp-write -> full-verify -> atomic-replace pattern as
the CMIP6-raw fix, no separate persistent backup copy (needs only ~1 file's
headroom at a time, not 2x the archive).

Started with GFDL-ESM4/ssp126 (1105 files, 989 GB) at 16 parallel workers.
**Real finding**: 16-way parallelism hits genuine disk contention on
bb-server1 -- `iostat` showed `sda` at 97% utilisation, queue depth ~5.8,
during the run. Per-file write+verify time for the hourly variables went
from ~70s (single file, no contention) to **~350-400s** under 16-way
contention -- 5-6x slower per file than serial. Net throughput is still
better than serial (16 files every ~360s beats 1 file every ~70s, roughly
3x, not the naively-hoped 16x), but revises the time estimate:

- GFDL-ESM4/ssp126 (1105 files): **~6.5 hours** (was optimistically ~1-2.5h)
- Full archive (7205 files, 6.4 TB): **~1.8 days** (was optimistically ~8-16h;
  serial would have been ~5 days)

### Concurrent-reader benchmark: realistic per-MPI-rank tile sizes

The full-domain single-process benchmark above (530ms -> 7ms, ~75x) uses
GETM's access pattern but not its real *scale* -- with 184 MPI ranks, each
rank reads only its own small local subdomain tile (`pygetm.input`'s
`grid.tiling.subdomain2slices` + a lazy `isel` on the source file --
confirmed no centralised read+broadcast; every rank does its own
independent netCDF read, every simulated hour).

**First attempt at this benchmark was retracted** (2026-09-16): it ran
concurrently with the background rewrite job (confounded by real disk
contention from that job), used a mismatched file pair, and produced a
per-hour cost that, extrapolated, implied more time on meteo I/O alone than
a whole simulated year actually takes end-to-end -- caught directly by the
user. Re-run cleanly below; the numbers in this section supersede that
attempt.

**Clean re-run.** Same variable, same shape, matched pair this time
(`GFDL-ESM4/ssp370/huss`, untouched by the rewrite, vs. the newly-fixed
`GFDL-ESM4/ssp126/huss`, same year). The 16-worker rewrite job was paused
(`SIGSTOP` on all 17 of its processes) and confirmed disk-idle via `iostat`
(2% `sda` utilisation) before running, then resumed afterward -- no
concurrent contention this time. N independent worker processes each read
their **own distinct** ~7x15-cell tile (14x13 grid over the 101x201 domain,
approximating a 184-way rank decomposition) at 10 sequential timesteps:

| N (ranks) | OLD mean / max (ms) | NEW mean / max (ms) |
|---|---|---|
| 16  | 56.2 / 141.6 | 50.3 / **52.8** |
| 64  | 47.2 / 145.2 | 101.3 / 108.2 |
| 184 | 263.6 / 711.9 | **154.9 / 236.0** |

GETM's timestep is gated by the slowest rank (max, not mean), so the max
column is the operationally relevant one. Honest reading, including the
part that doesn't favour the fix:

- **N=16**: NEW wins on both mean and max, with a much tighter spread
  (45.8-52.8ms vs 23.4-141.6ms) -- the expected effect of full-spatial-extent
  chunks avoiding scattered small reads.
- **N=64**: NEW is *worse* on mean (though still slightly ahead on max).
  Cause: NEW's chunk covers the *entire* spatial extent, so all 64 workers
  reading different tiles of the same timestep each independently
  decompress the same ~19.5MB chunk in their own process (the HDF5 chunk
  cache is per-process, not shared) -- CPU-bound decompression contention,
  not disk I/O. OLD's tiny chunks dodge that specific cost and instead pay
  for scattered small reads.
- **N=184** (3x oversubscribed on bb-server1's 64 cores): NEW wins clearly
  on both mean and max -- OLD's many small scattered chunks compound badly
  under real contention, while NEW's fewer/larger reads scale better. Max:
  711.9ms -> 236.0ms, a ~476ms reduction per variable per simulated hour,
  at this rank count and on this hardware.

**Why this still isn't a per-simulated-year saving estimate.** The real
HPC target is **184 cores across 3 nodes on a parallel filesystem**
(confirmed by the user, 2026-09-16) -- categorically different from this
test's single box, one local disk, and 184 processes oversubscribing 64
real cores. A parallel filesystem spreads concurrent reads across many
storage servers/disks and typically has far more aggregate bandwidth and
much better behaviour under many simultaneous readers than one local SATA
disk saturating at 16 writers. So:

- The **relative** old-vs-new comparison at each N, run back-to-back under
  identical (paused-rewrite, idle-disk) conditions, is a solid, trustworthy
  measurement of what this specific chunking bug costs *on this hardware*.
- The **absolute** ms figures, and especially the N=64 CPU-decompression-
  contention effect (an artifact of 64 real cores, not 184), do not
  transfer to the real cluster's 3-node/184-core parallel-filesystem
  environment.
- No per-simulated-year wall-clock saving figure is reported here. Getting
  one would need this same paired old-vs-new comparison run on the actual
  target cluster's own storage, with real MPI ranks across the real 3
  nodes -- not yet done.

## Re-running this analysis

```bash
python running/analyze_chunk_pace.py <local_run01_mirror> [--csv out.csv]
```

The baseline (first 5 chronological complete years) is fixed in the
script itself, not a flag -- there's only one right answer for this
experiment, its own historical-forced period. Deliberately a year
*count*, not a calendar cutoff: the 2015+ slowdown was caused by a
chunking bug in the scenario forcing files, since fixed -- once the run
continues past the fix, new data for calendar years >= 2015 needs to be
compared against the same unchanging baseline to see whether it moves
back toward it, not silently pooled back in with the old, still-buggy
2015+ data under a shared "post-2015" label.

`<local_run01_mirror>` is wherever `run01`'s chunk directories land
locally after rsyncing from bb-server1. The script is resilient to
partial data by design (that's the whole point, since more chunks will
sync in over time):

- A chunk directory with no log yet (or an empty one) is reported as
  "no data yet", not treated as a failure.
- A chunk with checkpoint lines but no final "Time spent in main loop"
  line yet is "in progress" -- its per-year breakdown is still shown, but
  its last (possibly incomplete) year is excluded from the pace-summary
  statistics.
- `.attempt-*` retry directories are only used as a fallback when the
  plain (final) chunk directory has no usable data.

Just re-run it against the same local mirror whenever more data has
synced down -- no need to re-derive any of the above by hand.
