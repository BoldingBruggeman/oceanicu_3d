# NSe boundary condition generation — runbook

Practical how-to. For dataset-level details (CMEMS product IDs, coverage
dates, Baltic override rationale) see `ocean-prep/docs/nse_boundary_sources.md`
— that doc explains *what data*; this one explains *what to run, in what
order, from where*.

## 0. The boundary point file — read this first

**Canonical copy:** `NSe/nse_bdy_lonlat.txt` (currently 311 points).

This is the file the running model actually uses — confirmed empirically by
comparing it against `lon_bdy`/`lat_bdy` in a `getm-dump.nc` crash dump
written by a live run: **exact match, 0 points different**. Treat any other
copy as untrustworthy until proven otherwise (see "Known copies" below).

**Policy: this file belongs to the setup.** It must live under `NSe/`, not
in `ocean-prep/bdy_coords/` or `boundaries/bdy_coords/` or the
`oceanicu_3d/` repo root. Those locations should reference this copy
(symlink or an updated `file_path:` in their configs), not hold independent
copies. As of 2026-08-03 they do NOT — see "Known copies" below — this is
unresolved technical debt, not the intended design.

### Verifying it against the bathymetry

Every boundary point must land on a wet cell in the current
`NSe/Bathymetry/bathymetry_nse.nc`. Check with:

```python
import netCDF4, numpy as np

with netCDF4.Dataset("NSe/Bathymetry/bathymetry_nse.nc") as nc:
    lon = np.array(nc["lon"][:]); lat = np.array(nc["lat"][:])
    mask = np.array(nc["ocean_mask"][:]).astype(bool)

pts = []
with open("NSe/nse_bdy_lonlat.txt") as f:
    for line in f.readlines()[2:]:
        line = line.strip()
        if not line: continue
        lo, la = map(float, line.split(","))
        pts.append((lo, la))

n_land = 0
for lo, la in pts:
    i = int(np.argmin(np.abs(lon - lo)))
    j = int(np.argmin(np.abs(lat - la)))
    if not mask[j, i]:
        n_land += 1
        print("ON LAND:", lo, la)
print(f"{len(pts)} points, {n_land} on land")
```

**Run this after every bathymetry regeneration.** `generate_nse_bathymetry.py`
(mask_regions, rx0 smoothing, thalweg fixes) can change which cells are wet —
a point that was fine yesterday can end up on land today with no error or
warning anywhere else. This actually happened on 2026-08-03: a fix to
`mask_regions` rectangle-boundary floating-point tolerance (for an unrelated
single-row "Humber" closure) retroactively closed the eastern edge of the
pre-existing "West of Orkney" region, which had been silently *not* closing
due to the same rounding issue. 123 of 311 points ended up on land as a
side effect of a bathymetry fix that had nothing to do with boundaries.

### If a point needs to move or be dropped

1. **Back up first** — always: `.bak` copies with a timestamp suffix if a
   second round of fixes is likely (see `NSe/nse_bdy_lonlat.txt.bak*` for
   the pattern used so far).
2. Edit `NSe/nse_bdy_lonlat.txt` directly (plain `lon,lat` per line, header
   `T-grid` / `lon,lat`).
3. **If boundary_data NC files already exist** (see §2 below) for the *old*
   point list, they must be re-cut to match — the point count/order in the
   NC files' `nbdyp` dimension must stay in lockstep with the text file, or
   pygetm will refuse to load them (`"length 312 ... actual extent 317"`
   type errors) or — worse — silently misalign points if counts happen to
   match by coincidence. Use `NSe/trim_bdy.py <src.nc> <dst.nc> <idx0> ...`
   to drop specific 0-based `nbdyp` indices from hourly/daily reference
   files; verify the index against `boundary_lon`/`boundary_lat` in the NC
   file first, not just the text file (they can drift independently — see
   "Known copies").
4. If more than a couple of points changed, it may be simpler to fully
   regenerate the reference data from source (§2) than to patch the NC
   files by hand.

## 1. The three-stage pipeline

NSe boundary conditions are built in three independent stages, each with its
own config and CLI tool (all `ocean-prep`, pip-installed editable — works
from any `cwd`):

| Stage | Produces | Tool | Config |
|---|---|---|---|
| 1. Historical reference | `boundary_data/nse/{hourly,daily}/*.nc` — real CMEMS reanalysis + near-real-time forecast, 2015–present | `run-cmems-boundaries` | `boundaries/config/nse_bdy_create.yaml` |
| 2a. Future scenario (T/S) | `CMIP6/{model}/{experiment}/bdy_3d_{var}_*.nc` — delta-change projection | `run-delta-boundaries` | `NSe/config/nse_delta_bdy.yaml` |
| 2b. Future scenario (SSH/currents) | Tidal + CMIP6 mean SSH/transport, hourly | `run-tidal-boundaries` | `NSe/config/nse_tidal_bdy.yaml` |

Stage 1 must exist before stage 2a can run — `run-delta-boundaries` reads
the historical hourly/daily files as its cycling reference (it has no
independent boundary-point list; it inherits points from those NC files'
own `boundary_lon`/`boundary_lat`/`segment_id` variables). Stage 2b
(tidal) is independent of stage 1 — it reads TPXO9 + CMIP6 directly, using
`NSe/nse_bdy_lonlat.txt` for its own point list.

### Stage 1 — historical reference (CMEMS)

```bash
cd /home/kb/source/repos/boundaries    # nse_bdy_create.yaml lives here
run-cmems-boundaries --config config/nse_bdy_create.yaml --dryrun
run-cmems-boundaries --config config/nse_bdy_create.yaml
# selectively:
run-cmems-boundaries --config config/nse_bdy_create.yaml --dataset temperature salinity
run-cmems-boundaries --config config/nse_bdy_create.yaml --category physics
```

⚠️ **As of 2026-08-03, `nse_bdy_create.yaml`'s `boundary_points.file_path`
resolves (via `boundaries/bdy_coords/nse_bdy_lonlat.txt`, a symlink) to
`oceanicu_3d/nse_bdy_lonlat.txt` at the repo root — NOT `NSe/nse_bdy_lonlat.txt`.**
Do not run this for real until that's repointed (see "Known copies"), or
you will regenerate the historical reference files against a boundary point
list that doesn't match the live model's — reintroducing exactly the kind
of mismatch this doc exists to prevent.

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
`NSe/nse_bdy_lonlat.txt` (they inherit its point layout automatically, since
they were hand-trimmed against it this session — see "Known copies" for the
current true state).

### Stage 2b — future scenario, SSH/currents (tidal + CMIP6)

```bash
cd /home/kb/source/repos/OceanICU/oceanicu_3d/NSe
run-tidal-boundaries --config config/nse_tidal_bdy.yaml --dryrun
run-tidal-boundaries --config config/nse_tidal_bdy.yaml
run-tidal-boundaries --config config/nse_tidal_bdy.yaml --start 2060-01-01 --end 2060-12-31
run-tidal-boundaries --config config/nse_tidal_bdy.yaml --model UKESM1-0-LL --scenario ssp126
```

Must be run with `cwd = NSe/` — its config uses `file_path: ./nse_bdy_lonlat.txt`,
a relative path resolved against the working directory, not the config file's
own location.

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

## 3. Known copies of `nse_bdy_lonlat.txt` — current mess, as of 2026-08-03

| Path | Points | Status |
|---|---|---|
| `NSe/nse_bdy_lonlat.txt` | 311 | **Canonical** — matches the live model (verified via getm-dump.nc), but currently has 123 points on land due to the 2026-08-03 mask_regions regression (§0) — needs re-fixing before any regeneration |
| `oceanicu_3d/nse_bdy_lonlat.txt` (repo root) | 309 | User-edited 2026-08-03, NOT the file the model reads. 121 of its points differ from `NSe/`'s and are verified wet against the current bathymetry — the edit looks directionally correct but landed on the wrong copy |
| `boundaries/bdy_coords/nse_bdy_lonlat.txt` | — | Symlink → the repo-root file above (not `NSe/`) |
| `ocean-prep/bdy_coords/nse_bdy_lonlat.txt` | 317 | Real file, not a symlink. Stale — byte-identical to `NSe/nse_bdy_lonlat.txt.bak` (session's very first backup, before *any* fixes this session) |

**Configs currently pointing at the wrong copy** (via the `boundaries/bdy_coords`
symlink or `ocean-prep`'s own stale copy), needing repointing at `NSe/nse_bdy_lonlat.txt`:
`boundaries/config/nse_bdy_create.yaml`, `boundaries/config/nse_init_create.yaml`,
`ocean-prep/config/nse_bdy_create.yaml`, `ocean-prep/config/nse_tidal_bdy.yaml`,
`ocean-prep/config/nse_init_create.yaml`.

**Config already pointing at the right copy:** `NSe/config/nse_tidal_bdy.yaml`
(relative path resolves correctly *only* when run with `cwd=NSe/`, see stage 2b above).

Until this is cleaned up (repoint the symlinks/configs at `NSe/nse_bdy_lonlat.txt`,
delete or symlink-away the stale/divergent copies), don't run stage 1
(`run-cmems-boundaries`) for real — it would write new historical reference
files keyed to the wrong point list.
