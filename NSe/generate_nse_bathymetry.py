#!/usr/bin/env python3
"""Regenerate the NSe coarse bathymetry (bathymetry_nse.nc) from the pristine
GETM source, applying mask_regions / fixes / thalweg / smooth settings from
nse_bathymetry.yaml plus the accepted Belt Sea / Irish Sea / Great Belt /
Little Belt thalweg fixes from fixes.yaml.

Usage
-----
    ./generate_nse_bathymetry.py                # full run, writes bathymetry_nse.nc
    ./generate_nse_bathymetry.py --dryrun        # print resolved config, write nothing
    ./generate_nse_bathymetry.py --no-fixes      # skip --fixes-file/--accept-fixes
                                                  # (only the 4 explicit YAML fixes: entries apply)
    ./generate_nse_bathymetry.py --extract-only  # (re)run the ncks extraction step only

All pipeline behaviour (coarse source file, depth/mask variable names,
fixes:, mask_regions:, thalweg: waypoints, smooth.local_filters,
smooth.local_rx0) is controlled by nse_bathymetry.yaml — edit that and
re-run this script, no flags needed for config changes.

WARNING: overwrites output.file (bathymetry_nse.nc) in place, no backup
taken automatically — copy it first if you want to diff against the
previous version.
"""

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

GEBCO_SOURCE = Path("/data/kb/BLUE2/nwes/topo.nc")
TOPO_EXTRACT = SCRIPT_DIR / "Bathymetry" / "topo_nse_orig.nc"
CONFIG = SCRIPT_DIR / "config" / "nse_bathymetry.yaml"
FIXES_FILE = Path("/home/kb/source/repos/ocean-prep/report/nwes/fixes.yaml")

PYTHON = Path("/home/kb/miniconda3/envs/ocean-stack/bin/python3")

# Fixed to the NSe domain (matches nse_bathymetry.yaml's original grid extent).
NCKS_LON_RANGE = ("-7.50", "12.98")
NCKS_LAT_RANGE = ("48.50", "61.00")


def extract_topo() -> None:
    """(Re)extract the NSe region from the pristine GETM source via ncks."""
    print(f"Extracting NSe region from {GEBCO_SOURCE} -> {TOPO_EXTRACT}")
    subprocess.run(
        [
            "ncks", "-O",
            "-d", f"lon,{NCKS_LON_RANGE[0]},{NCKS_LON_RANGE[1]}",
            "-d", f"lat,{NCKS_LAT_RANGE[0]},{NCKS_LAT_RANGE[1]}",
            str(GEBCO_SOURCE), str(TOPO_EXTRACT),
        ],
        check=True,
    )


def run_pipeline(use_fixes: bool, extra_args: list[str]) -> None:
    """Run the bathymetry-regrid pipeline against nse_bathymetry.yaml.

    Run with cwd=SCRIPT_DIR (NSe/), not ocean-prep: the config's
    output.report_dir is a relative path ("./report/nse/"), and it should
    land next to this script/config, not inside the ocean-prep checkout.
    This works because ocean-prep is pip-installed editable in the
    ocean-prep conda env, so 'cli.bathymetry_regrid' is importable from any
    working directory — no need to cd into the ocean-prep repo.
    """
    cmd = [str(PYTHON), "-m", "cli.bathymetry_regrid", "--config", str(CONFIG)]
    if use_fixes:
        cmd += ["--fixes-file", str(FIXES_FILE), "--accept-fixes"]
    cmd += extra_args

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(SCRIPT_DIR))
    print(f"Report dir: {SCRIPT_DIR / 'report'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extract-only", action="store_true",
                         help="(re)run the ncks extraction step only, skip the pipeline")
    parser.add_argument("--no-fixes", action="store_true",
                         help="skip --fixes-file/--accept-fixes (only YAML fixes: entries apply)")
    parser.add_argument("--dryrun", action="store_true",
                         help="print the resolved pipeline config, write nothing")
    args, unknown = parser.parse_known_args()

    if args.extract_only:
        extract_topo()
        return

    if not TOPO_EXTRACT.exists():
        extract_topo()

    extra_args = unknown + (["--dryrun"] if args.dryrun else [])
    run_pipeline(use_fixes=not args.no_fixes, extra_args=extra_args)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
