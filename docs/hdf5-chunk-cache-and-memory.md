# HDF5/netCDF chunk cache: how it works, and its role in the scylla OOM (2026-09-17)

## Two separate problems, not one

This investigation started blended together and needs to stay split:

1. **CMIP6-raw runtime step** (`experiments/NSe/CMIP6_raw/run01`): wall-clock
   pace per simulated year jumped ~13.7% crossing the 2015 historical->
   scenario forcing splice, and a further, separate, statistically real
   *ongoing* trend on top of that (+3.27 s/year, p<0.01). Fully documented
   in `docs/chunk-pace-analysis.md`. We rechunked the raw GFDL-ESM4 files
   from contiguous storage to `[1, nlat, nlon]` hoping for improvement,
   particularly past chunk 013/2051 where the fix should have started
   applying -- **it did not measurably help**. This remains unsolved.
2. **Memory blowup crossing a year boundary** (this document): using the
   **bias-corrected** meteo source (`meteo.source: CMIP6`, e.g.
   `experiments/NSe/CMIP6/GFDL-ESM4/ssp126/run01`, which reads one file
   *per variable per calendar year*), memory grows every time the
   simulation crosses into a new year, and this is what prevented runs on
   scylla from going past ~3 years. This is the subject of this note.

Today's rechunk/cache work (item 2's fix, below) does **not** touch item
1 -- different code path, different mechanism, and the CMIP6-raw rechunk
already tried for item 1 is unrelated to chunk *cache* tuning entirely.

## Background: what a chunk cache actually is

### Contiguous vs. chunked HDF5 storage

An HDF5 dataset is stored either:

- **Contiguous**: one flat block on disk, in creation order. A partial
  read just seeks to an offset and reads the requested bytes directly --
  there's no notion of a "chunk" at all, and no chunk cache applies to it.
- **Chunked**: divided into fixed-size blocks (the *chunk shape*, e.g.
  `[240, 101, 201]` -- 240 timesteps x full spatial grid), each
  independently compressible. Reading *any* part of a chunk means HDF5
  reads and decompresses the *whole* chunk into memory, so chunk shape
  controls both compression granularity and how much unrelated data you
  pay to decompress for one small read.

The GFDL-ESM4 CMIP6-raw forcing files (`/data/CMIP6/GFDL-ESM4/{historical,
ssp370}/*.nc`) were **contiguous** until 2026-09-17 (see "Rechunking the
raw CMIP6-raw files" below) -- meaning the entire chunk-cache discussion
in this document did not even apply to them before that date. It has
always applied to the **bias-corrected** disagg archive
(`/data/BiasCorrected/CMIP6/**/meteo/*_disagg_*.nc`), which has been
chunked+compressed (`zlib` level 4, shuffle) since ocean-prep's own
bc-correct pipeline first produced it.

### The raw data chunk cache

For a **chunked** dataset, HDF5 keeps a small in-memory LRU cache of
recently-used, already-decompressed chunks per open dataset -- the "raw
data chunk cache" (RDCC). Without it, every single read into a chunk
you've already touched would re-read and re-decompress that whole chunk
from disk again. Three parameters control it:

| Parameter | Meaning |
|---|---|
| `size` (bytes) | Total memory budget for cached chunk data. |
| `nelems` | Number of **hash-table slots** used to look up whether a chunk is cached -- *not* a count of chunks. Should be prime and roughly 100x the number of chunks you expect to fit in `size`, or unrelated chunks collide on the same slot and evict each other even with room to spare. |
| `preemption` (`w0`, 0.0-1.0) | Eviction policy: `0.0` = pure recency (LRU), `1.0` = pure frequency (evict least-accessed regardless of recency). HDF5's own default is `0.75`. |

### How this passes through netCDF -> HDF5 (confirmed directly, not assumed)

`netCDF4.Variable.get_var_chunk_cache()`/`set_var_chunk_cache()` are not
a Python-level abstraction sitting on top of something else -- they are a
direct pass-through to HDF5's own per-dataset cache. Concretely: netCDF-C's
`nc_get_var_chunk_cache`/`nc_set_var_chunk_cache` call HDF5's
`H5Pget_chunk_cache`/`H5Pset_chunk_cache` on the **dataset access
property list (DAPL)** used to open that specific variable
(`H5Dget_access_plist()` under the hood). `h5py`'s equivalent
(`dset._id.get_access_plist().get_chunk_cache()`) reads the exact same
underlying property through a different binding -- both agreed on the
same measured value (64MB) when cross-checked directly.

Two distinct scopes exist, and conflating them causes real bugs:

- **Per-variable** (`Variable.set_var_chunk_cache(size, nelems, w0)`):
  overrides the cache for one already-open variable handle. Must be
  called *before* that variable's data is read for the first time --
  HDF5 fixes the cache size into the DAPL at the moment the dataset is
  opened; you cannot resize it later without closing and reopening.
- **Process-wide default** (`netCDF4.set_chunk_cache(size, nelems, w0)`
  / `get_chunk_cache()`, wrapping `nc_set_chunk_cache`/`nc_get_chunk_cache`):
  the default applied to **every** variable, in **every** file, opened
  anywhere in the process from that point on, until changed again. This
  is *too broad* on its own for "shrink meteo reads, leave simulation
  output alone" -- a GETM run reads forcing and writes history/restart
  output in the same process, so a naive global change would leak into
  the model's own output files too.

**The bracket technique** (confirmed directly, 2026-09-17) resolves this:
shrink the global default, open the file, restore the global default
immediately, in a `try`/`finally`. Because HDF5 captures the cache size
once, per file handle, at open time:

- every variable in that one file (main var *and* coordinate vars) gets
  the small cache -- genuinely **file-level**, not one named variable;
- restoring the global default immediately afterward does **not** leak
  back into that already-open file;
- a second file opened *after* the restore gets the original,
  untouched default;
- the `finally` means a failed open can't leave the small size in effect
  for every later file the process touches.

This is what `driver/scripts/meteo.py`'s `_open_with_small_cache()` /
`_raw_var()` now does for CMIP6-raw meteo reads (commits `0b8eb88`,
`5f9d4b4`). It was chosen over `engine="h5netcdf"` + `driver_kwds`
(xarray's own kwarg for this, confirmed to work) because `h5netcdf` isn't
installed in the `pygetm` conda env that actually runs simulations --
the bracket needs nothing beyond `netCDF4` + `xarray`, both already
present.

## How this connects to the memory leak

`pygetm.input`'s module-level file cache (`open_nc_files` in
`pygetm/input/__init__.py`) **never evicts anything** -- `_open()` just
appends every opened dataset and returns cached entries forever, for the
life of the process. This is already flagged in this repo's own code:
`expand_year_glob`'s docstring says outright that a bare year-wildcard
glob "opens (and permanently caches -- see `pygetm.input.open_nc_files`,
which **has no eviction**) every matching file up front."

Whether that matters depends entirely on **how many files get opened**,
which differs completely between the two meteo sources:

| Source | Function | Files opened | Grows with run length? |
|---|---|---|---|
| `CMIP6-raw` (raw CMIP6, `experiments/NSe/CMIP6_raw/run01`) | `_raw_var()` | At most 2 whole-period files per variable (historical + scenario), once, at setup | **No** -- fixed, bounded |
| `CMIP6` (bias-corrected, `experiments/NSe/CMIP6/GFDL-ESM4/ssp126/run01`) | `_spliced_paths()` | One file **per variable per calendar year** in the run's `[start, stop)` span (`expand_year_glob` bounds it to just the needed years, not the whole ~90-year archive -- an existing, smart partial mitigation) | **Yes** -- linearly, in the number of years the run spans |

Only the second one has the "crossing a year boundary" growth pattern
scylla actually hit. `_raw_var()` was fixed today (file-level cache
shrink); `_spliced_paths()` was **not** -- it's the branch actually
exposed to this risk, and remains unfixed as of this writing.

Critically -- confirmed by direct test, not assumed -- **the cost is not
paid when the file is opened**. `pygetm.input.from_nc()` opens every file
for every year immediately, at setup (RSS barely moves: ~267MB whether
5 years x 8 variables are open or a single one is). The cost is paid
**lazily, the first time data is actually read from that specific file**
-- and because nothing is ever closed, each new year's first-read cost
stacks permanently on top of every previous year's, for the rest of the
process's life.

## Chunk size matters as much as cache size

The bias-corrected archive's actual on-disk chunk shape right now is
`[240, 101, 201]` float64 = **~39MB per chunk**. This matters for two
reasons found directly in testing:

- A cache *smaller* than one chunk (e.g. the 1MB fix value) still works
  correctly (HDF5 just can't fully cache any chunk), but gets zero reuse
  benefit -- every access re-decompresses the same chunk from scratch.
  Fine for the OOM problem, bad for read performance.
- A "right-sized" cache (tested at 80MB, ~2x chunk size) makes the
  memory problem measurably **worse**, not better -- see results below
  -- because it lets each of the never-closed per-year files hoard
  proportionally more before hitting eviction.

This means the earlier idea of extending the meteo cache size in config
and the open question of what chunk size to use when the BiasCorrected
archive eventually gets rewritten are the **same** decision, not two
independent ones: shrinking the archive's own chunk size (the same
`[1, nlat, nlon]`-style rechunk already applied to the raw GFDL-ESM4
files) would let a small cache hold several *whole* chunks instead of a
fraction of one -- low memory *and* real cache reuse, instead of being
forced to trade one for the other as the two knobs stand today.

## Test methodology

Real data throughout: GFDL-ESM4/ssp126 bias-corrected disagg archive,
current on-disk state (float64, `[240, 101, 201]` chunks, zlib-4 +
shuffle) -- not yet touched by any rechunk work, so this reflects what a
real run against this archive experiences right now. 8 variables per
year (`tas`, `huss`, `uas`, `vas`, `psl`, `pr`, `net_sw`, `net_lw`),
matching `_spliced_paths()`'s real per-year read set. Real
`multiprocessing`, one process per simulated MPI rank, so each pays its
own memory cost independently -- matching `pygetm.input.InputManager`'s
confirmed per-rank-independent-reads behaviour (no MPI collectives).

Two rounds:

1. **Hand-rolled** `netCDF4.Dataset` open + read, incrementally (open
   year N, read from it, move to year N+1), across three cache configs.
   Good first signal, but conflates "open" with "read" since both
   happened in the same step.
2. **Corrected**, using `pygetm.input.from_nc()` directly (the actual
   call `_spliced_paths()` makes) -- opens **all** years for **all**
   variables in one call per variable (matching real setup-time
   behaviour), measures RSS with zero reads done, *then* reads forward
   year by year using plain scalar `isel(time=idx)` on the already-open
   multi-year array.

   A first attempt at round 2 used `.sel(time=slice("2015-01-01",
   "2015-12-31"))` to select each year -- this was a dead end worth
   recording: that call alone took 8.1s and jumped RSS by ~1.4GB for a
   *single* variable, *before* any `.isel()`/`.values()` was even
   called -- it eagerly materializes the entire selected year rather
   than staying lazy. With 8 variables x 5 years x N workers this
   explained an early run taking 7+ minutes and ballooning memory
   before being killed. It's also simply not how GETM reads data in
   practice (`TemporalInterpolation` does index-based lookups, not date
   slicing), so switching to scalar `isel()` on the full multi-year
   array is both the fix and the more faithful test.

## Results

### Round 1 -- hand-rolled open+read, 3 cache configs, 30 workers, 5 years

| Year | Default (HDF5 library default) | Small 1MB (the fix) | "Right-sized" 80MB (~2x chunk) |
|---|---|---|---|
| 2015 | 404 MB | 90 MB | 671 MB |
| 2016 | 673 MB | 96 MB | 1269 MB |
| 2017 | 1037 MB | 110 MB | 1869 MB |
| 2018 | 1335 MB | 117 MB | 2470 MB |
| 2019 | 1632 MB | 123 MB | 3071 MB |
| **/year growth** | **~307 MB** | **~8 MB** | **~600 MB** |
| **Aggregate @ 30 workers, year 2019** | **47.8 GB** | **3.6 GB** | **90.0 GB** |

### Round 2 -- corrected, via `pygetm.input.from_nc()`, scalar isel, 30 workers, 5 years

| | Right after opening all 5 years (**zero reads**) | 2015 | 2016 | 2017 | 2018 | 2019 | /year growth |
|---|---|---|---|---|---|---|---|
| Default cache | 267 MB | 631 MB | 928 MB | 1226 MB | 1523 MB | 1820 MB | ~297 MB |
| Small 1MB (the fix) | 266 MB | 315 MB | 317 MB | 325 MB | 325 MB | 325 MB | ~2-8 MB |

The near-identical "zero reads" figure (267 vs. 266MB) across both cache
configs is the direct proof that the cache is not allocated at open
time -- the two configs are indistinguishable until the first read of
each file, then diverge sharply.

### Single-variable diagnostic (isolating exactly where the cost lands)

One variable (`tas`), 5 years, default cache, plain scalar `isel()`:

| Access | RSS | Delta |
|---|---|---|
| `from_nc()` (opens all 5 years) | 232 MB | -- |
| `isel(time=0)` -- 1st read, year 2015 | 269 MB | +37 MB |
| `isel(time=8759)` -- still year 2015 | 283 MB | +14 MB |
| `isel(time=8760)` -- 1st read, year 2016 (**new file**) | 320 MB | **+37 MB** |
| `isel(time=17519)` -- still year 2016 | 306 MB | -13 MB (eviction) |
| `isel(time=17520)` -- 1st read, year 2017 (**new file**) | 344 MB | **+37 MB** |
| `isel(time=43799)` -- year 2019 | 381 MB | +37 MB |

The ~37MB jump lands exactly on the first read of each newly-touched
file, never on files already read from. `~37MB x 8 variables ≈ 296MB`,
matching the ~297MB/year figure in the full 8-variable test almost
exactly.

### Extrapolated to the real 184-rank job

| | Default cache | Small 1MB (the fix) |
|---|---|---|
| Year 1 | ~113 GB | ~57 GB |
| Year 3 | ~226 GB | ~58 GB |
| Year 5 | ~335 GB | ~58 GB |

Default-cache growth alone very plausibly explains an OOM around year 3,
well before counting any of GETM's own model-state memory. The fix
converts an unbounded-with-run-length growth curve into an essentially
flat one.

## Status / what's left

- **Fixed**: `_raw_var()` (CMIP6-raw meteo reads) -- file-level cache
  shrink via the bracket technique. Commits `0b8eb88`, `5f9d4b4`.
- **Not fixed**: `_spliced_paths()` (bias-corrected `CMIP6` meteo reads)
  -- this is the branch actually exposed to the year-crossing growth
  documented here. Needs the same bracket treatment.
- **Open question**: whether to also make the meteo chunk-cache size a
  YAML config knob rather than a hardcoded constant, once applied to
  `_spliced_paths()` too.
- **Open question, deliberately deferred**: what chunk shape to use if/
  when the BiasCorrected disagg archive itself gets rewritten -- see
  "Chunk size matters as much as cache size" above; this is now informed
  by real numbers rather than a guess.
- **Unrelated, still unsolved**: the CMIP6-raw runtime step (item 1) --
  see `docs/chunk-pace-analysis.md`.
