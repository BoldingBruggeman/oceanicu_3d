#!/usr/bin/env python3
"""Splice the St Malo bathymetry patch (bathymetry_st_malo.nc) into a COPY of
bathymetry_nse.nc, at the exact index window the two files share.

This is a reviewable artifact for a later decision, not something applied to the
real domain file automatically -- writes bathymetry_nse_with_st_malo.nc, never
touches bathymetry_nse.nc itself (matching that file's own "no automatic backup"
warning in generate_nse_bathymetry.py's docstring: better to never need one here).

Thin wrapper around ocean-prep's own general `bathymetry-splice` tool (see
ocean-prep/lib/bathymetry/splice.py + cli/bathymetry_splice.py) -- this used to be a
hand-rolled implementation of the same window-locate/splice/verify logic; kept as a
real script here (rather than just documenting the command) so the exact parameters
used for THIS patch stay reproducible without having to remember them, matching
generate_st_malo_bathymetry.py's own reasoning for wrapping ocean-prep in a
subprocess call rather than importing it directly -- `ocean_prep` is only installed
in the `ocean-stack` conda env, not the `pygetm` env this script itself runs in.

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
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PARENT_FILE = SCRIPT_DIR / "bathymetry_nse.nc"
PATCH_FILE = SCRIPT_DIR / "bathymetry_st_malo.nc"
OUT_FILE = SCRIPT_DIR / "bathymetry_nse_with_st_malo.nc"

PYTHON = Path("/home/kb/miniconda3/envs/ocean-stack/bin/python3")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check-only", action="store_true", help="locate/verify the window, write nothing")
    args = p.parse_args()

    cmd = [
        str(PYTHON), "-m", "cli.bathymetry_splice",
        "--parent", str(PARENT_FILE),
        "--patch", str(PATCH_FILE),
        "--output", str(OUT_FILE),
        "--rename", "bathymetry=depth_raw",
    ]
    if args.check_only:
        cmd.append("--check-only")

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd="/home/kb/source/repos/ocean-prep")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
