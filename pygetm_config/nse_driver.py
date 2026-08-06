#!/usr/bin/env python
"""Reference driver for nse_from_oceanicu.yaml (this directory), demonstrating the
composable pattern from pygetm-config's docs/yaml_vs_python.md: the Domain itself
is now built generically by pygetm_config.loader from the config's `bathymetry:`
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
PYGETM_CONFIG_PROVIDERS). Run in a pygetm-capable environment with
pygetm-config installed there too (`pip install -e ".[introspect]"` from the
pygetm-config repo) -- no sys.path hacks, this script has no dependency on
being located near the pygetm-config repo, only on pygetm_config being
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

from pygetm_config import loader
from pygetm_config.errors import SchemaValidationError
from pygetm_config.schema import build_schema
from pygetm_config.yaml_parse import validate_config

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


# The five data_script/script/post_data_script target functions that used to
# live in this file directly (add_rivers/set_river_data/set_meteo_data/
# set_sst_proxy/set_hydrography_ic) now live in scripts/ -- one file per
# provider role, grouped by role rather than fully atomized (river position +
# data stay together, see oceanicu_providers.py's own comment on why), since
# nse_driver.py itself was becoming a grab-bag of unrelated per-role logic.
# Nothing about HOW they're loaded changed: still load_dotted_target'd via
# "path/to/file.py:name" (see scripts/rivers.py, scripts/meteo.py,
# scripts/hydrography.py), never imported directly here -- see those files'
# own module docstrings.


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
        "failing -- default is to fail loudly. Matches pygetm-config run's own flag.",
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
        help="write a self-contained, standalone Python script (no pygetm_config import "
        "needed to run it) implementing this config as literal native pyGETM calls, then "
        "exit without building/running anything here -- see pygetm_config.codegen's module "
        "docstring for the 'regenerate when the config changes, don't hand-maintain' scoping. "
        "Rivers are included: river_discharge.emorid.script (resolved above) points at "
        "scripts/rivers.py's add_rivers(), whose real source gets embedded in the generated "
        "script (see pygetm_config.loader.run_river_discharge_script's docstring). The "
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
        "file/folder config fields; repeatable, always wins. Matches pygetm-config run's own "
        "flag (see pygetm_config.loader.apply_data_roots).",
    )
    parser.add_argument(
        "--data-roots-file",
        default=None,
        metavar="PATH",
        help="YAML file of NAME: value data-path env vars; only fills gaps not already "
        "exported in the environment. Matches pygetm-config run's own flag.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="verbosity for pygetm_config.loader's native pyGETM call logging (see its module "
        "docstring's 'Debugging' note -- every domain/simulation/strategy construction, "
        ".set() call, add_by_index/add_by_location, output file, sim.start()/advance()/"
        "finish() is logged with the actual resolved arguments). DEBUG also shows one line "
        "per open_boundaries entry (rivers stay at DEBUG regardless since scripts/rivers.py's "
        "own add_rivers, not loader.add_rivers, is what actually adds them here -- see that "
        "function for its own per-river logging via pygetm's domain.rivers). Default INFO.",
    )
    args = parser.parse_args(argv)

    # Must happen before build_schema()/anything pygetm-related -- see
    # pygetm_config.loader's module docstring "Debugging" note: pyGETM's own
    # first domain-construction call has a side effect of configuring the root
    # logger itself, and a log call made before that point is silently dropped
    # (Python's "handler of last resort" only shows WARNING+ with no handler
    # configured yet) -- relying on that side effect's timing isn't safe.
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s:%(name)s:%(message)s")

    # Before any real file access (TODO item 15, pygetm-config) -- populates
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
    # packaging PYGETM_CONFIG_PROVIDERS env var (see pygetm-config's
    # docs/providers.md) -- setdefault, not a hard override, so a caller can
    # still point at a different registry explicitly if they want. Must
    # happen BEFORE build_schema(), which is what actually reads this env var
    # (via providers.discover_provider_sections()).
    os.environ.setdefault(
        "PYGETM_CONFIG_PROVIDERS",
        f"{Path(__file__).parent / 'oceanicu_providers.py'}:register_oceanicu_providers",
    )
    schema = build_schema()

    with open(args.config) as f:
        raw = yaml.safe_load(f)

    # Resolve domain.path's folder from BATHYMETRY_FOLDER, exactly like
    # run_model.py resolves TPXO_FOLDER/ERA5_FOLDER/etc. (os.getenv(VAR,
    # default) -- see module-level comment on _DEFAULT_BATHYMETRY_FOLDER for
    # why the default, not the env var, is what actually works today).
    # `bathymetry:` used to be its own top-level section (domain/bathymetry
    # unification, pygetm-config) -- now it's domain: {method: BathymetryFile,
    # path: ..., ...}, same field, different location.
    domain_cfg = raw.get("domain")
    if domain_cfg and domain_cfg.get("method") == "BathymetryFile" and domain_cfg.get("path") and not os.path.isabs(domain_cfg["path"]):
        folder = os.getenv("BATHYMETRY_FOLDER", _DEFAULT_BATHYMETRY_FOLDER)
        domain_cfg["path"] = str(Path(folder) / domain_cfg["path"])

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
    if river_discharge.get("source") == "emorid":
        if not emorid_cfg.get("script"):
            emorid_cfg["script"] = f"{Path(__file__).parent / 'scripts' / 'rivers.py'}:add_rivers"
        # data_script (user request): the second half of river_discharge's
        # job -- real discharge data, mirroring cfg_rivers.py's own
        # create()/data() split. Same resolve-portably-at-run-time reasoning
        # as `script` above.
        if not emorid_cfg.get("data_script"):
            emorid_cfg["data_script"] = f"{Path(__file__).parent / 'scripts' / 'rivers.py'}:set_river_data"
        river_discharge["emorid"] = emorid_cfg
        raw["river_discharge"] = river_discharge

    # hydrography.<source>.data_script's own schema default (see
    # oceanicu_providers.py) isn't auto-injected either (same reasoning as
    # river_discharge.script/data_script above). "constant" hydrography
    # doesn't get one at all -- it's fully expressible as plain
    # data_assignments, no Python needed (see set_hydrography_ic's own
    # docstring).
    hydrography = raw.get("hydrography") or {}
    hydrography_source = hydrography.get("source")
    if hydrography_source in ("WOA", "CMEMS"):
        hydro_cfg = hydrography.get(hydrography_source) or {}
        if not hydro_cfg.get("data_script"):
            hydro_cfg["data_script"] = f"{Path(__file__).parent / 'scripts' / 'hydrography.py'}:set_hydrography_ic"
        hydrography[hydrography_source] = hydro_cfg
        raw["hydrography"] = hydrography

    # meteo.<source>.data_script's own schema default (see
    # oceanicu_providers.py) isn't auto-injected either (same reasoning as
    # river_discharge.script/data_script above). Unlike bathymetry/tpxo,
    # meteo's straightforward 1:1 file-read fields (u10/v10/t2m/qa-or-d2m/
    # sp/tp/tcc) genuinely differ by SOURCE, not just by file path -- CMIP6
    # uses `qa`/SPECIFIC_HUMIDITY, ERA5 uses `d2m`/DEW_POINT_TEMPERATURE,
    # real distinct TARGET fields, not just different data -- so a single
    # static data_assignments list in the YAML can't represent "pick one of
    # these two sets" the way a single scalar override can. Computed here
    # instead, mirroring cfg_airsea.py's own data() exactly (see
    # set_meteo_data's own docstring for the swr/ql CMIP6-only derived-flux
    # piece this does NOT cover -- ERA5's own swr/ql, when actually needed,
    # are each a single file read, real data_assignments entries below).
    meteo = raw.get("meteo") or {}
    meteo_source = meteo.get("source")
    meteo_cfg = (meteo.get(meteo_source) or {}) if meteo_source in ("ERA5", "CMIP6") else {}
    if meteo_source in ("ERA5", "CMIP6"):
        if not meteo_cfg.get("data_script"):
            meteo_cfg["data_script"] = f"{Path(__file__).parent / 'scripts' / 'meteo.py'}:set_meteo_data"
        meteo[meteo_source] = meteo_cfg
        raw["meteo"] = meteo

        _folder = Path(meteo_cfg["folder"])
        if meteo_cfg.get("folder_template"):
            _folder = _folder / meteo_cfg["folder_template"].format(
                model=meteo_cfg.get("model", ""), scenario=meteo_cfg.get("scenario", "")
            )

        if meteo_source == "ERA5":
            _meteo_assignments = [
                {"target": "simulation.airsea.t2m", "kind": "file", "file": str(_folder / "era5_t2m_????.nc"), "variable": "t2m", "pre_transform_offset": -273.15},
                {"target": "simulation.airsea.d2m", "kind": "file", "file": str(_folder / "era5_d2m_????.nc"), "variable": "d2m", "pre_transform_offset": -273.15},
                {"target": "simulation.airsea.u10", "kind": "file", "file": str(_folder / "era5_u10_????.nc"), "variable": "u10"},
                {"target": "simulation.airsea.v10", "kind": "file", "file": str(_folder / "era5_v10_????.nc"), "variable": "v10"},
                {"target": "simulation.airsea.sp", "kind": "file", "file": str(_folder / "era5_sp_????.nc"), "variable": "sp"},
                {"target": "simulation.airsea.tp", "kind": "file", "file": str(_folder / "era5_tp_????.nc"), "variable": "tp", "pre_transform_scale": 1 / 3600.0},
                {"target": "simulation.airsea.tcc", "kind": "file", "file": str(_folder / "era5_tcc_????.nc"), "variable": "tcc"},
            ]
        else:  # CMIP6
            # Real, verified data (2026-08-06): the actual bias-corrected
            # dataset (/data/BiasCorrected/CMIP6/<model>/<scenario>/meteo/,
            # re-interpolated onto ERA5's own grid/calendar/hourly
            # convention -- confirmed directly against a real sample file:
            # latitude/longitude coords matching ERA5 exactly, calendar
            # proleptic_gregorian -- NOT raw CMIP6's own noleap calendar/
            # native grid/coarser variable set) -- filenames are
            # "{var}_bc_bilinear__disagg_{year}.nc" (the "_disagg" variant
            # specifically -- ERA5-diurnal-cycle-disaggregated, confirmed
            # the one to use, not the coarser non-disagg file also present
            # per-variable). psl (sea-level pressure, not sp/surface
            # pressure) is used as an approximation for sp -- a real,
            # unresolved physical simplification for this shelf/coastal
            # domain, not something to silently treat as exact. evspsbl
            # (evaporation) exists in this dataset but is NOT wired in --
            # calculate_evaporation stays True (pygetm computes it
            # internally, same as the other sources) rather than assuming
            # evspsbl is a drop-in replacement without checking. tcc (cloud
            # cover) does NOT exist in this dataset at all -- constant
            # placeholder, same "required even if not used" reasoning as
            # ERA5/pygetm's own require_set() check, see set_meteo_data's
            # own docstring for why swr/ql are also left unset here (no
            # radiation data available yet either).
            _meteo_assignments = [
                {"target": "simulation.airsea.t2m", "kind": "file", "file": str(_folder / "tas_bc_bilinear__disagg_????.nc"), "variable": "tas", "pre_transform_offset": -273.15},
                {"target": "simulation.airsea.qa", "kind": "file", "file": str(_folder / "huss_bc_bilinear__disagg_????.nc"), "variable": "huss"},
                {"target": "simulation.airsea.u10", "kind": "file", "file": str(_folder / "uas_bc_bilinear__disagg_????.nc"), "variable": "uas"},
                {"target": "simulation.airsea.v10", "kind": "file", "file": str(_folder / "vas_bc_bilinear__disagg_????.nc"), "variable": "vas"},
                {"target": "simulation.airsea.sp", "kind": "file", "file": str(_folder / "psl_bc_bilinear__disagg_????.nc"), "variable": "psl"},
                {"target": "simulation.airsea.tp", "kind": "file", "file": str(_folder / "pr_bc_bilinear__disagg_????.nc"), "variable": "pr", "pre_transform_scale": 1 / 1000.0},
                {"target": "simulation.airsea.tcc", "kind": "constant", "constant_value": 0.5},
            ]
            # humidity_measure differs by source too (a real airsea
            # constructor param, not a data_assignments target -- see
            # oceanicu_providers.py's own comment on why it isn't modeled
            # as a schema field). Only set if not already overridden. `type`
            # must be set alongside it (airsea is a discriminated ChoiceSpec
            # -- FluxesFromMeteo | Fluxes -- humidity_measure alone is not a
            # valid config on its own) -- defaults to FluxesFromMeteo,
            # matching this schema's own default choice.
            _airsea_cfg = raw.setdefault("simulation", {}).setdefault("airsea", {})
            _airsea_cfg.setdefault("type", "FluxesFromMeteo")
            _airsea_cfg.setdefault("humidity_measure", "SPECIFIC_HUMIDITY")

        raw["data_assignments"] = _meteo_assignments + [
            a for a in raw.get("data_assignments", []) if not str(a.get("target", "")).startswith("simulation.airsea.")
        ]

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

    # post_data_script isn't part of pygetm-config's OWN schema at all (no
    # natural home for a single bare string -- see loader.run_post_data_script's
    # own docstring), unlike river_discharge.script (which piggybacks on the
    # ALREADY-schema-registered river_discharge role). Injected directly onto
    # the validated config dict here, not into `raw` before validate_config --
    # validate_config only ever keeps fields matching a real schema section,
    # so injecting into `raw` would just get it silently dropped. Placed
    # before the --print-config check below so it's visible there too, same
    # as river_discharge.script.
    config.setdefault("post_data_script", f"{Path(__file__).parent / 'scripts' / 'meteo.py'}:set_sst_proxy")

    if args.print_config:
        print(yaml.safe_dump(config, sort_keys=False))
        return 0

    if args.dump_python:
        from pygetm_config import codegen

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
    # scripts use (river_discharge.script, see pygetm_config.loader.
    # run_river_discharge_script) rather than calling add_rivers() directly,
    # so real execution here and the generated standalone script are
    # provably consistent, not two separately-maintained paths to the same
    # rivers. add_rivers() itself is unchanged and lives in scripts/rivers.py
    # -- river_discharge.emorid.script (resolved above) just points at it
    # by path.
    n_rivers = loader.run_river_discharge_script(domain, config)
    print(f"{n_rivers} river(s) added from {config['river_discharge']['file']}", file=sys.stderr)

    sim = loader.build_simulation(domain, config, schema)

    # Before apply_data_assignments, matching cfg_ic.py's own real placement
    # (before cfg_boundaries.data_2d/data_3d) in run_model.py's create_
    # simulation() exactly. load_restart= (user request, TODO item 21):
    # "shall only be done if not a restart".
    loader.run_hydrography_data_script(sim, domain, config, load_restart=args.load_restart)

    loader.apply_data_assignments(sim, domain, config, schema)

    # The second half of river_discharge's job (user request) -- real
    # discharge data, mirroring cfg_rivers.py's own create()/data() split.
    # Needs the LIVE sim.rivers collection, so this runs after
    # apply_data_assignments, same generic mechanism as the other two hooks
    # below -- river_discharge.emorid.data_script (resolved above) points at
    # set_river_data(), also in scripts/rivers.py.
    n_river_data = loader.run_river_discharge_data_script(sim, domain, config)
    print(f"{n_river_data} river(s) given real discharge data", file=sys.stderr)

    # Goes through the SAME generic mechanism --dump-python's generated
    # scripts use (post_data_script, see pygetm_config.loader.
    # run_post_data_script), same reasoning as run_river_discharge_script
    # above -- real execution and the generated script are provably
    # consistent, not two separately-maintained paths. set_sst_proxy() itself
    # is unchanged and lives in scripts/meteo.py -- post_data_script (resolved
    # above) just points at it by path.
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
