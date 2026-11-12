#!/usr/bin/env python
"""pygetm-config driver for OceanICU domain configs (NSe and any domain with
the same shape).

The Domain is built generically by pygetm_config.loader from the config's
`domain:` section; rivers, meteo, hydrography and FABM setup come from the
project hooks in scripts/*.py, and provider-derived data_assignments from
oceanicu_providers.py (registered below via PYGETM_CONFIG_PROVIDERS).

Runs the model directly, or with --dump-python writes a standalone script
(pygetm only, no pygetm-config) for the HPC. Needs pygetm-config installed
(`pip install -e ".[introspect]"` in the pygetm-config repo):

    python driver/oceanicu_driver.py NSe/config/nse_cmems.yaml --start 2024-03-01T00:00:00 --stop 2024-03-02T00:00:00
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


# Hook functions (rivers, meteo, hydrography, fabm) live in scripts/ and are
# loaded by "path/to/file.py:name", never imported here.


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--start", required=True, help="ISO 8601 start time (per-invocation, like OceanICU's own CLI convention)")
    parser.add_argument("--stop", required=True, help="ISO 8601 stop time")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--plot-domain",
        nargs="?",
        const=True,
        default=False,
        metavar="PREFIX",
        help="build the domain (open boundaries + rivers added, no Simulation), plot it "
        "(mesh, and mask -- both include river/open-boundary markers via domain.plot()'s "
        "own show_rivers/show_open_boundaries defaults), save the two PNGs, then exit -- "
        "before building the Simulation, so an unrelated land/sea-mask problem doesn't block it. "
        "Saves {PREFIX}_mesh.png and {PREFIX}_mask.png; PREFIX defaults to "
        "domain_<config file's stem>, or pass one to override.",
    )
    parser.add_argument(
        "--export-domain-summary",
        nargs="?",
        const=True,
        default=False,
        metavar="PREFIX",
        help="build the domain (same point as --plot-domain -- open boundaries + rivers "
        "added, no Simulation) and write the real T-grid bathymetry/mask/lon/lat, "
        "open-boundary point positions, and river name/lon/lat to {PREFIX}_summary.npz, "
        "then exit. For rendering with ocean-plot (real coastlines, controlled figure "
        "size/DPI, decluttered output) instead of pygetm's own Domain.plot() -- see "
        "ocean-post's generate_domain_overview_plot. Not together with --plot-domain.",
    )
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
        "a modified config is resolving the way you expect (e.g. after "
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
        "generated script has its own argparse CLI (-h shows it): --start/--stop/--dry-run/"
        "--load-restart/--load-restart-time/--save-restart/--skip-unavailable-output are "
        "runtime arguments of THAT script; --start/--stop given here become its defaults. "
        "Defaults to generated_<domain>.py, derived from the config "
        "file's own name, or pass a path.",
    )
    parser.add_argument(
        "--dump-python-style",
        choices=["functions", "flat"],
        default="functions",
        help="only relevant with --dump-python. 'functions' (default) structures the "
        "script into create_domain()/create_simulation(domain)/configure_output(sim)/"
        "run(sim)/main(), matching this project's own run_model.py shape. 'flat' emits "
        "the exact same statements as one top-to-bottom module-level sequence with no "
        "function defs -- easier to manually cut into/extend ad hoc, matching "
        "pygetm-config's own docs/getting_started.md non-OceanICU-shaped driver example.",
    )
    parser.add_argument(
        "--dump-python-full-config-yaml",
        action="store_true",
        help="only relevant with --dump-python. By default the companion *_config.yaml is "
        "TRIMMED to only the top-level keys an active script-hook function (meteo/"
        "hydrography/river_discharge's data_script, post_data_script) actually reads at the "
        "generated script's own runtime -- every other section (domain, simulation, output, "
        "runtime, ...) is already baked into literal Python in the generated .py itself, so "
        "editing it in the companion YAML silently has no effect. Pass this flag to get the "
        "old, untrimmed, full-config companion file instead (e.g. for archival/diffing "
        "against the source config).",
    )
    parser.add_argument("--load-restart", default=None, metavar="PATH", help="resume from a restart file; overrides runtime.time with the restart's own time")
    parser.add_argument(
        "--load-restart-time", default=None, metavar="ISO8601",
        help="only relevant with --load-restart. Which internal snapshot to resume from, for a "
        "restart file that holds more than one (e.g. written with add_restart(interval=...) "
        "instead of the default single end-of-run snapshot) -- must exactly match one of the "
        "file's own time coordinate values, or sim.load_restart() raises listing the file's "
        "actual span. Omit to use the last snapshot in the file (sim.load_restart()'s own "
        "default), same behavior as before this flag existed.",
    )
    parser.add_argument("--save-restart", default=None, metavar="PATH", help="write a restart file for this run")
    parser.add_argument(
        "--save-restart-interval", type=float, default=None, metavar="N",
        help="only relevant with --save-restart. How often to write a snapshot, in "
        "--save-restart-interval-units; overrides runtime.save_restart_interval. Omit (and "
        "leave the config unset) for pygetm's own default (one snapshot, at the end of this "
        "invocation).",
    )
    parser.add_argument(
        "--save-restart-interval-units", default=None, metavar="UNIT",
        help="only relevant with --save-restart-interval. One of pygetm.output.TimeUnit's own "
        "member names: TIMESTEPS/SECONDS/MINUTES/HOURS/DAYS/MONTHS/YEARS; overrides "
        "runtime.save_restart_interval_units. Default TIMESTEPS if an interval is given but "
        "this is not.",
    )
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

    # Before anything pygetm-related: a log call made before pyGETM configures
    # the root logger would be dropped (see pygetm_config.loader "Debugging").
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s:%(name)s:%(message)s")

    # Before any file access: fills os.environ for ${VAR} data paths.
    loader.apply_data_roots(args.data_root, args.data_roots_file)

    # Register oceanicu_providers.py before build_schema() reads the env var;
    # it sets PYGETM_CONFIG_DATA_ASSIGNMENTS_DERIVERS itself. The pygetm-free
    # TUI/web (prebuilt schema.json) still need that exported explicitly --
    # see driver/README.md.
    os.environ.setdefault(
        "PYGETM_CONFIG_PROVIDERS",
        f"{Path(__file__).resolve().parent / 'oceanicu_providers.py'}:register_oceanicu_providers",
    )

    # Default to this checkout's own scripts/ and repo root; an explicit
    # --data-root/roots-file wins. Configs refer to "${SCRIPT_FOLDER}/...",
    # so generated artifacts stay portable.
    os.environ.setdefault("SCRIPT_FOLDER", str(Path(__file__).resolve().parent / "scripts"))
    os.environ.setdefault("GOTM_FOLDER", str(Path(__file__).resolve().parent.parent))

    schema = build_schema()

    with open(args.config) as f:
        raw = yaml.safe_load(f)

    # validate_config never fills schema defaults, so set the hook targets
    # here when the YAML doesn't; ${SCRIPT_FOLDER} keeps them portable.
    river_discharge = raw.get("river_discharge") or {}
    emorid_cfg = river_discharge.get("emorid") or {}
    if river_discharge.get("source") == "emorid":
        if not emorid_cfg.get("script"):
            emorid_cfg["script"] = "${SCRIPT_FOLDER}/rivers.py:add_rivers"
        # The data half of river_discharge (as cfg_rivers.py's create()/data()).
        if not emorid_cfg.get("data_script"):
            emorid_cfg["data_script"] = "${SCRIPT_FOLDER}/rivers.py:set_river_data"
        river_discharge["emorid"] = emorid_cfg
        raw["river_discharge"] = river_discharge

    # simulation.gotm: a bare filename, resolved relative to the run
    # directory, which keeps its own copy (like fabm.yaml).
    if not (raw.get("simulation") or {}).get("gotm"):
        raw.setdefault("simulation", {})["gotm"] = "gotm.yaml"

    # Default hydrography.<source>.data_script ("constant" needs none).
    hydrography = raw.get("hydrography") or {}
    hydrography_source = hydrography.get("source")
    if hydrography_source in ("WOA", "CMEMS"):
        hydro_cfg = hydrography.get(hydrography_source) or {}
        if not hydro_cfg.get("data_script"):
            hydro_cfg["data_script"] = "${SCRIPT_FOLDER}/hydrography.py:set_hydrography_ic"
        hydrography[hydrography_source] = hydro_cfg
        raw["hydrography"] = hydrography

    # boundaries.* values come from oceanicu_providers.derive_data_assignments
    # (PYGETM_CONFIG_DATA_ASSIGNMENTS_DERIVERS), for every entrypoint.

    # Default meteo.<source>.data_script; the 1:1 meteo file reads come from
    # oceanicu_providers.derive_data_assignments.
    meteo = raw.get("meteo") or {}
    meteo_source = meteo.get("source")
    if meteo_source in ("ERA5", "CMIP6", "CMIP6-raw"):
        meteo_cfg = meteo.get(meteo_source) or {}
        if not meteo_cfg.get("data_script"):
            meteo_cfg["data_script"] = "${SCRIPT_FOLDER}/meteo.py:set_meteo_data"
        meteo[meteo_source] = meteo_cfg
        raw["meteo"] = meteo

        # Shared meteo params go into the active simulation.airsea.<type>
        # sub-dict (nested-by-label ChoiceSpec shape); setdefault, so an
        # explicit simulation.airsea block wins.
        _airsea_cfg = raw.setdefault("simulation", {}).setdefault("airsea", {})
        _airsea_cfg.setdefault("type", "FluxesFromMeteo")
        _airsea_type_cfg = _airsea_cfg.setdefault(_airsea_cfg["type"], {})
        # Always derived from the source: ERA5 provides d2m, CMIP6 huss.
        _airsea_type_cfg["humidity_measure"] = (
            "DEW_POINT_TEMPERATURE" if meteo_source == "ERA5" else "SPECIFIC_HUMIDITY"
        )
        if meteo_cfg.get("evaporation") is not None:
            _airsea_type_cfg.setdefault("calculate_evaporation", meteo_cfg["evaporation"])
    elif meteo_source == "Fluxes":
        # meteo.source Fluxes needs simulation.airsea.type Fluxes.
        raw.setdefault("simulation", {}).setdefault("airsea", {}).setdefault("type", "Fluxes")

    # Default fabm.ERSEM.data_script. simulation.fabm itself comes from
    # oceanicu_providers.derive_config_value_overrides (every entrypoint).
    fabm = raw.get("fabm") or {}
    fabm_source = fabm.get("source")
    if fabm_source == "ERSEM":
        fabm_cfg = fabm.get(fabm_source) or {}
        if not fabm_cfg.get("data_script"):
            fabm_cfg["data_script"] = "${SCRIPT_FOLDER}/fabm.py:configure_fabm"
        fabm[fabm_source] = fabm_cfg
        raw["fabm"] = fabm

    # The fabm_mizer output group expansion is done by oceanicu_providers.
    # derive_config_value_overrides, for every entrypoint.

    raw.setdefault("runtime", {})["time"] = args.start

    # runtime.debug_output (schema-only flag): every output file that
    # requests a group with a *_debug counterpart also gets that counterpart.
    if bool(raw.get("runtime", {}).get("debug_output", False)):
        _debug_group_for = {
            "barotropic_2d": "barotropic_2d_debug",
            "barotropic_3d": "barotropic_3d_debug",
            "baroclinic_3d": "baroclinic_3d_debug",
        }
        for file_entry in raw.get("output", {}).get("files", []):
            _referenced = {g for req in file_entry.get("variable_requests", []) for g in (req.get("groups") or [])}
            _debug_groups = [_debug_group_for[g] for g in _referenced if g in _debug_group_for]
            if _debug_groups:
                file_entry.setdefault("variable_requests", []).append({"groups": _debug_groups})

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

    # Default post_data_script (sets sim.sst; redundant with pygetm-config's
    # own non-BAROCLINIC sst handling, kept as a harmless default).
    config.setdefault("runtime", {}).setdefault("post_data_script", "${SCRIPT_FOLDER}/meteo.py:set_sst_proxy")

    # runtime.stop is not a schema field, so it goes into the validated
    # config: script hooks (set_meteo_data, configure_fabm) read the run's stop.
    config.setdefault("runtime", {})["stop"] = args.stop

    if args.print_config:
        print(yaml.safe_dump(config, sort_keys=False))
        return 0

    if args.dump_python:
        from pygetm_config import codegen

        if isinstance(args.dump_python, str):
            out_path = args.dump_python
        else:
            # Same default name as the TUI/web "Generate script".
            out_path = codegen.default_generated_script_path(args.config)
        config_yaml_path = str(Path(out_path).with_name(Path(out_path).stem + "_config.yaml"))
        # Boilerplate and embedded hook bodies go to a companion _utils.py.
        utils_module_path = str(Path(out_path).with_name(Path(out_path).stem + "_utils.py"))
        script = codegen.generate_script(
            config,
            schema,
            stop=args.stop,
            # Never forward --dry-run: it would become the generated script's default.
            dry_run=False,
            load_restart=args.load_restart,
            save_restart=args.save_restart,
            skip_unavailable_output=args.skip_unavailable_output,
            config_yaml_path=config_yaml_path,
            trim_config_yaml=not args.dump_python_full_config_yaml,
            utils_module_path=utils_module_path,
            style=args.dump_python_style,
        )
        with open(out_path, "w") as f:
            f.write(script)
        print(f"wrote {out_path}", file=sys.stderr)
        print(f"wrote {utils_module_path}", file=sys.stderr)
        print(f"wrote {config_yaml_path}", file=sys.stderr)

        out_dir = Path(out_path).resolve().parent
        # Shared with the TUI/web "Generate script".
        copied = codegen.copy_companion_files(config, out_dir)
        for name in copied:
            print(f"copied companion file: {name}", file=sys.stderr)
        return 0

    domain = loader.build_domain(config, schema)
    print(f"domain built: {domain.nx} x {domain.ny}", file=sys.stderr)

    # Order: open boundaries -> cfl_check -> rivers.
    loader.add_open_boundaries(domain, config, schema)
    domain.cfl_check()

    # Same hook mechanism as the generated scripts (river_discharge.script).
    n_rivers = loader.run_river_discharge_script(domain, config)
    print(f"{n_rivers} river(s) added", file=sys.stderr)

    # --plot-domain plots before build_simulation(): it only needs `domain`,
    # and must not be blocked by an open-boundary/mask problem.
    if args.plot_domain and args.export_domain_summary:
        parser.error("--plot-domain and --export-domain-summary are mutually exclusive -- run twice")
    if args.plot_domain:
        # domain.plot() validates that open-boundary points are on water;
        # allow_on_land downgrades that to a warning, for this plot only.
        domain.open_boundaries.allow_on_land = True
        prefix = args.plot_domain if isinstance(args.plot_domain, str) else f"domain_{Path(args.config).stem}"
        # Grid shape rather than a mean spacing: the grid needn't be uniform.
        extent = (
            f"{domain.ny} × {domain.nx} grid points"
            f"  ·  lon [{float(domain.lon.min()):.2f}, {float(domain.lon.max()):.2f}]°E"
            f"  ·  lat [{float(domain.lat.min()):.2f}, {float(domain.lat.max()):.2f}]°N"
        )
        fig = domain.plot(show_mesh=True)
        if fig is not None:
            fig.suptitle(f"{Path(args.config).stem}\n{extent}")
            fig.savefig(f"{prefix}_mesh.png")
            print(f"wrote {prefix}_mesh.png", file=sys.stderr)
        fig = domain.plot(show_mesh=False, show_mask=True)
        if fig is not None:
            fig.suptitle(f"{Path(args.config).stem}\n{extent}")
            fig.savefig(f"{prefix}_mask.png")
            print(f"wrote {prefix}_mask.png", file=sys.stderr)
        return 0

    if args.export_domain_summary:
        # Same early exit and allow_on_land as --plot-domain; rendering is
        # done by ocean-post's generate_domain_overview_plot.
        import numpy as np
        from pygetm import core
        from pygetm.open_boundaries import Side

        domain.open_boundaries.allow_on_land = True
        prefix = (
            args.export_domain_summary
            if isinstance(args.export_domain_summary, str)
            else f"domain_{Path(args.config).stem}"
        )

        final_mask = domain.get_final_mask()
        t_mask = final_mask[1::2, 1::2]
        t_lon = domain.lon[1::2, 1::2]
        t_lat = domain.lat[1::2, 1::2]
        t_H = domain.H[1::2, 1::2]

        # l/mstart/mstop are 0-based T-grid indices, so they index t_lon/t_lat,
        # not the supergrid domain.lon/lat.
        bdy_lon, bdy_lat = [], []
        for b in domain.open_boundaries._boundaries:
            for m in range(b.mstart, b.mstop, b.mstep or 1):
                j, i = (m, b.l) if b.side in (Side.LEFT, Side.RIGHT) else (b.l, m)
                bdy_lon.append(float(t_lon[j, i]))
                bdy_lat.append(float(t_lat[j, i]))

        # River positions: as Domain.plot()'s show_rivers (supergrid index 1+2*i).
        domain.rivers.map_to_grid(core.Locator(**domain._tcoords(mask=final_mask)))
        river_lon, river_lat, river_name = [], [], []
        for name, river in domain.rivers.items():
            i_sup, j_sup = 1 + river.i * 2, 1 + river.j * 2
            river_lon.append(float(domain.lon[j_sup, i_sup]))
            river_lat.append(float(domain.lat[j_sup, i_sup]))
            river_name.append(name)

        out_path = f"{prefix}_summary.npz"
        np.savez(
            out_path,
            area=Path(args.config).stem,
            lon=t_lon, lat=t_lat, H=t_H, mask=t_mask,
            boundary_lon=np.array(bdy_lon), boundary_lat=np.array(bdy_lat),
            river_lon=np.array(river_lon), river_lat=np.array(river_lat),
            river_name=np.array(river_name),
        )
        print(f"wrote {out_path}", file=sys.stderr)
        return 0

    sim = loader.build_simulation(domain, config, schema)

    # Before apply_data_assignments; skipped when resuming from a restart.
    loader.run_hydrography_data_script(sim, domain, config, load_restart=args.load_restart)

    loader.apply_data_assignments(sim, domain, config, schema)

    # Discharge data needs the live sim.rivers, so after apply_data_assignments.
    n_river_data = loader.run_river_discharge_data_script(sim, domain, config)
    print(f"{n_river_data} river(s) given real discharge data", file=sys.stderr)

    # Same hook mechanism as the generated scripts (runtime.post_data_script).
    loader.run_post_data_script(sim, domain, config)

    loader.configure_output(
        sim, config, schema, skip_unavailable_fields=args.skip_unavailable_output, load_restart=args.load_restart
    )

    # As cli.py's --load-restart/--save-restart: add_restart() before
    # sim.start(); load_restart() returns the time to start from (the
    # restart's, or --load-restart-time's snapshot).
    if args.save_restart:
        restart_kwargs: dict = {}
        _interval = args.save_restart_interval
        if _interval is None:
            _interval = config.get("runtime", {}).get("save_restart_interval")
        if _interval is not None:
            restart_kwargs["interval"] = _interval
            _units = args.save_restart_interval_units
            if _units is None:
                _units = config.get("runtime", {}).get("save_restart_interval_units", "TIMESTEPS")
            from pygetm.output import TimeUnit

            restart_kwargs["interval_units"] = (
                TimeUnit(_units) if isinstance(_units, int) else getattr(TimeUnit, _units)
            )
        sim.output_manager.add_restart(args.save_restart, **restart_kwargs)
    start_kwargs = loader.build_start_kwargs(config, schema)
    if args.load_restart:
        restart_time = None
        if args.load_restart_time:
            restart_time = datetime.datetime.fromisoformat(args.load_restart_time)
        start_kwargs["time"] = sim.load_restart(args.load_restart, time=restart_time)
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
