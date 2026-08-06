"""A REAL, populated `pygetm_config.providers` registry for OceanICU. Provider
names/extra params below are taken directly from OceanICU's actual code --
`lib/cfg_airsea.py` (meteo: ERA5/CMIP6), `lib/cfg_boundaries.py` (barotropic:
TPXO/CMEMS/CMIP6, baroclinic: CMEMS/WOA), `lib/cfg_ic.py` (hydrography:
CMEMS/WOA/constant) -- not just the one `nse_model_config.yaml` instance,
which (like every other real OceanICU config) only ever picks ONE choice per
role. Verified each role's FULL set of real alternatives directly against
that code before writing this, after an earlier version of this file only
registered whichever single choice `nse_model_config.yaml` happened to use
per role (e.g. only ERA5, when `cfg.meteo.source == "CMIP6"` is an equally
real, supported branch in `cfg_airsea.py`) -- the `source:`-discriminated
`ChoiceSpec` this module builds (via `make_provider_slot`) always fully
supported multiple named sources per role; this file just hadn't populated
all of them.

Not currently wired up as a real installed entry point: `oceanicu_3d` has no
`pyproject.toml`/packaging today (it's a script directory, not an installed
package), so there's nothing for an `[project.entry-points]` table to attach
to yet. If/when this directory (or a subset of it) gets real packaging, the
entry point would be:

    [project.entry-points."pygetm_config.providers"]
    oceanicu = "pygetm_config.oceanicu_providers:register_oceanicu_providers"

(adjust the dotted module path to wherever this file actually lives once
packaged). Until then, `pygetm_config.schema.build_schema()` in a plain
`pygetm`+`pygetm-config` environment will NOT pick this up automatically --
call `register_oceanicu_providers()` directly and merge the result into a
schema's sections by hand if you want to use it before that packaging exists.

Role naming: `boundaries.barotropic`/`boundaries.baroclinic` (not `boundaries`
as one role) because they draw from genuinely different provider vocabularies
in OceanICU (TPXO makes sense only for barotropic tides; CMEMS only for
baroclinic fields) -- one combined role would need every provider to handle
both, which none of them do. Deliberately NOT `rivers` -- that key already
names pygetm-config's core `rivers:` section (open-boundary river *positions*,
from GlobalRiverCollection.add_by_location); this is where the river
*discharge data* comes from, a different concept. pygetm-config's
`build_schema()` raises loudly on this exact kind of key collision rather than
silently shadowing one section with the other -- this naming choice was
actually settled that way: `rivers` collided on an early draft of this module
and `build_schema()` caught it immediately.
"""

from __future__ import annotations

from pathlib import Path

from pygetm_config.model import ChoiceSpec, Importance, ParameterSpec, TypeRef
from pygetm_config.providers import make_provider_slot

# Absolute, not "nse_driver.py" -- pygetm_config.providers.load_dotted_target
# (used by both loader.run_river_discharge_script and codegen.py's
# _emit_river_discharge_script) resolves a "*.py:name" target as a filesystem
# path relative to the CURRENT WORKING DIRECTORY at run time, not relative to
# this file or the YAML config -- same reasoning as PYGETM_CONFIG_PROVIDERS'
# own absolute-path construction in nse_driver.py's main().
_NSE_DRIVER_PATH = Path(__file__).parent / "nse_driver.py"

# Shared across every CMIP6-sourced role (boundaries.{barotropic,baroclinic},
# meteo) -- CMIP6 folder_templates all use {model}/{scenario} placeholders
# (verified: nse_model_config.yaml's boundaries.barotropic.CMIP6/baroclinic.
# CMIP6/meteo.CMIP6 blocks all declare `model: "MPI-ESM1-2-HR"` and
# `scenario: "ssp585"`), so these two extra params are identical everywhere
# CMIP6 shows up, same reasoning as meteo's own _meteo_shared below.
_CMIP6_SHARED = (
    ParameterSpec(
        name="model",
        type=TypeRef(kind="scalar", scalar_type="str"),
        help="CMIP6 model identifier, e.g. 'MPI-ESM1-2-HR' -- fills the {model} folder_template placeholder",
        importance=Importance.BASIC,
    ),
    ParameterSpec(
        name="scenario",
        type=TypeRef(kind="scalar", scalar_type="str"),
        help="CMIP6 scenario identifier, e.g. 'ssp585' -- fills the {scenario} folder_template placeholder",
        importance=Importance.BASIC,
    ),
)


def register_oceanicu_providers() -> dict[str, ChoiceSpec]:
    _hydrography_data_script = (
        ParameterSpec(
            name="data_script",
            type=TypeRef(kind="scalar", scalar_type="str"),
            default=f"{_NSE_DRIVER_PATH}:set_hydrography_ic",
            help=(
                "path/to/file.py:function_name implementing this source's real "
                "initial-condition attachment (mirrors cfg_ic.py's own create() -- "
                "a monthly-climatology-index pick, `.isel(time=imonth)`, not "
                "expressible as a data_assignments entry, plus a conditional "
                "density conversion, sim.density.convert_ts -- see "
                "pygetm_config.loader.run_hydrography_data_script's own "
                "docstring). Only runs when NOT loading from a restart. Same "
                "convention as river_discharge.data_script/meteo.data_script/"
                "PYGETM_CONFIG_PROVIDERS. 'constant' hydrography doesn't need "
                "this -- it's plain data_assignments (simulation.temp/"
                "simulation.salt, kind=constant), no Python at all."
            ),
            importance=Importance.BASIC,
        ),
    )

    hydrography = make_provider_slot(
        "hydrography",
        {
            # CMEMS/WOA: cfg_ic.py just does pygetm.input.from_nc(cfg.hydrography.
            # {CMEMS,WOA}.folder / "<fixed filename>", ...) -- no extra params
            # beyond the shared folder, PLUS data_script for the real
            # .isel(time=imonth)/convert_ts logic (TODO item 21).
            "CMEMS": _hydrography_data_script,
            "WOA": _hydrography_data_script,
            # constant: a fundamentally different shape -- cfg_ic.py's
            # `cfg.hydrography.source == "constant"` branch does
            # sim.temp.set(cfg.hydrography.constant.temp) directly, no file/folder
            # at all. The shared base params (folder/folder_template/...) still
            # get attached by make_provider_slot (nothing to override them off),
            # but are simply unused/None here -- harmless, matches how this
            # provider is actually used.
            "constant": (
                ParameterSpec(
                    name="temp",
                    type=TypeRef(kind="scalar", scalar_type="float"),
                    help="uniform initial temperature (degrees_Celsius)",
                    importance=Importance.BASIC,
                ),
                ParameterSpec(
                    name="salt",
                    type=TypeRef(kind="scalar", scalar_type="float"),
                    help="uniform initial salinity",
                    importance=Importance.BASIC,
                ),
            ),
        },
        default="CMEMS",
    )

    boundary_barotropic = make_provider_slot(
        "boundaries.barotropic",
        {
            "TPXO": (
                ParameterSpec(
                    name="tpxo_folder",
                    type=TypeRef(kind="path"),
                    help="directory containing TPXO tidal constituent files (env override: TPXO_FOLDER)",
                    importance=Importance.BASIC,
                ),
            ),
            # CMEMS: cfg_boundaries.py::data_2d's generic (non-TPXO) branch --
            # reads zos/uo/vo from a single resolved file via the shared
            # folder/folder_template/filename_template base, no extra params.
            "CMEMS": (),
            "CMIP6": _CMIP6_SHARED,
        },
        default="TPXO",
    )

    boundary_baroclinic = make_provider_slot(
        "boundaries.baroclinic",
        {
            "CMEMS": (),
            # WOA: cfg_boundaries.py::data_3d's WOA branch actually reads
            # cfg.hydrography.WOA.folder, NOT a boundaries.baroclinic.WOA.folder
            # of its own -- a real cross-role reuse in OceanICU's own code (WOA
            # climatology data serves both initial conditions AND baroclinic
            # boundary values). Not replicated here -- this schema models each
            # role as independently configured, and doing otherwise would need
            # loader-level plumbing this repo doesn't have; a project actually
            # using WOA for `boundaries.baroclinic` needs to keep that
            # cross-reference in mind (or just duplicate the folder value under
            # both roles, which is what this shape assumes).
            "WOA": (),
            "CMIP6": _CMIP6_SHARED,
        },
        default="CMEMS",
    )

    # Shared across meteo sources -- cfg_airsea.py's create() reads these BEFORE
    # branching on cfg.meteo.source (they configure the shared FluxesFromMeteo
    # instance, not anything source-specific), so both ERA5 and CMIP6 need them
    # identically. make_provider_slot's shared base (folder/folder_template/...)
    # is role-universal across ALL providers.py roles, not customizable per-role,
    # so these three are declared here and passed to both choices below instead.
    _meteo_shared = (
        ParameterSpec(
            name="evaporation",
            type=TypeRef(kind="scalar", scalar_type="bool"),
            default=True,
            help="derive evaporation from latent heat flux (cfg.meteo.evaporation, shared across sources)",
            importance=Importance.ADVANCED,
        ),
        ParameterSpec(
            name="shortwave_method",
            type=TypeRef(kind="scalar", scalar_type="int"),
            default=1,
            help="shortwave radiation method selector (see pygetm.airsea.FluxesFromMeteo)",
            importance=Importance.ADVANCED,
        ),
        ParameterSpec(
            name="longwave_method",
            type=TypeRef(kind="scalar", scalar_type="int"),
            default=1,
            help="longwave radiation method selector (see pygetm.airsea.FluxesFromMeteo)",
            importance=Importance.ADVANCED,
        ),
        ParameterSpec(
            name="data_script",
            type=TypeRef(kind="scalar", scalar_type="str"),
            default=f"{_NSE_DRIVER_PATH}:set_meteo_data",
            help=(
                "path/to/file.py:function_name for the ONE piece of meteo data "
                "attachment that genuinely can't be a static data_assignments "
                "entry: CMIP6's net shortwave/longwave (swr = rsds - rsus, "
                "ql = rlds - rlus, mirroring cfg_airsea.py's own data()) is a "
                "subtraction of TWO files, and pre_transform only supports a "
                "scale/offset on ONE file's value -- see pygetm_config.loader."
                "run_meteo_data_script's own docstring. Every other field "
                "(u10/v10/t2m/qa-or-d2m/sp/tp/tcc) IS a genuine 1:1 file read "
                "(or, for tcc, a required-even-if-unused constant) and is a "
                "real data_assignments entry instead -- pygetm.input.from_nc "
                "already handles a glob pattern (ERA5's annual files) or a "
                "single filename (CMIP6) identically, no branch needed for "
                "those. A no-op for ERA5 (which doesn't need this at all with "
                "the default shortwave/longwave method) -- ONE function covers "
                "every source (branches internally, matching cfg_airsea.py's "
                "own single data(sim, cfg) doing the same), so the SAME "
                "default is shared by every alternative below rather than "
                "each having its own. Same convention as river_discharge."
                "data_script/PYGETM_CONFIG_PROVIDERS."
            ),
            importance=Importance.BASIC,
        ),
    )

    meteo = make_provider_slot(
        "meteo",
        {
            # humidity_measure differs by source (DEW_POINT_TEMPERATURE for ERA5
            # vs SPECIFIC_HUMIDITY for CMIP6, per cfg_airsea.py) but that's a
            # fixed consequence of the source choice, not itself a configurable
            # field -- not modeled as a param here.
            "ERA5": _meteo_shared,
            "CMIP6": _meteo_shared + _CMIP6_SHARED,
        },
        default="ERA5",
    )

    # KNOWN INCOMPLETE, unlike the roles above: cfg_rivers.py's real
    # cfg.rivers.source is actually TWO-LEVEL -- "historic" (itself wrapping a
    # further cfg.rivers.historic.source choice, "emorid" being the only one
    # currently used) as one sibling of top-level cfg.rivers.source, and
    # "CMIP6" (a materially different, projected-discharge source with its own
    # per-source variable-naming lookup table, river_config[...], keyed by
    # source name -- index/name/lat/lon/Q/Qmean column names all differ) as
    # another. make_provider_slot builds a single FLAT source-discriminated
    # choice, with no support for a nested sub-choice like "historic"'s own
    # source -- modeling this properly would need either a second, nested
    # ChoiceSpec (a real gap in providers.py's design, not just this file) or
    # flattening "historic+emorid" into one combined choice name. Left as
    # "emorid" only, matching the ONE real source nse_model_config.yaml
    # actually uses -- not fixed here, unlike meteo/boundaries/hydrography
    # above, since it needs a design decision in providers.py itself, not
    # just more registry entries.
    river_discharge = make_provider_slot(
        "river_discharge",
        {
            "emorid": (
                ParameterSpec(
                    name="file",
                    type=TypeRef(kind="scalar", scalar_type="str"),
                    help="EMORID/JRC discharge NetCDF filename, relative to `folder`",
                    importance=Importance.BASIC,
                ),
                ParameterSpec(
                    name="threshold",
                    type=TypeRef(kind="scalar", scalar_type="float"),
                    default=0.0,
                    help="minimum mean discharge (m3/s) for a river to be included",
                    importance=Importance.BASIC,
                ),
                ParameterSpec(
                    name="script",
                    type=TypeRef(kind="scalar", scalar_type="str"),
                    default=f"{_NSE_DRIVER_PATH}:add_rivers",
                    help=(
                        "path/to/file.py:function_name implementing this source's real "
                        "river POSITIONING (name + location) -- see pygetm_config.loader."
                        "run_river_discharge_script's own docstring. Same convention as "
                        "PYGETM_CONFIG_PROVIDERS. A future 'cmip6_bias_corrected' source "
                        "would need its own, different function here -- a real "
                        "scenario-discharge dataset has its own station/grid-cell "
                        "inventory, not EMORID's real-world coordinates."
                    ),
                    importance=Importance.BASIC,
                ),
                ParameterSpec(
                    name="data_script",
                    type=TypeRef(kind="scalar", scalar_type="str"),
                    default=f"{_NSE_DRIVER_PATH}:set_river_data",
                    help=(
                        "path/to/file.py:function_name implementing this source's real "
                        "river DISCHARGE DATA (mirrors cfg_rivers.py's own two-step split: "
                        "'1) Set name and position of rivers... 2) Attach river data to the "
                        "Simulation object' -- position (`script`, above) runs before `sim` "
                        "exists, data needs the live sim.rivers collection, so this is a "
                        "SEPARATE hook, timed like post_data_script (after data_assignments) "
                        "-- see pygetm_config.loader.run_river_discharge_data_script's own "
                        "docstring. Same file as `script` above is fine (this role's function "
                        "for position and data live together in nse_driver.py), but they are "
                        "two distinct functions, not one combined one -- a future source with "
                        "its own inventory needs its own pair, not a single function doing "
                        "both."
                    ),
                    importance=Importance.BASIC,
                ),
            ),
        },
        default="emorid",
    )

    # Dict order here IS the section order everywhere downstream (TUI
    # navigation tree, generated YAML template, ...) -- pygetm_config.schema.
    # build_schema() preserves registration order rather than alphabetizing
    # it (see that module's own comment). Matches run_model.py's real
    # create_simulation() processing order exactly: cfg_ic.create (hydrography,
    # line 172) -> cfg_boundaries.data_2d/data_3d (boundaries, lines 174-176)
    # -> cfg_rivers.data (river_discharge, line 178) -> cfg_airsea.data
    # (meteo, line 180).
    return {
        "hydrography": hydrography,
        "boundaries.barotropic": boundary_barotropic,
        "boundaries.baroclinic": boundary_baroclinic,
        "river_discharge": river_discharge,
        "meteo": meteo,
    }
