#!/usr/bin/env python
"""General-purpose pygetm-config driver for OceanICU domain configs (NSe,
and any future domain that follows the same schema-validated shape) --
NOT NSe-specific despite its origin as "nse_driver.py"; renamed and moved
here (out of the old pygetm_config/ directory, which collided with the
actual `pygetm_config` PACKAGE name) so this general-purpose driver/
oceanicu_providers.py/scripts/ trio lives separately from any one domain's
own config, which belongs under that domain's own config directory instead
(e.g. NSe/config/nse_from_oceanicu.yaml). Demonstrates the composable
pattern from pygetm-config's docs/yaml_vs_python.md: the Domain itself is
built generically by pygetm_config.loader from the config's `domain:`
section (schema-validated, one choice among several -- see
schema._build_bathymetry_file_choice), since reading a pre-prepared
bathymetry file turned out to need only variable names and a mask
convention, not bespoke code. Rivers, by contrast, genuinely stay bespoke,
project-specific Python here (mirroring OceanICU's real cfg_rivers.py,
verified against that source): they're dynamic and threshold-filtered from
an EMORID file at run time, not a static list.

This is a REFERENCE / illustrative script -- it needs a real bathymetry NetCDF
(referenced by the config's own `domain:` section) and a real EMORID
river-discharge NetCDF to actually run (paths taken from the config's
`river_discharge:` section, which IS schema-validated -- see
oceanicu_providers.py, registered automatically below via
PYGETM_CONFIG_PROVIDERS). Run in a pygetm-capable environment with
pygetm-config installed there too (`pip install -e ".[introspect]"` from the
pygetm-config repo) -- no sys.path hacks, this script has no dependency on
being located near the pygetm-config repo, only on pygetm_config being
importable:

    python driver/oceanicu_driver.py NSe/config/nse_from_oceanicu.yaml --start 2024-03-01T00:00:00 --stop 2024-03-02T00:00:00
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



# The five data_script/script/post_data_script target functions that used to
# live in this file directly (add_rivers/set_river_data/set_meteo_data/
# set_sst_proxy/set_hydrography_ic) now live in scripts/ -- one file per
# provider role, grouped by role rather than fully atomized (river position +
# data stay together, see oceanicu_providers.py's own comment on why), since
# this file itself was becoming a grab-bag of unrelated per-role logic.
# Nothing about HOW they're loaded changed: still load_dotted_target'd via
# "path/to/file.py:name" (see scripts/rivers.py, scripts/meteo.py,
# scripts/hydrography.py), never imported directly here -- see those files'
# own module docstrings.

# meteo.<source>.shortwave_method/longwave_method (oceanicu_providers.py's
# own _meteo_shared) are plain ints (matching cfg_airsea.py's own real -1/
# -2/1 literals) -- but simulation.airsea.shortwave_method/longwave_method
# (the REAL pygetm.airsea.FluxesFromMeteo constructor params that actually
# control construction) are schema-typed as an ENUM of string names: a
# synthesized "sentinel_overlay" enum, airsea.shortwave_method_or_sentinel/
# longwave_method_or_sentinel, wrapping pygetm.airsea.ShortwaveMethod/
# awex.LongwaveMethod plus the NET_FLUX(-1)/DOWNWARD_FLUX(-2) sentinels.
# These two dicts translate the int value into the enum member name the
# schema actually needs; kept as a name->name map (not the raw int) so this
# stays correct even if pygetm ever renumbers the underlying enum values.
_SHORTWAVE_METHOD_NAMES = {1: "ROSATI_MIYAKODA", -1: "NET_FLUX", -2: "DOWNWARD_FLUX"}
_LONGWAVE_METHOD_NAMES = {
    1: "CLARK", 2: "HASTENRATH_LAMB", 3: "BIGNAMI", 4: "BERLIAND_BERLIAND",
    5: "JOSEY1", 6: "JOSEY2", -1: "NET_FLUX", -2: "DOWNWARD_FLUX",
}


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
        help="build the domain and Simulation, plot the domain (mesh+subdomains+tiling, and "
        "mask), save the two PNGs, then exit -- before any hydrography/data-assignment/river-"
        "data loading. Mirrors run_model.py's own --plot_domain exactly (both figures, same "
        "show_mesh/show_subdomains/tiling and show_mask calls to domain.plot(), same early-"
        "exit-before-data placement), richer than pygetm-config run's plain --plot-domain "
        "(which only calls domain.plot() with no args and doesn't need a built Simulation). "
        "Saves {PREFIX}_mesh.png and {PREFIX}_mask.png; PREFIX defaults to "
        "domain_<config file's stem>, or pass one to override.",
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
        "at generation time. Defaults to generated_<domain>.py, derived from the config "
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

    # Before any real file access -- populates os.environ for whatever
    # ${VAR}/$VAR data-path references a config uses (see loader.
    # resolve_data_path). Every domain config's own domain.path/tpxo_folder/
    # hydrography.folder/river_discharge.folder/meteo.folder uses ${VAR}
    # syntax, resolved lazily at the point of actual use, not baked in here.
    loader.apply_data_roots(args.data_root, args.data_roots_file)

    # Auto-register oceanicu_providers.py (this directory) via the zero-
    # packaging PYGETM_CONFIG_PROVIDERS env var (see pygetm-config's
    # docs/providers.md) -- setdefault, not a hard override, so a caller can
    # still point at a different registry explicitly if they want. Must
    # happen BEFORE build_schema(), which is what actually reads this env var
    # (via providers.discover_provider_sections()).
    # PYGETM_CONFIG_DATA_ASSIGNMENTS_DERIVERS is NOT set here separately --
    # register_oceanicu_providers() itself sets it (as a side effect, the
    # moment build_schema() below resolves this PYGETM_CONFIG_PROVIDERS
    # entry), so THIS process (direct run / --dump-python) doesn't need a
    # second explicit setdefault call here. Does NOT reach `pygetm-config
    # edit --schema dist/schema.json`/`web` -- that pygetm-free workflow
    # never calls build_schema()/register_oceanicu_providers() at all (a
    # pre-built schema.json is the whole point), so a real TUI/web launch
    # still needs PYGETM_CONFIG_DATA_ASSIGNMENTS_DERIVERS exported
    # explicitly -- see driver/README.md's own TUI launch instructions.
    os.environ.setdefault(
        "PYGETM_CONFIG_PROVIDERS",
        f"{Path(__file__).parent / 'oceanicu_providers.py'}:register_oceanicu_providers",
    )

    # SCRIPT_FOLDER/GOTM_FOLDER, same setdefault-after-apply_data_roots
    # pattern as PYGETM_CONFIG_PROVIDERS above -- an explicit --data-root/
    # roots-file override (already applied by apply_data_roots) wins;
    # otherwise default to THIS checkout's own real locations, so a plain
    # `oceanicu_driver.py run` works out of the box with zero configuration.
    # The config fields themselves (below) are written as
    # "${SCRIPT_FOLDER}/rivers.py:add_rivers" etc, not a frozen
    # Path(__file__)-relative absolute string, so the frozen artifacts
    # --dump-python produces (generated_config.yaml, and simulation.gotm's
    # own resolve_data_path(...) call embedded live in the generated script)
    # stay portable if the pair is later copied to a different machine or
    # checkout location -- a $VAR template survives that round-trip the same
    # way data files do (loader.resolve_data_path, and providers.
    # load_dotted_target for .script/.data_script/post_data_script targets).
    os.environ.setdefault("SCRIPT_FOLDER", str(Path(__file__).parent / "scripts"))
    os.environ.setdefault("GOTM_FOLDER", str(Path(__file__).parent.parent))

    schema = build_schema()

    with open(args.config) as f:
        raw = yaml.safe_load(f)

    # river_discharge.emorid.script's own schema default (see
    # oceanicu_providers.py, computed from THAT file's own __file__) isn't
    # auto-injected by validate_config -- it only ever keeps fields
    # EXPLICITLY present in the raw YAML, never fills in unset ones (a
    # schema `default` is template/TUI-display-only). Set it here explicitly
    # if the YAML doesn't override it, so a domain config itself doesn't
    # need a machine-specific absolute path baked in. Written as
    # "${SCRIPT_FOLDER}/rivers.py:..." rather than a frozen
    # Path(__file__)-relative absolute string, matching the SCRIPT_FOLDER/
    # GOTM_FOLDER setdefault above. Only "emorid" today (the one real
    # registered source, see oceanicu_providers.py's own river_discharge
    # role).
    river_discharge = raw.get("river_discharge") or {}
    emorid_cfg = river_discharge.get("emorid") or {}
    if river_discharge.get("source") == "emorid":
        if not emorid_cfg.get("script"):
            emorid_cfg["script"] = "${SCRIPT_FOLDER}/rivers.py:add_rivers"
        # The second half of river_discharge's job: real discharge data,
        # mirroring cfg_rivers.py's own create()/data() split. Same $VAR
        # reasoning as `script` above.
        if not emorid_cfg.get("data_script"):
            emorid_cfg["data_script"] = "${SCRIPT_FOLDER}/rivers.py:set_river_data"
        river_discharge["emorid"] = emorid_cfg
        raw["river_discharge"] = river_discharge

    # simulation.gotm: run_model.py always passes gotm=Path("gotm.yaml") to
    # Simulation() -- a real, non-trivial GOTM turbulence-closure config
    # (turb_method/tke_method/len_scale_method/stab_method/turb_param), not
    # pyGETM's own internal k-epsilon defaults. gotm.yaml is a fixed project
    # asset (lives at the oceanicu_3d repo root, one level up from this
    # file). `simulation.gotm` is already a real schema `path`-kind field,
    # so writing "${GOTM_FOLDER}/gotm.yaml" here is enough on its own --
    # loader._coerce_value already calls resolve_data_path on it.
    if not (raw.get("simulation") or {}).get("gotm"):
        raw.setdefault("simulation", {})["gotm"] = "${GOTM_FOLDER}/gotm.yaml"

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
            hydro_cfg["data_script"] = "${SCRIPT_FOLDER}/hydrography.py:set_hydrography_ic"
        hydrography[hydrography_source] = hydro_cfg
        raw["hydrography"] = hydrography

    # boundaries.baroclinic's 3D temp/salt boundary VALUES (WOA vs CMEMS,
    # differing folder/variable/on_grid/climotology shape) are now computed
    # by oceanicu_providers.derive_data_assignments, registered above via
    # PYGETM_CONFIG_DATA_ASSIGNMENTS_DERIVERS -- loader.apply_data_assignments/
    # codegen._emit_data_assignments both call it automatically (see that
    # function's own docstring for why this moved out of main(): a
    # setdefault-style injection HERE only ever helped driver runs, never
    # TUI/web's own 'Generate script', which doesn't run main() at all).

    # meteo.<source>.data_script's own schema default (see
    # oceanicu_providers.py) isn't auto-injected either (same reasoning as
    # river_discharge.script/data_script above). meteo's own 1:1 file-read
    # fields (u10/v10/t2m/qa-or-d2m/sp/tp/tcc/swr/ql) are now computed by
    # oceanicu_providers.derive_data_assignments (see that function's own
    # docstring, and the matching comment on boundaries.baroclinic above).
    meteo = raw.get("meteo") or {}
    meteo_source = meteo.get("source")
    meteo_cfg = (meteo.get(meteo_source) or {}) if meteo_source in ("ERA5", "CMIP6") else {}
    if meteo_source in ("ERA5", "CMIP6"):
        if not meteo_cfg.get("data_script"):
            meteo_cfg["data_script"] = "${SCRIPT_FOLDER}/meteo.py:set_meteo_data"
        meteo[meteo_source] = meteo_cfg
        raw["meteo"] = meteo

        # Propagate meteo.<source>'s shared params into the REAL airsea
        # constructor config (see the _SHORTWAVE_METHOD_NAMES/
        # _LONGWAVE_METHOD_NAMES module comment above for the int-to-enum
        # translation this needs). `type` must be set alongside these
        # (airsea is a discriminated ChoiceSpec -- FluxesFromMeteo |
        # Fluxes) -- defaults to FluxesFromMeteo, matching this schema's
        # own default choice. setdefault, not overwrite: a config that
        # already has its own simulation.airsea.* block wins. Written into
        # the ACTIVE label's own nested sub-dict (`_airsea_type_cfg`), not
        # flat onto `_airsea_cfg` itself -- every ChoiceSpec in the schema
        # uses the nested-by-label shape (every alternative kept under its
        # own <label>: key, see yaml_parse.py), so a config that already has
        # simulation.airsea.FluxesFromMeteo: {...} persisted (the TUI/web-
        # editable form) must get these setdefault fills in THAT same
        # sub-dict, not as stray flat keys alongside it -- which
        # yaml_parse._validate_choice correctly rejects as unknown fields.
        _airsea_cfg = raw.setdefault("simulation", {}).setdefault("airsea", {})
        _airsea_cfg.setdefault("type", "FluxesFromMeteo")
        _airsea_type_cfg = _airsea_cfg.setdefault(_airsea_cfg["type"], {})
        # Always derived from meteo_source, never left to a config value --
        # ERA5 only provides d2m (dewpoint), CMIP6 only provides huss
        # (specific humidity), so an explicit humidity_measure in the YAML
        # that disagrees with meteo_source is always wrong, not a valid
        # override (e.g. a config copy-pasted from an ERA5 setup, still
        # reading DEW_POINT_TEMPERATURE, silently mismatched against qa
        # being populated from huss). Mirrors cfg_airsea.py's own
        # unconditional derivation from cfg.meteo.source.
        _airsea_type_cfg["humidity_measure"] = (
            "DEW_POINT_TEMPERATURE" if meteo_source == "ERA5" else "SPECIFIC_HUMIDITY"
        )
        if meteo_cfg.get("shortwave_method") is not None:
            _airsea_type_cfg.setdefault(
                "shortwave_method", _SHORTWAVE_METHOD_NAMES[meteo_cfg["shortwave_method"]]
            )
        if meteo_cfg.get("longwave_method") is not None:
            _airsea_type_cfg.setdefault(
                "longwave_method", _LONGWAVE_METHOD_NAMES[meteo_cfg["longwave_method"]]
            )
        if meteo_cfg.get("evaporation") is not None:
            _airsea_type_cfg.setdefault("calculate_evaporation", meteo_cfg["evaporation"])

    # fabm.<source>'s own schema default (fabm.data_script) isn't
    # auto-injected either (same reasoning as river_discharge.script/
    # data_script/meteo.data_script above). Propagate fabm.<source>.file
    # into simulation.fabm (pygetm.Simulation's own fabm= constructor
    # kwarg, see run_model.py's `fabm=cfg.fabm.file`) -- setdefault, not
    # overwrite: a config that already sets simulation.fabm directly wins.
    # A config with no `fabm:` section at all is unaffected (simulation.fabm
    # stays unset/false, matching current behavior -- FABM off by default).
    fabm = raw.get("fabm") or {}
    fabm_source = fabm.get("source")
    if fabm_source == "ERSEM":
        fabm_cfg = fabm.get(fabm_source) or {}
        if not fabm_cfg.get("data_script"):
            fabm_cfg["data_script"] = "${SCRIPT_FOLDER}/fabm.py:configure_fabm"
        fabm[fabm_source] = fabm_cfg
        raw["fabm"] = fabm

        if fabm_cfg.get("file"):
            raw.setdefault("simulation", {}).setdefault("fabm", fabm_cfg["file"])

    raw.setdefault("runtime", {})["time"] = args.start

    # runtime.debug_output (SCHEMA-ONLY flag, see pygetm-config's own
    # _build_runtime_section -- not a real pygetm parameter): matches
    # cfg_output.py's real cfg.runtime.debug_output gating exactly, but
    # expressed as a per-run toggle here rather than a static YAML choice.
    # cfg_output.py ADDS the debug fields to the SAME output file that
    # already carries the matching non-debug group (never its own separate
    # file) -- so this walks output.files, and for any file whose
    # variable_requests already reference a *_debug-having group (barotropic_
    # 2d/barotropic_3d/baroclinic_3d), appends a new variable_requests entry
    # for that group's *_debug counterpart. Driven entirely by which groups a
    # file already references, not by hardcoded filenames -- stays correct
    # if nse_from_oceanicu.yaml's own file layout changes.
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

    # post_data_script now has a real schema home at runtime.post_data_script
    # (pygetm-config's schema.py::_build_runtime_section) -- injected here,
    # not into `raw` before validate_config, purely so a config missing it
    # still gets this default even though validate_config already ran
    # (validate_config itself would happily carry a YAML-provided value at
    # this same path). Now largely redundant for OceanICU's own use --
    # pygetm-config's core apply_data_assignments/_emit_data_assignments
    # already set sim.sst automatically for non-BAROCLINIC runs -- but kept
    # as a harmless belt-and-suspenders default; set_sst_proxy just re-does
    # the same assignment.
    config.setdefault("runtime", {}).setdefault("post_data_script", "${SCRIPT_FOLDER}/meteo.py:set_sst_proxy")

    if args.print_config:
        print(yaml.safe_dump(config, sort_keys=False))
        return 0

    if args.dump_python:
        from pygetm_config import codegen

        if isinstance(args.dump_python, str):
            out_path = args.dump_python
        else:
            # Same shared naming convention as the TUI/web frontends use
            # (codegen.default_generated_script_path) -- NOT a hand-rolled
            # duplicate: this used to strip a "_from_oceanicu" suffix that
            # default_generated_script_path doesn't, producing a DIFFERENT
            # default filename (generated_nse.py) than TUI/web's own
            # Ctrl+G/generate button (generated_nse_from_oceanicu.py) for
            # the exact same config -- the three generation modes must
            # agree, including on this.
            out_path = codegen.default_generated_script_path(args.config)
        config_yaml_path = str(Path(out_path).with_name(Path(out_path).stem + "_config.yaml"))
        # Argparse/config-loading/script-hook boilerplate goes to a
        # companion "_utils.py" module -- a user only needs to see the main
        # generated file's own pyGETM call sequence, not the rivers.py/
        # hydrography.py/meteo.py hook bodies embedded inline in the way.
        utils_module_path = str(Path(out_path).with_name(Path(out_path).stem + "_utils.py"))
        script = codegen.generate_script(
            config,
            schema,
            stop=args.stop,
            # NOT args.dry_run -- --dump-python never builds/runs anything
            # itself, so THIS invocation's --dry-run is inert; naively
            # forwarding it would bake "dry-run-only forever" into the
            # GENERATED script's own default the moment someone dry-runs
            # generation once, even though they fully intend to give it a
            # real --stop and run it for real later (see codegen.
            # generate_script's own docstring for the full reasoning).
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
        return 0

    domain = loader.build_domain(config, schema)
    print(f"domain built: {domain.nx} x {domain.ny}", file=sys.stderr)

    # From here on, entirely generic -- driven by the schema/config like any
    # other pyGETM setup, regardless of how `domain` was actually built above.
    # Order matters and matches run_model.py's own create_domain() exactly:
    # open boundaries -> domain.cfl_check() -> rivers.
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

    if args.plot_domain:
        prefix = args.plot_domain if isinstance(args.plot_domain, str) else f"domain_{Path(args.config).stem}"
        fig = domain.plot(show_mesh=True, show_subdomains=True, tiling=sim.tiling)
        if fig is not None:
            fig.savefig(f"{prefix}_mesh.png")
            print(f"wrote {prefix}_mesh.png", file=sys.stderr)
        fig = domain.plot(show_mesh=False, show_mask=True)
        if fig is not None:
            fig.savefig(f"{prefix}_mask.png")
            print(f"wrote {prefix}_mask.png", file=sys.stderr)
        return 0

    # Before apply_data_assignments, matching cfg_ic.py's own placement
    # (before cfg_boundaries.data_2d/data_3d) in run_model.py's create_
    # simulation(). load_restart= is only applied when not resuming from a
    # restart -- a restart already has its own real initial condition.
    loader.run_hydrography_data_script(sim, domain, config, load_restart=args.load_restart)

    loader.apply_data_assignments(sim, domain, config, schema)

    # The second half of river_discharge's job -- real discharge data,
    # mirroring cfg_rivers.py's own create()/data() split. Needs the LIVE
    # sim.rivers collection, so this runs after apply_data_assignments,
    # same generic mechanism as the other two hooks below --
    # river_discharge.emorid.data_script (resolved above) points at
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

    loader.configure_output(
        sim, config, schema, skip_unavailable_fields=args.skip_unavailable_output, load_restart=args.load_restart
    )

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
