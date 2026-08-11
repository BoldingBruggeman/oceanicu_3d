#!/usr/bin/env python3
"""Compare the St Malo bathymetry patch (bathymetry_st_malo.nc, built via ocean-prep
from EMODnet -- see generate_st_malo_bathymetry.py) against the same window of
bathymetry_nse.nc, the file it could potentially replace/patch a slice of.

Usage
-----
    ./compare_st_malo_bathymetry.py

Writes st_malo_compare.png (original coarse | new coarse | high-res EMODnet source,
so coastline fidelity can be checked against the un-regridded data directly) and
st_malo_diff.png (new - original, in metres AND as a percent change) into this
directory, and prints a summary.

Compares `depth_rx0_0p20` -- the final, rx0-smoothed depth both files' own
pipelines produce under the same variable name, i.e. what would actually be used
in a pygetm domain (not `bathymetry`/`depth_raw`, each file's own pre-processing
raw depth under a different name -- see the note printed at startup).
"""

import glob
from pathlib import Path

import cartopy.crs as ccrs
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

SCRIPT_DIR = Path(__file__).resolve().parent
PARENT_FILE = SCRIPT_DIR / "bathymetry_nse.nc"
NEW_FILE = SCRIPT_DIR / "bathymetry_st_malo.nc"
EMODNET_CACHE_GLOB = "/data/cache/emodnet/*.nc"

VARIABLE = "depth_rx0_0p20"


def load_matching_slice():
    new = xr.open_dataset(NEW_FILE)
    parent = xr.open_dataset(PARENT_FILE)
    # tolerance= makes a genuine grid misalignment a hard KeyError instead of a
    # silent, arbitrary nearest-cell snap -- this comparison is only meaningful
    # cell-for-cell, exactly matching st_malo_bathy_create.yaml's own grid: block
    # (copied from bathymetry_nse.nc's real coordinate array, see that config's
    # header comment).
    original = parent.sel(lon=new.lon, lat=new.lat, method="nearest", tolerance=1e-4)
    # .sel(..., method="nearest") keeps the PARENT's own (slightly different, see
    # load_matching_slice's tolerance= above) coordinate values on the result --
    # real bug caught here: xarray arithmetic between two DataArrays aligns by
    # exact coordinate label, so subtracting against `new` (whose coords differ
    # by ~1e-7) silently produced an all-NaN result (zero label matches), not an
    # error. Reassigning `new`'s own coords (already verified equal to within
    # tolerance above) makes both sides share identical labels.
    original = original.assign_coords(lon=new.lon, lat=new.lat)
    print(f"Note: parent's raw pre-processing depth is 'bathymetry'; the new "
          f"file's own is 'depth_raw' (same role, different name -- both "
          f"pipelines agree on '{VARIABLE}' for the final depth compared below).\n")
    return original, new


def load_high_res_source(new: xr.Dataset) -> xr.Dataset:
    """Find the cached EMODnet tile covering `new`'s bbox and crop it to exactly
    that bbox -- the real, un-regridded source data, so coastline fidelity can be
    checked directly instead of only inferring it from the coarse regridded result.
    Matches by tile coverage (not a hardcoded cache filename, which encodes the
    exact buffered bbox and changes whenever the grid: block's own extent does --
    see st_malo_bathy_create.yaml's own lon_max history).
    """
    lon_min, lon_max = float(new.lon.min()), float(new.lon.max())
    lat_min, lat_max = float(new.lat.min()), float(new.lat.max())
    for path in glob.glob(EMODNET_CACHE_GLOB):
        ds = xr.open_dataset(path)
        if (ds.lon.min() <= lon_min and ds.lon.max() >= lon_max
                and ds.lat.min() <= lat_min and ds.lat.max() >= lat_max):
            cropped = ds.sel(lon=slice(lon_min, lon_max), lat=slice(lat_min, lat_max))
            print(f"High-res source: {path} ({ds.sizes['lon']}x{ds.sizes['lat']} full tile, "
                  f"cropped to {cropped.sizes['lon']}x{cropped.sizes['lat']} for this bbox, "
                  f"~{float(new.sizes['lon']) * 0.08 / cropped.sizes['lon'] * 1000:.0f} m/cell "
                  f"vs the coarse grid's own ~5.6 km/cell)")
            return cropped
        ds.close()
    raise FileNotFoundError(
        f"No cached EMODnet tile in {EMODNET_CACHE_GLOB} covers "
        f"lon {lon_min:.2f}..{lon_max:.2f}, lat {lat_min:.2f}..{lat_max:.2f} -- "
        f"run generate_st_malo_bathymetry.py first (it downloads/caches the source)."
    )


def summarize(original: xr.Dataset, new: xr.Dataset) -> tuple[xr.DataArray, xr.DataArray]:
    orig_depth = original[VARIABLE].where(original["ocean_mask"] == 1)
    new_depth = new[VARIABLE].where(new["ocean_mask"] == 1)
    diff = new_depth - orig_depth
    pct_diff = 100.0 * diff / orig_depth

    mask_disagree = int((original["ocean_mask"].values != new["ocean_mask"].values).sum())
    total_cells = int(new["ocean_mask"].size)

    print(f"Comparing '{VARIABLE}' over {new.sizes['lon']}x{new.sizes['lat']} cells "
          f"(lon {float(new.lon.min()):.2f}..{float(new.lon.max()):.2f}, "
          f"lat {float(new.lat.min()):.2f}..{float(new.lat.max()):.2f})\n")
    print(f"{'':20s} {'original':>12s} {'new (EMODnet)':>14s}")
    print(f"{'min depth (m)':20s} {float(orig_depth.min()):12.2f} {float(new_depth.min()):14.2f}")
    print(f"{'max depth (m)':20s} {float(orig_depth.max()):12.2f} {float(new_depth.max()):14.2f}")
    print(f"{'mean depth (m)':20s} {float(orig_depth.mean()):12.2f} {float(new_depth.mean()):14.2f}")
    print()
    print(f"mean |diff| (m) where both wet   : {float(np.abs(diff).mean()):.3f}")
    print(f"max  |diff| (m) where both wet   : {float(np.abs(diff).max()):.3f}")
    print(f"mean |diff| (%) where both wet   : {float(np.nanmean(np.abs(pct_diff))):.1f}")
    print(f"median |diff| (%) where both wet : {float(np.nanmedian(np.abs(pct_diff))):.1f}")
    print(f"ocean_mask disagreements         : {mask_disagree} / {total_cells} cells")
    return diff, pct_diff


def plot(original: xr.Dataset, new: xr.Dataset, high_res: xr.Dataset, diff: xr.DataArray, pct_diff: xr.DataArray) -> None:
    vmax = float(max(original[VARIABLE].max(), new[VARIABLE].max()))
    proj = ccrs.PlateCarree()

    fig, axes = plt.subplots(1, 3, figsize=(17, 5), subplot_kw={"projection": proj})
    pcm = None
    for ax, ds, title in zip(
        axes,
        [original, new, high_res],
        ["original (bathymetry_nse.nc)", "new (EMODnet, regridded to same grid)", "EMODnet source (native ~230 m/cell)"],
    ):
        ax.coastlines(resolution="10m", linewidth=0.8)
        depth = ds[VARIABLE].where(ds["ocean_mask"] == 1) if "ocean_mask" in ds else ds["depth"]
        pcm = ax.pcolormesh(ds.lon, ds.lat, depth, cmap="viridis_r", vmin=0, vmax=vmax, transform=proj)
        ax.set_title(title)
    fig.colorbar(pcm, ax=axes, label="depth (m)", shrink=0.8)
    out_compare = SCRIPT_DIR / "st_malo_compare.png"
    fig.savefig(out_compare, dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5), subplot_kw={"projection": proj}, constrained_layout=True)
    ax = axes[0]
    ax.coastlines(resolution="10m", linewidth=0.8)
    vabs = float(np.nanmax(np.abs(diff.values))) or 1.0
    pcm0 = ax.pcolormesh(new.lon, new.lat, diff, cmap="RdBu_r", vmin=-vabs, vmax=vabs, transform=proj)
    ax.set_title(f"{VARIABLE}: new − original (m)")
    fig.colorbar(pcm0, ax=ax, label="Δdepth (m)", shrink=0.8)

    ax = axes[1]
    ax.coastlines(resolution="10m", linewidth=0.8)
    # A percentile-based cap, not the raw max: shallow cells make for huge, unstable
    # percentages from small absolute differences (e.g. 1 m off a 2 m depth is 50%) --
    # a couple of those would otherwise wash out the colour scale for everywhere else.
    pabs = float(np.nanpercentile(np.abs(pct_diff.values), 95)) or 1.0
    pcm1 = ax.pcolormesh(new.lon, new.lat, pct_diff, cmap="RdBu_r", vmin=-pabs, vmax=pabs, transform=proj)
    ax.set_title(f"{VARIABLE}: percent change (new vs. original)")
    fig.colorbar(pcm1, ax=ax, label="Δdepth (%)", shrink=0.8)

    out_diff = SCRIPT_DIR / "st_malo_diff.png"
    fig.savefig(out_diff, dpi=150)
    plt.close(fig)

    print(f"\nWrote {out_compare} and {out_diff}")


def main() -> None:
    original, new = load_matching_slice()
    high_res = load_high_res_source(new)
    diff, pct_diff = summarize(original, new)
    plot(original, new, high_res, diff, pct_diff)


if __name__ == "__main__":
    main()
