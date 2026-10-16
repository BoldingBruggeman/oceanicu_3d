#!/usr/bin/env python3
"""Extract diagnostics for the NW silicate/carbon blow-up investigation
(docs/nw_silicate_blowup_investigation.md) directly from an experiment's
existing output/restart files -- no rerun needed.

Confirmed so far (2026-10-16, from orca/bb-server1): the anomaly sits at
open-boundary segment 2 (lon=-6.22, lat 58.50-59.15N), and the raw CMEMS
boundary forcing there is clean. The leading hypothesis points at
ERSEM's benthic dissolved-matter exchange term (K3/K4/K5 -- DIC/ammonium/
silicate all share one subroutine in benthic_column_dissolved_matter.F90)
and/or the particulate-silicate penetration-depth state (pen_depth_s,
Q6/Q7). This script pulls whatever of that is actually present in this
experiment's own output/restart netCDF files at the segment-2 location,
so it works with data already on disk -- it does not add new output
variables or touch the run itself.

Run this ON SCYLLA (or wherever the experiment's chunk directories
actually live) -- it has no access to bb-server1/orca-only data.

Usage:
    python3 diagnose_ersem_benthic_blowup.py --experiment-root /path/to/NSe/CMEMS/v01E_gvc

    # If the default lon/lat variable names or segment-2 bbox don't match
    # this setup, first just list what's actually in the files:
    python3 diagnose_ersem_benthic_blowup.py --experiment-root ... --list-vars
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np

try:
    import netCDF4 as nc
except ImportError:
    sys.exit("ERROR: this script needs netCDF4 (pip install netCDF4), same as the rest of running/.")

_CHUNK_DIR_RE = re.compile(r"^(?P<num>\d{3})_(?P<start>\d{8})_(?P<end>\d{8})$")

# Segment 2 of the NSe open boundary, per NSe/config/nse_bdy_create.yaml's own
# documented segment layout: western, lon=-6.220, lat 58.50-59.15N. This is
# where the silicate/carbon/benthic-DIC anomaly was confirmed to sit.
DEFAULT_LON_RANGE = (-6.6, -5.8)
DEFAULT_LAT_RANGE = (58.4, 59.25)

# Candidate names for things we're hunting for -- ERSEM/FABM variable naming
# is "<instance>_<constituent>" (confirmed against this project's own
# fabm_ersem_2D/3D output lists, e.g. G3_c, N5_s), but output group
# membership varies per config, so we search rather than assume presence.
PELAGIC_SILICATE = "N5_s"            # pelagic silicate state (confirmed in fabm_ersem_3D)
BENTHIC_DIC = "G3_c"                 # benthic DIC state (confirmed in fabm_ersem_2D)
WANTED_SUBSTRINGS = (
    "K3", "K4", "K5",                # benthic DIC/ammonium/silicate dissolved pools
    "Q6", "Q7",                      # benthic particulate (opal/POM) pools
    "pen_depth", "D1m", "D2m",       # penetration depth / oxic-layer-interface states
    "diff1", "diff2", "diff3",       # sediment diffusivities (bioturbation-enhanced)
    "poro", "Dtot", "cmix",          # sediment porosity / column depth / transfer constant
)


def find_chunk_dirs(experiment_root: Path) -> list[Path]:
    chunks = sorted(
        (p for p in experiment_root.iterdir() if p.is_dir() and _CHUNK_DIR_RE.match(p.name)),
        key=lambda p: p.name,
    )
    if not chunks:
        sys.exit(f"ERROR: no chunk directories (NNN_YYYYMMDD_YYYYMMDD) found directly under {experiment_root}")
    return chunks


def find_lonlat(ds: "nc.Dataset", lon_name: str | None, lat_name: str | None) -> tuple[np.ndarray, np.ndarray, str, str]:
    if lon_name and lat_name:
        return ds.variables[lon_name][:], ds.variables[lat_name][:], lon_name, lat_name
    candidates = [("lon", "lat"), ("longitude", "latitude"), ("grid/lon", "grid/lat")]
    for lo, la in candidates:
        if lo in ds.variables and la in ds.variables:
            return ds.variables[lo][:], ds.variables[la][:], lo, la
    raise KeyError(
        f"couldn't find a lon/lat variable pair automatically (tried {candidates}); "
        f"pass --lon-var/--lat-var explicitly. Variables present: {sorted(ds.variables.keys())}"
    )


def mask_for_bbox(lon: np.ndarray, lat: np.ndarray, lon_range, lat_range) -> np.ndarray:
    return (lon >= lon_range[0]) & (lon <= lon_range[1]) & (lat >= lat_range[0]) & (lat <= lat_range[1])


def list_vars(nc_path: Path) -> None:
    with nc.Dataset(nc_path) as ds:
        print(f"\n{nc_path}  ({len(ds.variables)} variables)")
        for name, var in sorted(ds.variables.items()):
            print(f"  {name:30s} dims={var.dimensions} shape={var.shape}")


def report_output_timeseries(chunk_dirs: list[Path], output_name: str, lon_range, lat_range,
                              lon_var: str | None, lat_var: str | None) -> None:
    print(f"\n=== {output_name}: time series at segment-2 bbox "
          f"(lon {lon_range}, lat {lat_range}) across {len(chunk_dirs)} chunk dir(s) ===")
    any_found = False
    for chunk_dir in chunk_dirs:
        f = chunk_dir / output_name
        if not f.is_file():
            continue
        any_found = True
        with nc.Dataset(f) as ds:
            try:
                lon, lat, _, _ = find_lonlat(ds, lon_var, lat_var)
            except KeyError as exc:
                print(f"  {chunk_dir.name}: {exc}")
                continue
            sel = mask_for_bbox(lon, lat, lon_range, lat_range)
            if not sel.any():
                print(f"  {chunk_dir.name}: no grid point falls inside the bbox -- "
                      f"check --lon-range/--lat-range or this experiment's own grid")
                continue
            time_var = ds.variables.get("time")
            times = nc.num2date(time_var[:], time_var.units) if time_var is not None else None
            for name in (PELAGIC_SILICATE, BENTHIC_DIC):
                if name not in ds.variables:
                    continue
                var = ds.variables[name]
                # Collapse all non-time dims other than the spatial ones already
                # selected by `sel`; take max over the bbox for a quick "did
                # anything here blow up" signal rather than every point's value.
                data = var[:]
                if data.ndim == len(sel.shape) + 1:
                    # leading time axis, spatial axes matching sel's shape
                    flat = data.reshape(data.shape[0], -1)[:, sel.ravel()]
                else:
                    flat = data[..., sel] if sel.ndim == 1 else None
                if flat is None:
                    print(f"  {chunk_dir.name}/{name}: shape {data.shape} didn't match the "
                          f"lon/lat mask shape {sel.shape} -- inspect manually (--list-vars)")
                    continue
                vmax = np.nanmax(flat, axis=-1)
                vmin = np.nanmin(flat, axis=-1)
                t0 = times[0] if times is not None else "?"
                t1 = times[-1] if times is not None else "?"
                print(f"  {chunk_dir.name}/{name}: [{t0} .. {t1}]  "
                      f"min={np.nanmin(vmin):.4g}  max={np.nanmax(vmax):.4g}")
    if not any_found:
        print(f"  (no {output_name} found in any chunk dir under {chunk_dirs[0].parent})")


def report_restart_snapshot(chunk_dirs: list[Path], lon_range, lat_range,
                             lon_var: str | None, lat_var: str | None) -> None:
    restart_candidates = []
    for chunk_dir in reversed(chunk_dirs):  # most recent first
        restart_candidates.extend(sorted(chunk_dir.glob("restart_*.nc")))
    if not restart_candidates:
        print("\n=== restart snapshot: none found (restart_*.nc) in any chunk dir -- "
              "can't check current K5/D1m/D2m state directly ===")
        return
    restart_path = restart_candidates[0]
    print(f"\n=== restart snapshot: {restart_path} ===")
    with nc.Dataset(restart_path) as ds:
        try:
            lon, lat, _, _ = find_lonlat(ds, lon_var, lat_var)
        except KeyError as exc:
            print(f"  {exc}")
            return
        sel = mask_for_bbox(lon, lat, lon_range, lat_range)
        if not sel.any():
            print("  no grid point falls inside the bbox -- check --lon-range/--lat-range")
            return
        matches = sorted(
            name for name in ds.variables
            if any(sub in name for sub in WANTED_SUBSTRINGS)
        )
        if not matches:
            print(f"  none of {WANTED_SUBSTRINGS} found as variable-name substrings in this restart.\n"
                  f"  Full variable list (use --list-vars for shapes too):\n"
                  f"  {sorted(ds.variables.keys())}")
            return
        for name in matches:
            var = ds.variables[name]
            data = var[:]
            if data.ndim == 0:
                print(f"  {name}: scalar = {float(data)}")
                continue
            if data.shape[-len(sel.shape):] != sel.shape:
                print(f"  {name}: shape {data.shape} doesn't match lon/lat mask shape {sel.shape} -- skipping")
                continue
            flat = data[..., sel]
            print(f"  {name}: min={np.nanmin(flat):.6g}  max={np.nanmax(flat):.6g}  mean={np.nanmean(flat):.6g}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--experiment-root", required=True, type=Path,
                    help="e.g. /work/.../experiments/NSe/CMEMS/v01E_gvc -- the directory "
                         "directly containing this experiment's NNN_YYYYMMDD_YYYYMMDD chunk dirs")
    p.add_argument("--output-name", default="nse_3d.nc",
                    help="output file name inside each chunk dir to scan for N5_s/G3_c time series "
                         "(default: nse_3d.nc, per NSe/config/nse_cmems.yaml)")
    p.add_argument("--lon-range", nargs=2, type=float, default=DEFAULT_LON_RANGE, metavar=("MIN", "MAX"))
    p.add_argument("--lat-range", nargs=2, type=float, default=DEFAULT_LAT_RANGE, metavar=("MIN", "MAX"))
    p.add_argument("--lon-var", default=None, help="override auto-detected longitude variable name")
    p.add_argument("--lat-var", default=None, help="override auto-detected latitude variable name")
    p.add_argument("--list-vars", action="store_true",
                    help="just dump every variable name/shape in the latest chunk's output and restart "
                         "files, then exit -- use this first if the defaults above don't find anything")
    args = p.parse_args()

    if not args.experiment_root.is_dir():
        sys.exit(f"ERROR: {args.experiment_root} is not a directory")

    chunk_dirs = find_chunk_dirs(args.experiment_root)
    print(f"Found {len(chunk_dirs)} chunk dir(s) under {args.experiment_root}: "
          f"{chunk_dirs[0].name} .. {chunk_dirs[-1].name}")

    if args.list_vars:
        latest = chunk_dirs[-1]
        out = latest / args.output_name
        if out.is_file():
            list_vars(out)
        restarts = sorted(latest.glob("restart_*.nc"))
        if restarts:
            list_vars(restarts[-1])
        else:
            print(f"\nno restart_*.nc in {latest}")
        return 0

    report_output_timeseries(chunk_dirs, args.output_name, args.lon_range, args.lat_range,
                              args.lon_var, args.lat_var)
    report_restart_snapshot(chunk_dirs, args.lon_range, args.lat_range, args.lon_var, args.lat_var)
    return 0


if __name__ == "__main__":
    sys.exit(main())
