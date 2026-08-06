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

# Same convention, and (unlike BATHYMETRY_FOLDER) machines.yaml's own
# TPXO_FOLDER for this host (orca: /server/data/TPXO9) is verified correct --
# real TPXO9 atlas data confirmed present there, not stale.
_DEFAULT_TPXO_FOLDER = "/server/data/TPXO9"


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


def set_sst_proxy(sim, domain, config: dict) -> None:
    """Barotropic runtypes (BAROTROPIC_2D/BAROTROPIC_3D) have no computed sea
    surface temperature to give pygetm's FluxesFromMeteo airsea
    implementation (which requires sst to be set regardless of runtype), so
    they use t2m (2m air temperature) as a stand-in. BAROCLINIC runs have a
    real, model-calculated SST and don't need this substitution at all.
    Verified directly against cfg_airsea.py's own data() function -- its
    comment there: "if not a baroclinic run use the t2m temperatures as
    proxy for SST" (`sim.sst = sim.airsea.t2m`). Without this, sim.start()
    crashes under a non-baroclinic runtype with `AssertionError: sst is
    masked`.

    Must run AFTER data_assignments (needs sim.airsea.t2m to already hold a
    real value, not just exist -- cfg_airsea.py's own version of this line
    lives right after its own t2m/d2m/u10/... assignments, not before them)
    -- registered via post_data_script (see pygetm_schema.loader.
    run_post_data_script), not river_discharge.script (that hook runs
    before the simulation object even exists, too early for this).
    """
    import pygetm

    if sim.runtype < pygetm.RunType.BAROCLINIC:
        sim.sst = sim.airsea.t2m


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--start", required=True, help="ISO 8601 start time (per-invocation, like OceanICU's own CLI convention)")
    parser.add_argument("--stop", required=True, help="ISO 8601 stop time")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-unavailable-output",
        action="store_true",
        help="drop individual requested output fields that don't exist for the chosen "
        "runtype (e.g. baroclinic fields under BAROTROPIC_2D) with a warning, instead of "
        "failing -- default is to fail loudly. Matches pygetm-schema run's own flag.",
    )
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
        "docstring for the 'regenerate when the config changes, don't hand-maintain' scoping. "
        "Rivers are included: river_discharge.emorid.script (resolved above) points back at "
        "this file's own add_rivers(), whose real source gets embedded in the generated "
        "script (see pygetm_schema.loader.run_river_discharge_script's docstring). The "
        "generated script has its own real argparse CLI (-h shows it) -- --start/--stop/"
        "--dry-run/--load-restart/--save-restart/--skip-unavailable-output are genuine "
        "runtime arguments of THAT script, defaulting to whatever was given here, not fixed "
        "at generation time. Defaults to generated_nse.py, or pass a path.",
    )
    parser.add_argument("--load-restart", default=None, metavar="PATH", help="resume from a restart file; overrides runtime.time with the restart's own time")
    parser.add_argument("--save-restart", default=None, metavar="PATH", help="write a restart file for this run")
    parser.add_argument(
        "--data-root",
        action="append",
        metavar="NAME=VALUE",
        help="override a data-path environment variable used by ${VAR}/$VAR references in "
        "file/folder config fields; repeatable, always wins. Matches pygetm-schema run's own "
        "flag (see pygetm_schema.loader.apply_data_roots).",
    )
    parser.add_argument(
        "--data-roots-file",
        default=None,
        metavar="PATH",
        help="YAML file of NAME: value data-path env vars; only fills gaps not already "
        "exported in the environment. Matches pygetm-schema run's own flag.",
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

    # Before any real file access (TODO item 15, pygetm-schema) -- populates
    # os.environ for whatever ${VAR}/$VAR data-path references a config uses
    # (see loader.resolve_data_path). NSe's own bathymetry.path/tpxo_folder
    # resolution below (BATHYMETRY_FOLDER/TPXO_FOLDER) is a separate, older,
    # bespoke mechanism -- NOT yet migrated to use ${VAR} syntax directly in
    # nse_from_oceanicu.yaml, so this call doesn't change ITS behavior today;
    # it's here so --data-root/--data-roots-file are available for any OTHER
    # data_assignments/array_like file field that already does (or later
    # adopts) ${VAR} syntax.
    loader.apply_data_roots(args.data_root, args.data_roots_file)

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

    # Same TPXO_FOLDER env var run_model.py itself resolves (see machines.yaml)
    # -- overrides every kind='tpxo' data_assignments entry's tpxo_folder, same
    # override-with-real-default pattern as bathymetry above.
    for entry in raw.get("data_assignments", []):
        if entry.get("kind") == "tpxo":
            entry["tpxo_folder"] = os.getenv("TPXO_FOLDER", _DEFAULT_TPXO_FOLDER)

    # river_discharge.emorid.script's own schema default (see
    # oceanicu_providers.py, computed from THAT file's own __file__) isn't
    # auto-injected by validate_config -- it only ever keeps fields
    # EXPLICITLY present in the raw YAML, never fills in unset ones (a
    # schema `default` is template/TUI-display-only). Set it here explicitly
    # if the YAML doesn't override it, so nse_from_oceanicu.yaml itself
    # doesn't need a machine-specific absolute path baked in -- same
    # "resolve portably at run time" reasoning as BATHYMETRY_FOLDER/
    # TPXO_FOLDER above. Only "emorid" today (the one real registered
    # source, see oceanicu_providers.py's own river_discharge role).
    river_discharge = raw.get("river_discharge") or {}
    emorid_cfg = river_discharge.get("emorid") or {}
    if river_discharge.get("source") == "emorid" and not emorid_cfg.get("script"):
        emorid_cfg["script"] = f"{Path(__file__).parent / 'nse_driver.py'}:add_rivers"
        river_discharge["emorid"] = emorid_cfg
        raw["river_discharge"] = river_discharge

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

    # post_data_script isn't part of pygetm-schema's OWN schema at all (no
    # natural home for a single bare string -- see loader.run_post_data_script's
    # own docstring), unlike river_discharge.script (which piggybacks on the
    # ALREADY-schema-registered river_discharge role). Injected directly onto
    # the validated config dict here, not into `raw` before validate_config --
    # validate_config only ever keeps fields matching a real schema section,
    # so injecting into `raw` would just get it silently dropped. Placed
    # before the --print-config check below so it's visible there too, same
    # as river_discharge.script.
    config.setdefault("post_data_script", f"{Path(__file__).parent / 'nse_driver.py'}:set_sst_proxy")

    if args.print_config:
        print(yaml.safe_dump(config, sort_keys=False))
        return 0

    if args.dump_python:
        from pygetm_schema import codegen

        out_path = args.dump_python if isinstance(args.dump_python, str) else "generated_nse.py"
        config_yaml_path = str(Path(out_path).with_name(Path(out_path).stem + "_config.yaml"))
        script = codegen.generate_script(
            config,
            schema,
            stop=args.stop,
            dry_run=args.dry_run,
            load_restart=args.load_restart,
            save_restart=args.save_restart,
            skip_unavailable_output=args.skip_unavailable_output,
            config_yaml_path=config_yaml_path,
        )
        with open(out_path, "w") as f:
            f.write(script)
        print(f"wrote {out_path}", file=sys.stderr)
        print(f"wrote {config_yaml_path}", file=sys.stderr)
        return 0

    domain = loader.build_domain(config, schema)
    print(f"domain built: {domain.nx} x {domain.ny}", file=sys.stderr)

    # From here on, entirely generic -- driven by the schema/config like any
    # other pyGETM setup, regardless of how `domain` was actually built above.
    # Order matters and matches run_model.py's own create_domain() exactly:
    # open boundaries -> domain.cfl_check() -> rivers (an earlier version of
    # this script added rivers BEFORE open boundaries, the reverse of both
    # run_model.py and loader.build_and_configure's own order).
    loader.add_open_boundaries(domain, config, schema)
    loader.check_domain_cfl(domain)

    # Goes through the SAME generic mechanism --dump-python's generated
    # scripts use (river_discharge.script, see pygetm_schema.loader.
    # run_river_discharge_script) rather than calling add_rivers() directly,
    # so real execution here and the generated standalone script are
    # provably consistent, not two separately-maintained paths to the same
    # rivers. add_rivers() itself is unchanged and still lives in this file
    # -- river_discharge.emorid.script (resolved above) just points back at
    # it by path.
    n_rivers = loader.run_river_discharge_script(domain, config)
    print(f"{n_rivers} river(s) added from {config['river_discharge']['file']}", file=sys.stderr)

    sim = loader.build_simulation(domain, config, schema)
    loader.apply_data_assignments(sim, domain, config, schema)

    # Goes through the SAME generic mechanism --dump-python's generated
    # scripts use (post_data_script, see pygetm_schema.loader.
    # run_post_data_script), same reasoning as run_river_discharge_script
    # above -- real execution and the generated script are provably
    # consistent, not two separately-maintained paths. set_sst_proxy() itself
    # is unchanged and still lives in this file -- post_data_script (resolved
    # above) just points back at it by path.
    loader.run_post_data_script(sim, domain, config)

    loader.configure_output(sim, config, schema, skip_unavailable_fields=args.skip_unavailable_output)

    # Mirrors cli.py's own --load-restart/--save-restart handling exactly
    # (loader.start_simulation has no restart support of its own -- this
    # replicates its two lines by hand, using loader.build_start_kwargs,
    # which exists specifically for this override case; see its own
    # docstring). add_restart() must be registered before sim.start(), same
    # as any other output file; load_restart()'s own return value overrides
    # runtime.time -- resuming from a restart always uses the restart's own
    # saved time, not whatever runtime.time/--start the config/CLI gave.
    if args.save_restart:
        sim.output_manager.add_restart(args.save_restart)
    start_kwargs = loader.build_start_kwargs(config, schema)
    if args.load_restart:
        start_kwargs["time"] = sim.load_restart(args.load_restart)
    sim.start(**start_kwargs)

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
