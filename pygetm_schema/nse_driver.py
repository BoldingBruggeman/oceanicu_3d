#!/usr/bin/env python
"""Reference driver for nse_from_oceanicu.yaml (this directory), demonstrating the
composable pattern from pygetm-schema's docs/yaml_vs_python.md: the Domain itself
is now built generically by pygetm_schema.loader from the config's `bathymetry:`
section (schema-validated -- see schema._build_bathymetry_section), since reading
a pre-prepared bathymetry file turned out to need only variable names and a mask
convention, not bespoke code. Rivers, by contrast, genuinely stay bespoke,
project-specific Python here (mirroring OceanICU's real cfg_rivers.py, verified
against that source): they're dynamic and threshold-filtered from an EMORID file
at run time, not a static list.

This is a REFERENCE / illustrative script -- it needs a real bathymetry NetCDF
(referenced by the config's own `bathymetry:` section) and a real EMORID
river-discharge NetCDF to actually run (paths taken from the config's
`river_discharge:` section, which IS schema-validated -- see
oceanicu_providers.py, registered automatically below via
PYGETM_SCHEMA_PROVIDERS). Run in a pygetm-capable environment with
pygetm-schema installed there too (`pip install -e ".[introspect]"` from the
pygetm-schema repo) -- no sys.path hacks, this script has no dependency on
being located near the pygetm-schema repo, only on pygetm_schema being
importable:

    python nse_driver.py nse_from_oceanicu.yaml --start 2024-03-01T00:00:00 --stop 2024-03-02T00:00:00

Known issue in the source config, deliberately not silently fixed here either
(see the YAML's own comments): open_boundaries[*].type_3d is 0, which is not a
valid pygetm boundary-condition-type constant. This script corrects it to
ZERO_GRADIENT with a loud warning rather than either failing outright or hiding
the correction -- whoever owns the setup should confirm that's actually the
intended value and fix it upstream in the YAML.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
from pathlib import Path

import yaml

from pygetm_schema import loader
from pygetm_schema.errors import SchemaValidationError
from pygetm_schema.schema import build_schema
from pygetm_schema.yaml_parse import validate_config

# Real, working location on this machine (orca) as of 2026-08 -- used only when
# BATHYMETRY_FOLDER isn't set in the environment. machines.yaml already has a
# BATHYMETRY_FOLDER entry per hostname (same convention as TPXO_FOLDER/
# ERA5_FOLDER/etc.), but orca's current value ("/data/Bathymetry/NS") doesn't
# exist on disk -- stale, not fixed here since the intended correction isn't
# obvious (moved? renamed? never valid?). Whoever owns machines.yaml should
# either fix that entry or export BATHYMETRY_FOLDER correctly before relying
# on the env var alone.
_DEFAULT_BATHYMETRY_FOLDER = "/home/kb/source/repos/OceanICU/oceanicu_3d/Bathymetry"


def add_rivers(domain, config: dict):
    """Mirrors cfg_rivers.py's create() -- dynamic, threshold-filtered, read from
    an EMORID/JRC discharge file at run time. The *set* of rivers depends on the
    threshold and the domain's exact footprint, so (per pygetm-schema's
    docs/yaml_vs_python.md) this cannot become a static YAML list without losing
    that behavior; it has to stay a loop over real data, same as in OceanICU
    today.

    Reads the validated `river_discharge:` section (a `nested_by_label`
    ChoiceSpec -- see oceanicu_providers.py), not a free-form dict: whichever
    source is active (only "emorid" is registered today) has its fields
    flattened onto `config["river_discharge"]` directly by validate_config,
    regardless of whether the YAML wrote them nested under `emorid:` or flat.
    """
    import xarray as xr
    import pygetm

    rcfg = config["river_discharge"]
    path = Path(rcfg["folder"]) / rcfg["file"]
    threshold = rcfg.get("threshold", 0)

    with xr.open_dataset(path) as ds:
        # "qmean" heuristic normalizes away underscores/case -- the real EMORID
        # file (verified against /data/EMORID/EMORID_1990_2024.nc) names this
        # "Q_mean", not "qmean".
        qmean_name = next(v for v in ds.data_vars if "qmean" in v.lower().replace("_", ""))
        valid = ds[qmean_name] > threshold
        lons = ds["lon"].values[valid.values]
        lats = ds["lat"].values[valid.values]
        # Real file uses "site_name", not "name" -- check both rather than
        # silently falling back to anonymous numeric indices.
        name_var = next((v for v in ("site_name", "name") if v in ds), None)
        names = ds[name_var].values[valid.values] if name_var else range(len(lons))
        n_added = 0
        for name, lon, lat in zip(names, lons, lats):
            if not domain.contains(lon, lat):
                continue
            domain.rivers.add_by_location(str(name), lon, lat, coordinate_type=pygetm.CoordinateType.LONLAT)
            n_added += 1
    return n_added


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--start", required=True, help="ISO 8601 start time (per-invocation, like OceanICU's own CLI convention)")
    parser.add_argument("--stop", required=True, help="ISO 8601 stop time")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="print the fully validated/merged config as YAML and exit -- use this to check "
        "a modified nse_from_oceanicu.yaml is resolving the way you expect (e.g. after "
        "switching a provider's source:) without actually building or running anything",
    )
    parser.add_argument(
        "--dump-python",
        nargs="?",
        const=True,
        default=False,
        metavar="PATH",
        help="write a self-contained, standalone Python script (no pygetm_schema import "
        "needed to run it) implementing this config as literal native pyGETM calls, then "
        "exit without building/running anything here -- see pygetm_schema.codegen's module "
        "docstring for the 'one-off debugging tool, regenerate don't hand-maintain' scoping. "
        "CAVEAT: rivers here come from this script's OWN bespoke add_rivers() (dynamic, "
        "threshold-filtered from the real EMORID file at run time), not from a static "
        "config `rivers:` list codegen can read -- the generated script will NOT include "
        "them; add that loop by hand in the output if you need river forcing there too. "
        "Honors --dry-run for whether the script includes a run loop. Defaults to "
        "generated_nse.py, or pass a path.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="verbosity for pygetm_schema.loader's native pyGETM call logging (see its module "
        "docstring's 'Debugging' note -- every domain/simulation/strategy construction, "
        ".set() call, add_by_index/add_by_location, output file, sim.start()/advance()/"
        "finish() is logged with the actual resolved arguments). DEBUG also shows one line "
        "per open_boundaries entry (rivers stay at DEBUG regardless since this script's own "
        "add_rivers, not loader.add_rivers, is what actually adds them here -- see that "
        "function for its own per-river logging via pygetm's domain.rivers). Default INFO.",
    )
    args = parser.parse_args(argv)

    # Must happen before build_schema()/anything pygetm-related -- see
    # pygetm_schema.loader's module docstring "Debugging" note: pyGETM's own
    # first domain-construction call has a side effect of configuring the root
    # logger itself, and a log call made before that point is silently dropped
    # (Python's "handler of last resort" only shows WARNING+ with no handler
    # configured yet) -- relying on that side effect's timing isn't safe.
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s:%(name)s:%(message)s")

    # Auto-register oceanicu_providers.py (this directory) via the zero-
    # packaging PYGETM_SCHEMA_PROVIDERS env var (see pygetm-schema's
    # docs/providers.md) -- setdefault, not a hard override, so a caller can
    # still point at a different registry explicitly if they want. Must
    # happen BEFORE build_schema(), which is what actually reads this env var
    # (via providers.discover_provider_sections()).
    os.environ.setdefault(
        "PYGETM_SCHEMA_PROVIDERS",
        f"{Path(__file__).parent / 'oceanicu_providers.py'}:register_oceanicu_providers",
    )
    schema = build_schema()

    with open(args.config) as f:
        raw = yaml.safe_load(f)

    # Resolve bathymetry.path's folder from BATHYMETRY_FOLDER, exactly like
    # run_model.py resolves TPXO_FOLDER/ERA5_FOLDER/etc. (os.getenv(VAR,
    # default) -- see module-level comment on _DEFAULT_BATHYMETRY_FOLDER for
    # why the default, not the env var, is what actually works today).
    bathy = raw.get("bathymetry")
    if bathy and bathy.get("path") and not os.path.isabs(bathy["path"]):
        folder = os.getenv("BATHYMETRY_FOLDER", _DEFAULT_BATHYMETRY_FOLDER)
        bathy["path"] = str(Path(folder) / bathy["path"])

    # Known issue in the source config -- see module docstring. Corrected here
    # with a loud warning, not silently.
    for b in raw.get("open_boundaries", []):
        if b.get("type_3d") == 0:
            print(
                "WARNING: open_boundaries[...].type_3d=0 is not a valid "
                "boundary_condition_type -- correcting to ZERO_GRADIENT (1). "
                "Confirm this is actually intended and fix it in the YAML.",
                file=sys.stderr,
            )
            b["type_3d"] = "ZERO_GRADIENT"

    raw.setdefault("runtime", {})["time"] = args.start

    try:
        config, errors = validate_config(raw, schema)
        if errors:
            for e in errors:
                print(e, file=sys.stderr)
            print(f"{len(errors)} error(s) -- fix the config before running", file=sys.stderr)
            return 1
    except SchemaValidationError as e:  # pragma: no cover -- validate_config returns errors, doesn't raise
        print(e, file=sys.stderr)
        return 1

    if args.print_config:
        print(yaml.safe_dump(config, sort_keys=False))
        return 0

    if args.dump_python:
        from pygetm_schema import codegen

        script = codegen.generate_script(config, schema, stop=args.stop, dry_run=args.dry_run)
        out_path = args.dump_python if isinstance(args.dump_python, str) else "generated_nse.py"
        with open(out_path, "w") as f:
            f.write(script)
        print(
            f"wrote {out_path} -- NOTE: rivers are added dynamically by this script's own "
            "add_rivers(), not present in the generated output (see --help)",
            file=sys.stderr,
        )
        return 0

    domain = loader.build_domain(config, schema)
    print(f"domain built: {domain.nx} x {domain.ny}", file=sys.stderr)

    n_rivers = add_rivers(domain, config)
    print(f"{n_rivers} river(s) added from {config['river_discharge']['file']}", file=sys.stderr)

    # From here on, entirely generic -- driven by the schema/config like any
    # other pyGETM setup, regardless of how `domain` was actually built above.
    loader.add_open_boundaries(domain, config, schema)
    sim = loader.build_simulation(domain, config, schema)
    loader.apply_data_assignments(sim, domain, config, schema)
    loader.configure_output(sim, config, schema)
    loader.start_simulation(sim, config, schema)

    if args.dry_run:
        print(f"dry run: built and started, time={sim.time}", file=sys.stderr)
        loader.finish_simulation(sim)
        return 0

    stop = datetime.datetime.fromisoformat(args.stop)
    loader.run_loop(sim, config, stop)
    loader.finish_simulation(sim)
    print(f"finished: time={sim.time}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
