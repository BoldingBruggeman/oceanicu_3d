#!/usr/bin/env python
"""One-off fix for river_flows_future_{scenario}[_noleap].nc's real coverage
gap: each file's first record is stamped at month-END (e.g. 2015-01-31),
not month-START, so any run/chunk requesting 2015-01-01 finds no data
before it. The LAST record already lands exactly on the scenario's own
real stop (2099-12-31) -- only the first record needs moving.

Rewrites time[0] in place to the first day of its own month (same
value/units/calendar otherwise unchanged -- no Q/Q_mean/regulated_flag
data is touched). Skips any file whose first record is already at
month-start, and skips "_daily" files entirely (already start exactly at
2015-01-01, nothing to fix). See driver/scripts/rivers.py's set_river_data
docstring, and prefer river_discharge.CMIP6.daily=true (the _daily.nc
sibling) for any config that can use it -- this is the fallback for one
that still reads the monthly file.
"""
from __future__ import annotations

import argparse
import glob
import sys

import cftime
import netCDF4 as nc


def fix_file(path: str, dry_run: bool) -> bool:
    ds = nc.Dataset(path, "r+" if not dry_run else "r")
    try:
        t = ds.variables["time"]
        units = t.units
        calendar = getattr(t, "calendar", "standard")
        first = cftime.num2date(t[0], units, calendar)
        month_start = cftime.datetime(first.year, first.month, 1, calendar=calendar)
        if first == month_start:
            print(f"{path}: already at month-start ({first}) -- skipping")
            return False
        new_raw = cftime.date2num(month_start, units, calendar)
        print(f"{path}: time[0] {first} -> {month_start}  (raw {t[0]} -> {new_raw})")
        if not dry_run:
            t[0] = new_raw
        return True
    finally:
        ds.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pattern", help="glob for the monthly files, e.g. "
                                    "'/data/BiasCorrected/CMIP6/*/*/rivers/river_flows_future_*.nc'")
    p.add_argument("--dry-run", action="store_true", help="report what would change, edit nothing")
    args = p.parse_args()

    paths = [f for f in sorted(glob.glob(args.pattern)) if "_daily" not in f]
    if not paths:
        print(f"no files matched {args.pattern!r} (after excluding *_daily*)", file=sys.stderr)
        return 1
    changed = sum(fix_file(f, args.dry_run) for f in paths)
    print(f"\n{changed}/{len(paths)} file(s) {'would be' if args.dry_run else ''} changed".replace("  ", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
