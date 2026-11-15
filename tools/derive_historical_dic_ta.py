#!/usr/bin/env python3
"""derive_historical_dic_ta.py -- build the 2010-2014 historical-bridge
`dissic`/`talk` FABM boundary file by secular-trend extrapolation and a
salinity regression, both from Partridge, Artioli, Powley &
Lessin (PML, Dec 2025), "A Reproducible CO9p2 AMM7 NEMOv4.0.4 ERSEM
Configuration" (see oceanicu_3d/docs/pml-amm7-ersem-comparison.md for
the full summary).

No CMEMS product has dissic/talk before 2024-02-28/07-29 (no historical
reanalysis, NWS, Baltic or global). Two methods, by boundary segment:

Atlantic/NW-shelf segments (0-6, 8): a real secular-trend extrapolation.
    PML fit an exponential-in-depth trend to a real, independently
    compiled GLODAPv2.2020 cruise dataset, normalized to salinity 35,
    centered on 2002. The NSe subset of GLODAPv2.2023 (4303 rows, 15
    cruises, mostly 2001-2005) is far too sparse for our own fit (it
    gives depth trends of -210 to +437 mmol/m3/yr), so PML's Atlantic
    coefficients are used:
        DIC_trend(h) = 0.96805 * exp(-0.000519414 * h)   [per year, nDIC]
        TA_trend(h)  = 0.18086 * exp(-0.000181059 * h)   [per year, nTA]
    Applied to our own baseline (PML's GLODAP climatology is not
    available): the ANFC-period record (`bio_carbon_new`, from
    2024-02-28/07-29) is salinity-normalized, time-averaged, shifted back
    to each 2010-2014 date with the trend, and de-normalized with that
    date's salinity.

Baltic segment 7: PML's own real salinity regression, not a trend.
    Baltic dissic/ph also start only in 2024/2025 (derive_baltic_talk.py
    covers the ANFC period), and Baltic DIC/TA are dominated by
    freshwater mixing, so PML's salinity regression is used:
        DIC = 23.767 * S + 1388
        TA  = 25.406 * S + 1410.15
    applied to the 2010-2014 salinity of the physics daily boundary
    file at segment 7 (~49% finite; the gaps are in that file).

Both branches add PML's nitrate-anomaly seasonality:
    DIC = DIC + (106/16) * N~
    TA  = TA  - N~
    N~ = N(t) - N_bar(month(t))
with N(t) the no3 boundary value at the date (every no3 cell is either
always finite or always NaN over 2010-2024) and N_bar the monthly
climatology of the whole no3 record (the role of PML's WOA mean). Neither
branch has a seasonal cycle of its own; this provides it (PML Eq. 7-8).

Usage
-----
    python tools/derive_historical_dic_ta.py \\
        --bio-carbon-new /data/OceanICU/oceanicu_3d/data/NSe/CMEMS/bdy/bio_carbon_new_2024-07-29_to_2026-08-17.nc \\
        --physics-daily /data/OceanICU/oceanicu_3d/data/NSe/CMEMS/bdy/daily_2010-01-01_to_2026-08-17.nc \\
        --no3-boundary /data/OceanICU/oceanicu_3d/data/NSe/CMEMS/bdy/bio_daily_2010-01-01_to_2026-08-17.nc \\
        --start 2010-01-01 --stop 2014-12-31 \\
        --out /data/OceanICU/oceanicu_3d/data/NSe/CMEMS/bdy/bio_carbon_pml_trend_20100101_20141231.nc
"""
import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

log = logging.getLogger(__name__)

BALTIC_SEGMENT = 7

# PML's own published Atlantic coefficients (docs/pml-amm7-ersem-comparison.md,
# Partridge et al. 2025) -- per-year trend of the SALINITY-NORMALIZED
# (to S=35) quantity, exponential in depth h (m).
_DIC_TREND_A, _DIC_TREND_B = 0.96805, -0.000519414
_TA_TREND_A, _TA_TREND_B = 0.18086, -0.000181059

# Real seawater DIC/TA are always O(1500-2500) once in mmol/m3 -- same
# unit-mislabeling check derive_baltic_talk.py already uses (CMEMS's own
# `units` attrs aren't trustworthy here).
_PLAUSIBLE_MMOL_M3_FLOOR = 50.0


def _to_mmol_m3(da: xr.DataArray, name: str) -> xr.DataArray:
    mean_mag = float(np.abs(da).mean(skipna=True))
    if mean_mag < _PLAUSIBLE_MMOL_M3_FLOOR:
        log.info("%s: mean magnitude %.3g looks like mol/m3 despite its units attrs -- scaling x1000 to mmol/m3",
                  name, mean_mag)
        return da * 1000.0
    log.info("%s: mean magnitude %.3g already looks like mmol/m3 -- not rescaling", name, mean_mag)
    return da


def _year_fraction(times) -> np.ndarray:
    """Real (non-integer) year, e.g. 2024-07-02 ~= 2024.5 -- for trend
    arithmetic, not calendar bucketing."""
    t = pd.DatetimeIndex(np.asarray(times, dtype="datetime64[ns]"))
    days_in_year = np.where(t.is_leap_year, 366.0, 365.0)
    return t.year.values + (t.dayofyear.values - 1) / days_in_year


# PML's own Redfield ratio for converting a nitrate anomaly into a DIC
# anomaly (Eq. 7-8 in the comparison doc -- "C:Si:N:P = 106:15:16:1").
_REDFIELD_C_OVER_N = 106.0 / 16.0


def nitrate_anomaly(no3_boundary: xr.Dataset, target_times: pd.DatetimeIndex, idx: np.ndarray) -> np.ndarray:
    """Real nitrate anomaly N~(t) = N(t) - N_bar(month(t)) at each target
    date, for the given boundary-point subset -- PML's own Eq. 7-8
    notation. N(t) is the real no3 value at the target date; N_bar is a
    monthly climatology built from no3_boundary's OWN full real record
    (not bounded to target_times) -- see this module's own docstring for
    why both are safe (no3 has zero partial/temporal-gap cells).
    """
    no3 = no3_boundary["no3"].isel(nbdyp=idx)
    climatology = no3.groupby("time.month").mean(dim="time")  # (month, nbdyp, depth)
    months = xr.DataArray(target_times.month, dims="time", coords={"time": target_times})
    clim_at_target = climatology.sel(month=months)  # (time, nbdyp, depth)
    real_at_target = no3.sel(time=target_times)  # (time, nbdyp, depth)
    anomaly = (real_at_target - clim_at_target).values
    valid = anomaly[np.isfinite(anomaly)]
    if valid.size:
        log.info("nitrate anomaly (%d points): n=%d valid, mean=%.3f, min=%.3f, max=%.3f mmol/m3",
                  len(idx), valid.size, valid.mean(), valid.min(), valid.max())
    return anomaly


def derive_atlantic(bio_carbon_new: xr.Dataset, physics_daily: xr.Dataset,
                     target_times: pd.DatetimeIndex, non_baltic_idx: np.ndarray) -> dict:
    """Trend-extrapolate dissic/talk at every non-Baltic boundary point,
    back from a real ANFC-period baseline to each date in target_times.
    """
    depth = bio_carbon_new["depth"].values  # (ndepth,) shared grid, confirmed vs. physics_daily
    dic_trend = _DIC_TREND_A * np.exp(_DIC_TREND_B * depth)  # (ndepth,)
    ta_trend = _TA_TREND_A * np.exp(_TA_TREND_B * depth)

    baseline_time = bio_carbon_new["time"]
    t_ref = float(_year_fraction(baseline_time.values).mean())
    log.info("Atlantic baseline reference year (real ANFC record mean): %.3f", t_ref)

    so_baseline = physics_daily["so"].sel(time=baseline_time).isel(nbdyp=non_baltic_idx)  # (time, nbdyp, depth)
    dissic = _to_mmol_m3(bio_carbon_new["dissic"].isel(nbdyp=non_baltic_idx), "dissic (baseline)")
    talk = bio_carbon_new["talk"].isel(nbdyp=non_baltic_idx)

    # Normalize to S=35, then time-average over the real baseline record
    # -- one value per (nbdyp, depth), representative of t_ref.
    dic_norm_baseline = (dissic * 35.0 / so_baseline).mean(dim="time", skipna=True)
    ta_norm_baseline = (talk * 35.0 / so_baseline).mean(dim="time", skipna=True)

    target_year = _year_fraction(target_times.values)  # (ntarget,)
    years_back = t_ref - target_year  # positive: target is in the past

    # (ntarget, nbdyp, ndepth): shift the normalized baseline backward,
    # per-depth trend broadcast over nbdyp.
    dic_norm_hist = dic_norm_baseline.values[None, :, :] - dic_trend[None, None, :] * years_back[:, None, None]
    ta_norm_hist = ta_norm_baseline.values[None, :, :] - ta_trend[None, None, :] * years_back[:, None, None]

    # De-normalize using each TARGET date's own real historical salinity.
    so_hist = physics_daily["so"].sel(time=target_times).isel(nbdyp=non_baltic_idx).values  # (ntarget, nbdyp, ndepth)
    dissic_hist = dic_norm_hist * so_hist / 35.0
    talk_hist = ta_norm_hist * so_hist / 35.0

    n_valid = int(np.isfinite(dissic_hist).sum())
    log.info("Atlantic segments: %d non-Baltic points, %d depths, %d valid dissic values over %d days",
              len(non_baltic_idx), len(depth), n_valid, len(target_times))
    return {"dissic": dissic_hist, "talk": talk_hist}


def derive_baltic(physics_daily: xr.Dataset, target_times: pd.DatetimeIndex, baltic_idx: np.ndarray) -> dict:
    """PML's own real salinity regression -- no trend, no normalization,
    just real historical salinity plugged into their published formula.
    """
    so_hist = physics_daily["so"].sel(time=target_times).isel(nbdyp=baltic_idx).values  # (ntarget, nbdyp, ndepth)
    dissic_hist = 23.767 * so_hist + 1388.0
    talk_hist = 25.406 * so_hist + 1410.15
    frac_finite = float(np.isfinite(so_hist).mean())
    log.info("Baltic segment %d: %d points, salinity %.1f%% finite over %d days (real gap in the physics "
             "file itself, not filled here)", BALTIC_SEGMENT, len(baltic_idx), frac_finite * 100, len(target_times))
    return {"dissic": dissic_hist, "talk": talk_hist}


def build(bio_carbon_new_path: Path, physics_daily_path: Path, no3_boundary_path: Path,
          start: str, stop: str) -> xr.Dataset:
    bcn = xr.open_dataset(bio_carbon_new_path)
    phys = xr.open_dataset(physics_daily_path)
    no3_ds = xr.open_dataset(no3_boundary_path)

    segment_id = bcn["segment_id"].values.astype(int)
    baltic_idx = np.where(segment_id == BALTIC_SEGMENT)[0]
    non_baltic_idx = np.where(segment_id != BALTIC_SEGMENT)[0]
    log.info("Segments: %d Baltic (id=%d), %d Atlantic/NW-shelf points, %d total",
              len(baltic_idx), BALTIC_SEGMENT, len(non_baltic_idx), bcn.sizes["nbdyp"])

    target_times = pd.date_range(start, stop, freq="D")

    out_dissic = np.full((len(target_times), bcn.sizes["nbdyp"], bcn.sizes["depth"]), np.nan, dtype="float64")
    out_talk = np.full_like(out_dissic, np.nan)

    atl = derive_atlantic(bcn, phys, target_times, non_baltic_idx)
    atl_n_anom = nitrate_anomaly(no3_ds, target_times, non_baltic_idx)
    out_dissic[:, non_baltic_idx, :] = atl["dissic"] + _REDFIELD_C_OVER_N * atl_n_anom
    out_talk[:, non_baltic_idx, :] = atl["talk"] - atl_n_anom

    balt = derive_baltic(phys, target_times, baltic_idx)
    balt_n_anom = nitrate_anomaly(no3_ds, target_times, baltic_idx)
    out_dissic[:, baltic_idx, :] = balt["dissic"] + _REDFIELD_C_OVER_N * balt_n_anom
    out_talk[:, baltic_idx, :] = balt["talk"] - balt_n_anom

    ds = xr.Dataset(
        data_vars={
            "dissic": (("time", "nbdyp", "depth"), out_dissic.astype("float32"),
                       {"units": "mmol m-3", "long_name": "Dissolved inorganic carbon"}),
            "talk": (("time", "nbdyp", "depth"), out_talk.astype("float32"),
                     {"units": "mmol m-3", "long_name": "Total alkalinity"}),
        },
        coords={
            "time": target_times,
            "boundary_lat": ("nbdyp", bcn["boundary_lat"].values, bcn["boundary_lat"].attrs),
            "boundary_lon": ("nbdyp", bcn["boundary_lon"].values, bcn["boundary_lon"].attrs),
            "segment_id": ("nbdyp", segment_id, bcn["segment_id"].attrs),
            # Keep the CF attributes (standard_name/axis/positive/units): pygetm's
            # vertical_interpolation() rejects a depth axis without them.
            "depth": ("depth", bcn["depth"].values, bcn["depth"].attrs),
        },
    )
    ds.attrs["source"] = (
        f"derive_historical_dic_ta.py -- Atlantic/NW-shelf segments: PML (Partridge et al. 2025) "
        f"published Atlantic trend coefficients (DIC_trend=a*exp(b*h), a={_DIC_TREND_A}, b={_DIC_TREND_B}; "
        f"TA_trend a={_TA_TREND_A}, b={_TA_TREND_B}) applied to a salinity-normalized baseline from "
        f"{bio_carbon_new_path.name}, shifted backward and de-normalized with real historical salinity "
        f"from {physics_daily_path.name}. Baltic segment {BALTIC_SEGMENT}: PML's own salinity regression "
        f"(DIC=23.767*S+1388, TA=25.406*S+1410.15) applied to real historical salinity directly, no "
        f"trend. Both branches: PML's own nitrate-anomaly seasonality (DIC += 106/16*N~, TA -= N~, "
        f"N~ = real no3(t) - its own monthly climatology) from {no3_boundary_path.name}."
    )
    ds.attrs["history"] = f"Generated by derive_historical_dic_ta.py, target period {start} to {stop}"
    return ds


def _write_nc(ds: xr.Dataset, path: Path) -> None:
    encoding = {v: {"dtype": "float32", "contiguous": True} for v in ds.data_vars}
    tmp = path.with_suffix(path.suffix + ".tmp_derive_historical_dic_ta")
    ds.to_netcdf(tmp, encoding=encoding)
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bio-carbon-new", type=Path, required=True)
    parser.add_argument("--physics-daily", type=Path, required=True)
    parser.add_argument("--no3-boundary", type=Path, required=True,
                         help="real no3 boundary file (full real coverage across the target period) "
                              "used for the nitrate-anomaly seasonality layer -- see this module's own "
                              "docstring")
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--stop", default="2014-12-31")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dryrun", action="store_true", help="Compute and log stats, write nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                         format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")

    ds = build(args.bio_carbon_new, args.physics_daily, args.no3_boundary, args.start, args.stop)

    for name in ("dissic", "talk"):
        vals = ds[name].values
        valid = vals[np.isfinite(vals)]
        if valid.size:
            log.info("%s: n=%d valid, mean=%.1f, min=%.1f, max=%.1f mmol/m3", name, valid.size,
                      valid.mean(), valid.min(), valid.max())

    if args.dryrun:
        log.info("Dry-run: not writing.")
        return 0

    _write_nc(ds, args.out)
    log.info("Written: %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
