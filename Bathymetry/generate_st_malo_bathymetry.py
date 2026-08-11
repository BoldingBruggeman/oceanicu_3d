#!/usr/bin/env python3
"""Regenerate the St Malo bathymetry patch (bathymetry_st_malo.nc) from EMODnet,
on a grid matching bathymetry_nse.nc exactly.

All pipeline behaviour (grid bounds/resolution, source, fixes:, mask_regions:,
thalweg:, smooth:) is controlled by ocean-prep/config/st_malo_bathy_create.yaml --
edit that and re-run this script, no flags needed for config changes. See that
file's own header comment for how its grid was chosen to align exactly with
bathymetry_nse.nc's own lon/lat coordinate arrays.

Usage
-----
    ./generate_st_malo_bathymetry.py            # full run, writes bathymetry_st_malo.nc
    ./generate_st_malo_bathymetry.py --dryrun    # print resolved config, write nothing

Mirrors generate_nse_bathymetry.py's own shape (same PYTHON interpreter constant,
same reasoning for cwd=), but simpler: this is a fresh `grid:` build (see that
config's own `grid:` block), not a reprocess-an-existing-file one, so there's no
ncks pre-extraction step and no --fixes-file/--accept-fixes wiring needed.
"""

import argparse
import subprocess
import sys
from pathlib import Path

CONFIG = Path("/home/kb/source/repos/ocean-prep/config/st_malo_bathy_create.yaml")
PYTHON = Path("/home/kb/miniconda3/envs/ocean-stack/bin/python3")


def run_pipeline(extra_args: list[str]) -> None:
    """Run cli.bathymetry_regrid against st_malo_bathy_create.yaml.

    Run with cwd=ocean-prep (not oceanicu_3d, unlike generate_nse_bathymetry.py's
    own config): st_malo_bathy_create.yaml's own output.report_dir ("./report/
    st_malo/") is relative to the ocean-prep checkout, matching where every other
    *_bathy_create.yaml config in that repo (tamar, nwes, ...) already writes its
    own reports -- verified directly: a real run with this exact cwd wrote
    ocean-prep/report/st_malo/st_malo_report.md.
    """
    cmd = [str(PYTHON), "-m", "cli.bathymetry_regrid", "--config", str(CONFIG)] + extra_args
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(CONFIG.parent.parent))
    print(f"Report dir: {CONFIG.parent.parent / 'report' / 'st_malo'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dryrun", action="store_true", help="print the resolved pipeline config, write nothing")
    args, unknown = parser.parse_known_args()

    extra_args = unknown + (["--dryrun"] if args.dryrun else [])
    run_pipeline(extra_args)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
