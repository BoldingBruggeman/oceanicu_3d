#!/usr/bin/env python3
"""Splice the St Malo bathymetry patch (bathymetry_st_malo.nc) into a COPY of
bathymetry_nse.nc, at the exact index window the two files share.

This is a reviewable artifact for a later decision, not something applied to the
real domain file automatically -- writes bathymetry_nse_with_st_malo.nc, never
touches bathymetry_nse.nc itself (matching that file's own "no automatic backup"
warning in generate_nse_bathymetry.py's docstring: better to never need one here).

Usage
-----
    ./apply_st_malo_patch.py                 # writes bathymetry_nse_with_st_malo.nc
    ./apply_st_malo_patch.py --check-only     # locate the window and verify grid
                                               # alignment, write nothing

Every variable bathymetry_nse.nc and bathymetry_st_malo.nc share BY NAME
(basin_labels, depth_corrections_depth_rx0_0p20, depth_fixes, depth_fixes_delta,
depth_rx0_0p20, depth_smooth_delta, depth_u, depth_v, mask_regions, ocean_mask,
wet_fraction) is overwritten inside the window. One name mismatch: the parent's
'bathymetry' (raw, pre-processing depth) is patched from the new file's
'depth_raw' (same role, different name in each pipeline -- see
compare_st_malo_bathymetry.py's own note).
"""

import argparse
from pathlib import Path

import numpy as np
import xarray as xr

SCRIPT_DIR = Path(__file__).resolve().parent
PARENT_FILE = SCRIPT_DIR / "bathymetry_nse.nc"
NEW_FILE = SCRIPT_DIR / "bathymetry_st_malo.nc"
OUT_FILE = SCRIPT_DIR / "bathymetry_nse_with_st_malo.nc"

RENAMED_VARS = {"bathymetry": "depth_raw"}  # parent name -> new-file name


def find_window(parent: xr.Dataset, new: xr.Dataset) -> tuple[slice, slice]:
    """Locate new's own lon/lat extent as an integer index window into parent.

    Matches by nearest value (not exact float equality -- see compare_st_malo_
    bathymetry.py's own note on why parent/new coordinate floats differ by
    ~1e-7, netCDF round-trip noise, not a real grid mismatch), then verifies
    the WHOLE window lines up to within that same tolerance, not just its
    corners.
    """
    lon0 = int(np.argmin(np.abs(parent.lon.values - new.lon.values[0])))
    lat0 = int(np.argmin(np.abs(parent.lat.values - new.lat.values[0])))
    lon_sl = slice(lon0, lon0 + new.sizes["lon"])
    lat_sl = slice(lat0, lat0 + new.sizes["lat"])

    parent_window_lon = parent.lon.values[lon_sl]
    parent_window_lat = parent.lat.values[lat_sl]
    if not np.allclose(parent_window_lon, new.lon.values, atol=1e-4):
        raise ValueError(
            f"lon window mismatch: parent[{lon_sl}]={parent_window_lon[:3]}... "
            f"vs new={new.lon.values[:3]}... -- grids are not aligned as expected"
        )
    if not np.allclose(parent_window_lat, new.lat.values, atol=1e-4):
        raise ValueError(
            f"lat window mismatch: parent[{lat_sl}]={parent_window_lat[:3]}... "
            f"vs new={new.lat.values[:3]}... -- grids are not aligned as expected"
        )
    return lon_sl, lat_sl


def apply_patch(parent: xr.Dataset, new: xr.Dataset, lon_sl: slice, lat_sl: slice) -> xr.Dataset:
    patched = parent.copy(deep=True)
    shared = sorted(set(parent.data_vars) & set(new.data_vars))
    for var in shared:
        patched[var].values[lat_sl, lon_sl] = new[var].values
    for parent_var, new_var in RENAMED_VARS.items():
        if parent_var in parent.data_vars and new_var in new.data_vars:
            patched[parent_var].values[lat_sl, lon_sl] = new[new_var].values
    print(f"Patched {len(shared)} shared variable(s) + {len(RENAMED_VARS)} renamed "
          f"one(s) over window lon[{lon_sl.start}:{lon_sl.stop}] lat[{lat_sl.start}:{lat_sl.stop}] "
          f"({new.sizes['lon']}x{new.sizes['lat']} cells).")
    return patched


def verify_only_window_changed(parent: xr.Dataset, patched: xr.Dataset, lon_sl: slice, lat_sl: slice) -> None:
    """Real, automated check -- not just eyeballing: every cell OUTSIDE the
    patch window must be byte-identical to the original.
    """
    for var in sorted(set(parent.data_vars) & set(patched.data_vars)):
        a = parent[var].values.copy()
        b = patched[var].values.copy()
        a[lat_sl, lon_sl] = 0
        b[lat_sl, lon_sl] = 0
        if not np.array_equal(a, b, equal_nan=True):
            raise AssertionError(f"{var}: cells OUTSIDE the patch window changed -- patch is not localized")
    print("Verified: every cell outside the patch window is unchanged.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check-only", action="store_true", help="locate/verify the window, write nothing")
    args = p.parse_args()

    parent = xr.open_dataset(PARENT_FILE)
    new = xr.open_dataset(NEW_FILE)
    lon_sl, lat_sl = find_window(parent, new)
    print(f"Window located: lon[{lon_sl.start}:{lon_sl.stop}] lat[{lat_sl.start}:{lat_sl.stop}] "
          f"(lon {new.lon.values.min():.3f}..{new.lon.values.max():.3f}, "
          f"lat {new.lat.values.min():.3f}..{new.lat.values.max():.3f})")
    if args.check_only:
        return

    patched = apply_patch(parent, new, lon_sl, lat_sl)
    verify_only_window_changed(parent, patched, lon_sl, lat_sl)
    patched.to_netcdf(OUT_FILE)
    print(f"Wrote {OUT_FILE} (original {PARENT_FILE.name} left untouched).")


if __name__ == "__main__":
    main()
