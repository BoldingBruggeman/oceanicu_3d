# NSe boundary condition generation — runbook

Practical how-to. For dataset-level details (CMEMS product IDs, coverage
dates, Baltic override rationale) see `ocean-prep/docs/nse_boundary_sources.md`
— that doc explains *what data*; this one explains *what to run, in what
order, from where*.

## 0. The boundary point file — read this first

**Canonical copy:** `NSe/Bathymetry/nse_bdy_lonlat.txt` (currently 311
points, moved here from `NSe/nse_bdy_lonlat.txt` on 2026-08-17). Its
entire validity is defined by wet/land status against
`NSe/Bathymetry/bathymetry_nse.nc` — re-verify after every bathymetry
regeneration (see below) — and it lives alongside the other grid/mask
artifacts there, not the YAML domain configs in `NSe/config/`.

This is the file the running model actually uses — confirmed empirically by
comparing it against `lon_bdy`/`lat_bdy` in a `getm-dump.nc` crash dump
written by a live run: **exact match, 0 points different**.

**Policy: this file belongs to the setup, not to any shared/generic
location.** It must live under `NSe/Bathymetry/` — never in
`ocean-prep/bdy_coords/`, `boundaries.old/bdy_coords/`, or the
`oceanicu_3d` repo root — and never as a symlink from those locations
either: the point list changes per domain setup, not just NSe, so a
shared symlink target wouldn't generalize.

As of 2026-08-17 (resolving an earlier 2026-08-03 state with four
divergent copies spread across three repos — `NSe/`, the `oceanicu_3d`
repo root, `boundaries` (now `boundaries.old`), and `ocean-prep` — most
configs pointing at the wrong one), every config references this one
real, live copy directly:

- `NSe/config/nse_bdy_create.yaml`, `nse_init_create.yaml`, and
  `nse_tidal_bdy.yaml`. The first two use a real absolute path and were
  moved out of `ocean-prep/config/` into `NSe/config/` on 2026-08-24
  (NSe-specific configs belong with the NSe setup, not the generic
  `ocean-prep` tool repo); `nse_tidal_bdy.yaml` uses a relative path
  instead, resolved against `cwd=NSe/` (see Stage 2b below).
- `boundaries.old/config/{nse_bdy_create,nse_init_create}.yaml` (the
  pre-move copies, kept only as `.old` history).

The old `oceanicu_3d/nse_bdy_lonlat.txt` (repo-root, a 309-point
mis-landed edit — see "Update, 2026-08-17" below) is archived at
`NSe/Bathymetry/nse_bdy_lonlat.txt.mislanded_edit_20260803`, not left at
the repo root where it could be mistaken for a live copy again.

**If this ever looks inconsistent again:** `grep -rln nse_bdy_lonlat`
across `OceanICU/oceanicu_3d`, `ocean-prep`, and `boundaries.old` to find
every reference, then `ls -la` each one — only
`NSe/Bathymetry/nse_bdy_lonlat.txt` should be a real, live file;
everything else with that basename should be a `.bak*` or archived copy.

### Generating/verifying it -- `pygetm-config boundaries-generate`/`boundaries-check-points`

**As of this update, `nse_bdy_lonlat.txt` is a generated artifact --
never hand-edit it directly again.** The single source of truth is now
`NSe/config/nse_bdy_segments.yaml`, a short list of the same 9 segments
already baked into `nse_cmems.yaml`'s `open_boundaries:` block (index
form: `side`/`l`/`mstart`/`mstop`, or guide-point form:
`lonlat_a`/`lonlat_b` for an easier-to-pick-by-eye new segment). Both
the point file and the `open_boundaries:` YAML block are derived from
that one file, so they can't silently diverge again (see the pygetm-config
repo's `boundary_tool.py` for why, and the 2026-08-03 "123 points
silently on land" / "mislanded edit" incidents above for why this
mattered).

**This same physical domain is shared by ~10 run configs**
(`nse_cmems.yaml`, `nse_cmems_tides.yaml`, `nse_cmip6.yaml`,
`nse_cmip6_tides.yaml`, `nse_cmip6_raw.yaml`,
`nse_cmip6_fabm_dryrun{,_mizer}.yaml`, `nse_woa.yaml`,
`nse_woa_tides.yaml` -- `nse_from_oceanicu.yaml` deliberately excluded
for now, see below) -- their `open_boundaries:` *positions* must stay
identical, but **not** their `type_2d`/`type_3d`: the CMEMS configs use
`FLATHER_ELEV`, the CMIP6/WOA configs use `FLATHER_TRANSPORT` (a real,
intentional difference, confirmed 2026-10-22, not something a bulk
update should ever silently flip). Regenerate and push the new
positions out to every one of them in one go with (conda env with
`pygetm` installed):

```bash
conda activate pygetm
pygetm-config boundaries-generate \
  --domain NSe/config/nse_cmems.yaml \
  --segments NSe/config/nse_bdy_segments.yaml \
  --bdy-lonlat-out NSe/Bathymetry/nse_bdy_lonlat.txt \
  --plot /tmp/nse_boundaries.png --report /tmp/nse_boundaries.md \
  --apply-to NSe/config/nse_cmems.yaml --apply-to NSe/config/nse_cmems_tides.yaml \
  --apply-to NSe/config/nse_cmip6.yaml --apply-to NSe/config/nse_cmip6_tides.yaml \
  --apply-to NSe/config/nse_cmip6_raw.yaml \
  --apply-to NSe/config/nse_cmip6_fabm_dryrun.yaml \
  --apply-to NSe/config/nse_cmip6_fabm_dryrun_mizer.yaml \
  --apply-to NSe/config/nse_woa.yaml --apply-to NSe/config/nse_woa_tides.yaml
```

This calls pygetm's own real `add_by_index`/`adjust_mask` under the
hood, so a segment that's on land, overlaps another, or crosses another
fails loudly here, with the offending segment named -- instead of
silently writing a bad point file. `--apply-to` patches each target's
`open_boundaries:` list in place by position (`side`/`l`/`mstart`/
`mstop`/`name` only), via `ruamel.yaml` round-trip mode, so every
comment and everything else in the file is left untouched, and each
file's own `type_2d`/`type_3d` are preserved exactly as they already
are unless `--update-types` is also given. A target whose existing
segment count doesn't match (a segment being added/removed) is refused
unless `--allow-length-change` is passed too -- there's no existing
`type_2d`/`type_3d` to preserve for a genuinely new segment, so that's
a deliberate, opt-in step, not a silent guess. Without `--apply-to`, add
`--open-boundaries-out <path>` instead to just inspect the generated
block before deciding where it should go.

`nse_from_oceanicu.yaml` has a real, as-yet-unexplained one-point drift
(`mstart: 136` vs `137` on segment 1) and uses the older `WEST`/`NORTH`/
`EAST`/`SOUTH` side-naming convention instead of `LEFT`/`TOP`/`RIGHT`/
`BOTTOM` -- left out of `--apply-to` above deliberately (as of
2026-10-22) until someone confirms whether it's a legacy file that can
be retired or should be brought back in sync.

**Run this after every bathymetry regeneration -- it's now the direct,
automated replacement for the old manual numpy land-check snippet**:

```bash
pygetm-config boundaries-check-points \
  --domain NSe/config/nse_cmems.yaml --lonlat NSe/Bathymetry/nse_bdy_lonlat.txt
```

Exits non-zero and prints every `ON LAND: lon lat` point if the current
bathymetry has moved any boundary point onto land -- the exact failure
mode of the 2026-08-03 incident below, now caught immediately instead
of silently.

`generate_nse_bathymetry.py`
(mask_regions, rx0 smoothing, thalweg fixes) can change which cells are wet —
a point that was fine yesterday can end up on land today with no error or
warning anywhere else. This actually happened on 2026-08-03: a fix to
`mask_regions` rectangle-boundary floating-point tolerance (for an unrelated
single-row "Humber" closure) retroactively closed the eastern edge of the
pre-existing "West of Orkney" region, which had been silently *not* closing
due to the same rounding issue. 123 of 311 points ended up on land as a
side effect of a bathymetry fix that had nothing to do with boundaries.

**Update, 2026-08-17:** re-checked against the current `bathymetry_nse.nc`
— **0 of 311 points on land.** The bathymetry has evidently been
regenerated/corrected again since the above regression, independently of
any boundary-point edit. A partial fix for the 123-point regression was
attempted the same day it happened (121 of the 123 points moved to nearby
wet cells, 2 dropped as a redundant corner vertex once both adjacent
segments shifted) but landed on the wrong file (`oceanicu_3d/nse_bdy_lonlat.txt`
at the repo root, not the canonical copy) and was never actually applied —
now archived at `NSe/Bathymetry/nse_bdy_lonlat.txt.mislanded_edit_20260803`
for reference in case this regression (or a similar one) recurs. **Always
re-run the check above before trusting either "0 on land" or an old
point-count from this doc** — bathymetry regenerations happen independently
of this file and can silently invalidate either state.

### If a segment needs to move, shrink, or be dropped

1. **Back up first** — always: `.bak` copies with a timestamp suffix if a
   second round of fixes is likely (see `NSe/Bathymetry/nse_bdy_lonlat.txt.bak*`
   for the pattern used before this file became generated).
2. Edit `NSe/config/nse_bdy_segments.yaml` -- change the relevant
   segment's index tuple (or guide points), then re-run
   `pygetm-config boundaries-generate` (above) to regenerate both
   `nse_bdy_lonlat.txt` and the `open_boundaries:` block together.
   **Never edit `nse_bdy_lonlat.txt` by hand anymore** -- it's a build
   artifact now, and a hand edit will just be silently overwritten (or
   worse, drift from `nse_cmems.yaml`'s own segments again) the next
   time someone regenerates it.
3. **If boundary_data NC files already exist** (see §2 below) for the *old*
   point list, they must be re-cut to match — the point count/order in the
   NC files' `nbdyp` dimension must stay in lockstep with the text file, or
   pygetm will refuse to load them (`"length 312 ... actual extent 317"`
   type errors) or — worse — silently misalign points if counts happen to
   match by coincidence. Use `NSe/trim_bdy.py <src.nc> <dst.nc> <idx0> ...`
   to drop specific 0-based `nbdyp` indices from hourly/daily reference
   files; verify the index against `boundary_lon`/`boundary_lat` in the NC
   file first, not just the text file — they can drift independently of
   each other.
4. If more than a couple of points changed, it may be simpler to fully
   regenerate the reference data from source (§2) than to patch the NC
   files by hand.

## 1. The three-stage pipeline

NSe boundary conditions are built in three independent stages, each with its
own config and CLI tool (all `ocean-prep`, pip-installed editable — works
from any `cwd`):

| Stage | Produces | Tool | Config |
|---|---|---|---|
| 1. Historical reference | `boundary_data/nse/{hourly,daily}/*.nc` — real CMEMS reanalysis + near-real-time forecast, 2015–present | `run-cmems-boundaries` | `NSe/config/nse_bdy_create.yaml` |
| 2a. Future scenario (T/S) | `CMIP6/{model}/{experiment}/bdy_3d_{var}_*.nc` — delta-change projection | `run-delta-boundaries` | `NSe/config/nse_delta_bdy.yaml` |
| 2b. Future scenario (SSH/currents) | Tidal + CMIP6 mean SSH/transport, hourly | `run-tidal-boundaries` | `NSe/config/nse_tidal_bdy.yaml` |

Stage 1 must exist before stage 2a can run — `run-delta-boundaries` reads
the historical hourly/daily files as its cycling reference (it has no
independent boundary-point list; it inherits points from those NC files'
own `boundary_lon`/`boundary_lat`/`segment_id` variables). Stage 2b
(tidal) is independent of stage 1 — it reads TPXO9 + CMIP6 directly, using
`NSe/Bathymetry/nse_bdy_lonlat.txt` for its own point list.

### Stage 1 — historical reference (CMEMS)

```bash
cd /home/kb/source/repos/OceanICU/oceanicu_3d/NSe    # nse_bdy_create.yaml lives here (NSe-specific, not in ocean-prep)
run-cmems-boundaries --config config/nse_bdy_create.yaml --dryrun
run-cmems-boundaries --config config/nse_bdy_create.yaml
# selectively:
run-cmems-boundaries --config config/nse_bdy_create.yaml --dataset temperature salinity
run-cmems-boundaries --config config/nse_bdy_create.yaml --category physics
```

`nse_bdy_create.yaml`'s `boundary_points.file_path` is a real absolute path
directly at `NSe/Bathymetry/nse_bdy_lonlat.txt` — no symlink indirection
(fixed 2026-08-17; every domain setup follows this same pattern, its own
canonical copy in its own repo, referenced directly — a shared symlink
target doesn't generalize across setups).

### Stage 2a — future scenario, temperature/salinity (delta-change)

```bash
cd /home/kb/source/repos/OceanICU/oceanicu_3d/NSe
run-delta-boundaries --config config/nse_delta_bdy.yaml --dryrun
run-delta-boundaries --config config/nse_delta_bdy.yaml --scenario ssp126 ssp370 ssp585
run-delta-boundaries --config config/nse_delta_bdy.yaml \
    --future-start 2070-01-01 --future-end 2099-12-31
run-delta-boundaries --config config/nse_delta_bdy.yaml --variable thetao so
```

Method: `corrected(t) = AMM7/AMM15_ref(t_analog) + [CMIP6_future_clim(month) − CMIP6_hist_clim(month)]`,
where `t_analog` cycles through the historical reference period (same
calendar day/hour, year mapped modulo the reference length). Safe to run as
long as stage 1's hourly/daily files are current and correctly aligned with
`NSe/Bathymetry/nse_bdy_lonlat.txt` (they inherit its point layout
automatically from their own `boundary_lon`/`boundary_lat`/`segment_id`).

### Stage 2b — future scenario, SSH/currents (tidal + CMIP6)

```bash
cd /home/kb/source/repos/OceanICU/oceanicu_3d/NSe
run-tidal-boundaries --config config/nse_tidal_bdy.yaml --dryrun
run-tidal-boundaries --config config/nse_tidal_bdy.yaml
run-tidal-boundaries --config config/nse_tidal_bdy.yaml --start 2060-01-01 --end 2060-12-31
run-tidal-boundaries --config config/nse_tidal_bdy.yaml --model UKESM1-0-LL --scenario ssp126
```

`NSe/config/nse_tidal_bdy.yaml`'s `file_path` is `./Bathymetry/nse_bdy_lonlat.txt`,
a relative path resolved against the working directory — must still be run
with `cwd = NSe/`.

## 2. Post-generation sanity checks

- Re-run the land/water check in §0 if the bathymetry changed since the NC
  files were last built.
- `nc_nan_scan.py` (repo root, `oceanicu_3d/nc_nan_scan.py`) can scan any
  resulting or model-output NetCDF for unexpected NaNs inside the
  computational domain, distinguishing genuine bugs from NaN that's expected
  at open-boundary points for terms like `advU`/`advV`. See its own
  docstring — in short:
  ```bash
  python nc_nan_scan.py some_output.nc --vars advU,advV \
      --exclude-boundary-vars advU,advV --boundary-value 3,4
  ```
  (boundary mask codes are model/file-specific — check the "values=" list
  the tool prints per mask; this NSe setup's `masku`/`maskv` use 3 and 4
  for boundary orientation, not the GETM-textbook single code 2, which only
  the T-mask `maskt` uses here.)
