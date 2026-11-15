"""Regression test for derive_historical_dic_ta.py's output coordinate
attrs (2026-10-12): build() used to construct 'depth'/'boundary_lat'/
'boundary_lon'/'segment_id' from bare `.values` arrays, silently dropping
every CF attribute (standard_name/axis/positive/units) the source file
had. pygetm's own vertical_interpolation() refuses a depth axis with no
standard_name/axis attrs ("does not have a valid depth coordinate") --
confirmed real: every CMEMS+FABM run reading bio_carbon_pml_trend_*.nc
for O3_c/O3_TA crashed at Simulation construction with exactly that
exception. Minimal synthetic inputs -- only shapes/attrs matter here,
not physically realistic trend/salinity values.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from derive_historical_dic_ta import build

DEPTH_ATTRS = {
    "standard_name": "depth", "axis": "Z", "positive": "down",
    "units": "m", "long_name": "Depth",
}
LAT_ATTRS = {"units": "degrees_north", "standard_name": "latitude"}
LON_ATTRS = {"units": "degrees_east", "standard_name": "longitude"}
SEGID_ATTRS = {"long_name": "boundary segment index"}

NBDYP = 4
SEGMENT_ID = np.array([0, 0, 7, 7], dtype=np.int32)
DEPTH = np.array([0.0, 10.0])


def _write_bio_carbon_new(path: Path) -> None:
    time = pd.date_range("2024-07-29", periods=3, freq="D")
    shape = (len(time), NBDYP, len(DEPTH))
    ds = xr.Dataset(
        data_vars={
            "dissic": (("time", "nbdyp", "depth"), np.full(shape, 1900.0)),
            "talk": (("time", "nbdyp", "depth"), np.full(shape, 2300.0)),
        },
        coords={
            "time": time,
            "boundary_lat": ("nbdyp", np.array([56.0, 56.0, 58.0, 58.0]), LAT_ATTRS),
            "boundary_lon": ("nbdyp", np.array([3.0, 3.0, 12.0, 12.0]), LON_ATTRS),
            "segment_id": ("nbdyp", SEGMENT_ID, SEGID_ATTRS),
            "depth": ("depth", DEPTH, DEPTH_ATTRS),
        },
    )
    ds.to_netcdf(path)


def _write_physics_daily(path: Path, target_times: pd.DatetimeIndex) -> None:
    baseline_times = pd.date_range("2024-07-29", periods=3, freq="D")
    time = pd.DatetimeIndex(sorted(set(baseline_times) | set(target_times)))
    shape = (len(time), NBDYP, len(DEPTH))
    ds = xr.Dataset(
        data_vars={"so": (("time", "nbdyp", "depth"), np.full(shape, 35.0))},
        coords={"time": time, "depth": ("depth", DEPTH, DEPTH_ATTRS)},
    )
    ds.to_netcdf(path)


def _write_no3_boundary(path: Path, target_times: pd.DatetimeIndex) -> None:
    shape = (len(target_times), NBDYP, len(DEPTH))
    ds = xr.Dataset(
        data_vars={"no3": (("time", "nbdyp", "depth"), np.full(shape, 5.0))},
        coords={"time": target_times, "depth": ("depth", DEPTH, DEPTH_ATTRS)},
    )
    ds.to_netcdf(path)


def test_build_preserves_coordinate_attrs(tmp_path):
    target_times = pd.date_range("2010-01-01", "2010-01-02", freq="D")

    bcn_path = tmp_path / "bio_carbon_new.nc"
    phys_path = tmp_path / "physics_daily.nc"
    no3_path = tmp_path / "no3_boundary.nc"
    _write_bio_carbon_new(bcn_path)
    _write_physics_daily(phys_path, target_times)
    _write_no3_boundary(no3_path, target_times)

    ds = build(bcn_path, phys_path, no3_path, "2010-01-01", "2010-01-02")

    assert ds["depth"].attrs == DEPTH_ATTRS
    assert ds["boundary_lat"].attrs == LAT_ATTRS
    assert ds["boundary_lon"].attrs == LON_ATTRS
    assert ds["segment_id"].attrs == SEGID_ATTRS

    # Would-be regression: pygetm's vertical_interpolation() requires
    # exactly this (standard_name or axis) to recognize a depth axis.
    assert ds["depth"].attrs.get("standard_name") == "depth" or ds["depth"].attrs.get("axis") == "Z"
