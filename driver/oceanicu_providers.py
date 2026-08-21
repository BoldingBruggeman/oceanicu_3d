"""A populated `pygetm_config.providers` registry for OceanICU. Provider
names/extra params below are taken directly from OceanICU's actual code --
`lib/cfg_airsea.py` (meteo: ERA5/CMIP6), `lib/cfg_boundaries.py` (barotropic:
TPXO/CMEMS/CMIP6, baroclinic: CMEMS/WOA), `lib/cfg_ic.py` (hydrography:
CMEMS/WOA/constant) -- the FULL set of real alternatives per role, not just
whichever single choice any one domain config happens to use (every domain
config only ever picks ONE choice per role; the `source:`-discriminated
`ChoiceSpec` this module builds, via `make_provider_slot`, supports all of
them at once).

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
silently shadowing one section with the other.
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
# own absolute-path construction in nse_driver.py's main(). One file per
# provider role under scripts/ (not one big driver-adjacent file) -- each
# data_script/script/post_data_script function is fully self-contained (own
# local imports, no shared module-level state), so splitting cost nothing.
_SCRIPTS_DIR = Path(__file__).parent / "scripts"
_RIVERS_SCRIPT_PATH = _SCRIPTS_DIR / "rivers.py"
_METEO_SCRIPT_PATH = _SCRIPTS_DIR / "meteo.py"
_HYDROGRAPHY_SCRIPT_PATH = _SCRIPTS_DIR / "hydrography.py"
_FABM_SCRIPT_PATH = _SCRIPTS_DIR / "fabm.py"

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
    # setdefault, not override -- an explicit PYGETM_CONFIG_DATA_ASSIGNMENTS_
    # DERIVERS export still wins. Helps every caller that builds the schema
    # LIVE (oceanicu_driver.py direct/--dump-python, `pygetm-config run`,
    # `pygetm-config dump`) -- for those, this one setdefault is enough, no
    # need for oceanicu_driver.py's own main() to ALSO set the same env var
    # separately (that duplication was the earlier bug here).
    #
    # Does NOT help `pygetm-config edit --schema dist/schema.json`/`web`
    # (the REAL way this project's TUI actually gets launched, see driver/
    # README.md) -- that pygetm-free workflow deliberately loads a PRE-BUILT
    # schema.json and never calls build_schema()/this function at all, so
    # PYGETM_CONFIG_DATA_ASSIGNMENTS_DERIVERS must be exported EXPLICITLY
    # before launching `pygetm-config edit`, same as SCRIPT_FOLDER already
    # has to be -- see driver/README.md's own TUI launch instructions for
    # both exports. Confirmed directly: this setdefault provably never
    # fires in that code path (no live pygetm import happens there at all).
    import os

    os.environ.setdefault(
        "PYGETM_CONFIG_DATA_ASSIGNMENTS_DERIVERS",
        f"{Path(__file__)}:derive_data_assignments",
    )

    _hydrography_data_script = (
        ParameterSpec(
            name="data_script",
            type=TypeRef(kind="scalar", scalar_type="str"),
            default=f"{_HYDROGRAPHY_SCRIPT_PATH}:set_hydrography_ic",
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
            # .isel(time=imonth)/convert_ts logic.
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
            # WOA: uses its own boundaries.baroclinic.WOA.folder (the
            # role-universal shared base's `folder` param, same as every
            # other choice here) -- read directly by oceanicu_driver.py, not
            # reused from hydrography.WOA.folder. This schema models each
            # role as independently configured, so a project using WOA for
            # both hydrography AND boundaries.baroclinic sets the same real
            # folder value under both roles' own folder field.
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
            default=f"{_METEO_SCRIPT_PATH}:set_meteo_data",
            help=(
                "path/to/file.py:function_name for the meteo data attachment "
                "that genuinely can't be a static data_assignments entry: "
                "CMIP6's radiation (meteo.CMIP6.radiation_source -- 'net'/"
                "'components' reconstruct swr/ql directly from a subtraction "
                "of TWO files each; 'pseudo_tcc' derives tcc from bias-"
                "corrected daily-mean rsds via an analytic TOA-insolation "
                "computation and a clearness-index fit -- mirroring "
                "cfg_airsea.py's own data()). pre_transform only supports a "
                "scale/offset on ONE file's value, and pre_transform_"
                "expression is deliberately refused for security reasons -- "
                "see pygetm_config.loader.run_meteo_data_script's own "
                "docstring and scripts/meteo.py's own set_meteo_data "
                "docstring for all three. Every other field (u10/v10/t2m/"
                "qa-or-d2m/sp/tp, and tcc's own constant_value=0.5 "
                "placeholder that 'pseudo_tcc' overwrites) IS a genuine 1:1 "
                "file read (or constant) and is a real data_assignments "
                "entry instead -- pygetm.input.from_nc already handles a "
                "glob pattern (ERA5's annual files) or a single filename "
                "(CMIP6) identically, no branch needed for those. A no-op "
                "for ERA5 (which has a real tcc file and doesn't need this "
                "at all) -- ONE function covers every source (branches "
                "internally, matching cfg_airsea.py's own single data(sim, "
                "cfg) doing the same), so the SAME default is shared by "
                "every alternative below rather than each having its own. "
                "Same convention as river_discharge.data_script/"
                "PYGETM_CONFIG_PROVIDERS."
            ),
            importance=Importance.BASIC,
        ),
    )

    # ERA5, unlike CMIP6, has no "model"/"scenario" concept -- there's
    # exactly one extraction/processing run per domain, so it isn't worth a
    # separate sub-folder placeholder at all: `folder` is just the full real
    # path to where that extraction's era5_<var>_????.nc files already
    # live, same as every other non-CMIP6 provider in this module (CMEMS,
    # WOA, TPXO, emorid). The filename side (fixed "era5_<var>_????.nc"
    # glob, hardcoded in oceanicu_driver.py's own _meteo_assignments) is not
    # configurable -- would need wildcarding if a different extraction ever
    # used another naming convention.

    # meteo-CMIP6-specific, NOT part of _CMIP6_SHARED -- that tuple is also
    # used by boundaries.barotropic/baroclinic.CMIP6, which has no radiation
    # concept at all.
    _meteo_cmip6_radiation = (
        ParameterSpec(
            name="radiation_source",
            type=TypeRef(kind="scalar", scalar_type="str", nullable=True),
            default="pseudo_tcc",
            choices=("net", "components", "pseudo_tcc"),
            help=(
                "'net' | 'components' | 'pseudo_tcc' (default) -- which "
                "bias-corrected CMIP6 radiation data scripts/meteo.py:"
                "set_meteo_data uses. 'net': net_sw/net_lw, each already "
                "bias-corrected as one direct file (net_sw = rsds - rsus, "
                "net_lw = rlds - rlus corrected as a single composite "
                "quantity) -- ocean-prep's own bias-correction tool's "
                "recommended approach whenever those files exist for the "
                "model/scenario in use. 'components': the older convention, "
                "4 separate files (rsds/rsus/rlds/rlus) subtracted at "
                "runtime -- for a model/scenario that only has these 4 "
                "files, not net_sw/net_lw. 'pseudo_tcc': derives a pseudo "
                "cloud fraction from bias-corrected rsds ALONE (a "
                "clearness-index proxy calibrated against ERA5) and feeds "
                "it into simulation.airsea.tcc, letting FluxesFromMeteo's "
                "own ROSATI_MIYAKODA/CLARK bulk formulas compute both swr "
                "and ql -- added to get CMIP6 radiation working at all for "
                "models/scenarios where net_sw/net_lw/rsus/rlds/rlus aren't "
                "reliably available (GFDL THREDDS outages, CNRM ESGF 403s, "
                "etc.), NOT a replacement for 'net'/'components' where "
                "those files DO exist -- switch back to 'net' the moment a "
                "model/scenario has reliable source data for it. Whichever "
                "is chosen must be matched with the right shortwave_method/"
                "longwave_method (NET_FLUX=-1 for 'net'/'components', a "
                "bulk-formula method e.g. the default 1 for 'pseudo_tcc') "
                "-- see set_meteo_data's own docstring, which also warns at "
                "runtime if the two look inconsistent."
            ),
            importance=Importance.ADVANCED,
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
            "CMIP6": _meteo_shared + _CMIP6_SHARED + _meteo_cmip6_radiation,
        },
        default="ERA5",
    )

    # Narrower than the roles above: cfg_rivers.py's real cfg.rivers.source
    # is actually TWO-LEVEL -- "historic" (itself wrapping a further
    # cfg.rivers.historic.source choice, "emorid" being the only one
    # currently used) as one sibling of top-level cfg.rivers.source, and
    # "CMIP6" as another. make_provider_slot builds a single FLAT
    # source-discriminated choice, with no support for a nested sub-choice
    # like "historic"'s own source -- modeling this properly would need
    # either a second, nested ChoiceSpec in providers.py itself, or
    # flattening "historic+emorid" into one combined choice name.
    #
    # "CMIP6" reuses add_rivers/set_river_data UNCHANGED -- stats/cli/
    # river_projection.py's delta-change output (river_flows_future_
    # {scenario}.nc) is keyed by the SAME 446 EMORID stations the
    # station_filter already narrowed things down to (same site_name/lat/lon
    # identity, just Q replaced with the projected future series), so it's
    # schema-compatible with the "emorid" reader as-is -- river_projection.py
    # was given a Q_mean variable (site-mean of the projected Q) purely so
    # add_rivers's own qmean-pattern threshold lookup finds a match; nothing
    # in scripts/rivers.py changed beyond adding {model}/{scenario}
    # formatting to `file` itself (same substitution folder_template already
    # does) -- keeps `file: river_flows_future_{scenario}.nc` in sync with
    # `scenario:` automatically, rather than needing the literal scenario
    # name written out twice.
    _river_shared_fields = (
        ParameterSpec(
            name="file",
            type=TypeRef(kind="scalar", scalar_type="str"),
            help=(
                "discharge NetCDF filename, relative to `folder`/`folder_template` "
                "-- EMORID/JRC's own file for source=emorid, or "
                "'river_flows_future_{scenario}.nc' for source=CMIP6 (scripts/"
                "rivers.py formats {model}/{scenario} placeholders in this field "
                "itself, same as `folder_template` does) -- see stats/cli/"
                "river_projection.py's own output naming"
            ),
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
            default=f"{_RIVERS_SCRIPT_PATH}:add_rivers",
            help=(
                "path/to/file.py:function_name implementing this source's real "
                "river POSITIONING (name + location) -- see pygetm_config.loader."
                "run_river_discharge_script's own docstring. Same convention as "
                "PYGETM_CONFIG_PROVIDERS."
            ),
            importance=Importance.BASIC,
        ),
        ParameterSpec(
            name="data_script",
            type=TypeRef(kind="scalar", scalar_type="str"),
            default=f"{_RIVERS_SCRIPT_PATH}:set_river_data",
            help=(
                "path/to/file.py:function_name implementing this source's real "
                "river DISCHARGE DATA (mirrors cfg_rivers.py's own two-step split: "
                "'1) Set name and position of rivers... 2) Attach river data to the "
                "Simulation object' -- position (`script`, above) runs before `sim` "
                "exists, data needs the live sim.rivers collection, so this is a "
                "SEPARATE hook, timed like post_data_script (after data_assignments) "
                "-- see pygetm_config.loader.run_river_discharge_data_script's own "
                "docstring. Same file as `script` above is fine (this role's function "
                "for position and data live together in scripts/rivers.py)."
            ),
            importance=Importance.BASIC,
        ),
    )

    river_discharge = make_provider_slot(
        "river_discharge",
        {
            "emorid": _river_shared_fields,
            "CMIP6": _river_shared_fields + _CMIP6_SHARED,
        },
        default="emorid",
    )

    # FABM boundary/IC data (WOA-sourced tracer initial values + SPONGE
    # boundaries in cfg_fabm.py today) gets its own role, separate from
    # BOTH hydrography (T/S initial conditions) and fabm (which
    # biogeochemical model/dependencies) -- mirrors boundary_baroclinic's
    # own independence from hydrography above (that role's own comment:
    # "This schema models each role as independently configured") for the
    # identical reason: FABM boundary nutrients could legitimately come
    # from a different source than T/S (e.g. CMEMS biogeochemistry) without
    # restructuring later. cfg_fabm.py's real WOA branch currently checks
    # `cfg.hydrography.source == "WOA"` -- driver/scripts/fabm.py checks
    # this role's own `boundaries.fabm.source` instead.
    # Which FABM state variables actually get an explicit SPONGE boundary +
    # real values (vs. quietly keeping whatever boundary type FABM/pygetm
    # defaults to, e.g. ZERO_GRADIENT) is NOT fixed -- unlike T/S, where
    # exactly two tracers (temp/salt) always need one. A biogeochemical
    # model can have dozens of state variables and only some of them
    # (typically the ones with real open-ocean gradients, e.g. nutrients)
    # need boundary forcing at all. So this is a per-source, per-config
    # mapping rather than a hardcoded list: tracer name -> {file, variable}
    # (file resolved relative to this choice's own `folder`). Read by
    # derive_data_assignments below (boundary_type + values) AND by
    # scripts/fabm.py's own configure_fabm (the WOA-only initial-condition
    # pick, which needs a real .isel(time=imonth), so can't be a
    # data_assignments entry -- see that function's own docstring).
    _fabm_tracers_param = ParameterSpec(
        name="tracers",
        type=TypeRef(
            kind="mapping",
            nullable=True,
            inner=TypeRef(kind="mapping", inner=TypeRef(kind="scalar", scalar_type="str")),
        ),
        default=None,
        help="FABM state variable name -> {file, variable} for tracers that get a "
        "SPONGE boundary + real values from this source. Tracers NOT listed here "
        "keep pygetm's own ZERO_GRADIENT default (every Tracer, FABM ones included, "
        "gets ArrayOpenBoundaries(self, ZERO_GRADIENT) at construction, before any "
        "config runs -- see pygetm/tracer.py's own Tracer.__init__) and their "
        "fabm.yaml-declared initial_value for the interior IC -- not every FABM "
        "state variable needs an explicit boundary/IC override. `file` is resolved "
        "relative to this choice's own `folder`.",
        importance=Importance.BASIC,
    )

    boundary_fabm = make_provider_slot(
        "boundaries.fabm",
        {
            # WOA: a global climatology (on_grid=False, climatology=True,
            # cycling the same 12-month pattern all run) -- mirrors
            # boundary_baroclinic's own WOA branch.
            "WOA": (_fabm_tracers_param,),
            # CMEMS: a real biogeochemistry time series already sitting at
            # the boundary points (on_grid=True, climatology=False) -- real
            # CMEMS BGC product/variable naming isn't confirmed for NSe yet
            # (structurally complete, unverified, same status as the ERSEM
            # dependency setup itself), so `tracers[*].file`/`variable` are
            # left fully config-driven rather than a Python-side convention
            # like WOA's woa_*.nc files.
            "CMEMS": (_fabm_tracers_param,),
        },
        default="WOA",
    )

    # pyGETM/FABM supports many biogeochemical models (ERSEM here, but
    # others are possible) -- source-discriminated like every other role
    # here, not a bare enable/disable flag, so a future second model can be
    # added as another choice without restructuring. Mirrors cfg_fabm.py's
    # own `cfg.fabm.config == "ersem"` check exactly: real FABM setup always
    # needs BOTH sim.fabm (is FABM enabled at all, from simulation.fabm --
    # see oceanicu_driver.py's propagation of `file` below) AND which
    # specific model/config is active (this role's own `source`), since
    # different models need different dependencies/ICs/boundaries.
    #
    # `folder` (FABM-specific input files, e.g. cfg_fabm.py's EMEP/gelbstoff
    # netCDFs) comes for free from _shared_provider_base_params(). `file` is
    # the actual fabm.yaml path -- propagated into simulation.fabm by
    # oceanicu_driver.py (same shape as meteo's shared params propagating
    # into simulation.airsea).
    fabm = make_provider_slot(
        "fabm",
        {
            "ERSEM": (
                ParameterSpec(
                    name="file",
                    type=TypeRef(kind="path"),
                    default=None,
                    help="path to the fabm.yaml driving this run -- propagated into "
                    "simulation.fabm (pygetm.Simulation's own fabm= constructor kwarg) "
                    "by oceanicu_driver.py. Required to actually enable FABM.",
                    importance=Importance.BASIC,
                ),
                ParameterSpec(
                    name="data_script",
                    type=TypeRef(kind="scalar", scalar_type="str"),
                    default=f"{_FABM_SCRIPT_PATH}:configure_fabm",
                    help=(
                        "path/to/file.py:function_name for FABM *dependency* setup "
                        "(sim.fabm.get_dependency(...)) and FABM-tracer ICs/boundaries "
                        "-- none of which fit a static data_assignments entry (that "
                        "only reaches FABM state variables via fabm.<tracer_name>.<attr>, "
                        "a narrower API than dependencies). Mirrors cfg_fabm.py's own "
                        "real configure(sim, cfg, imonth). See "
                        "pygetm_config.loader.run_fabm_data_script's own docstring and "
                        "scripts/fabm.py's own configure_fabm docstring. Same convention "
                        "as meteo.data_script/river_discharge.data_script/"
                        "PYGETM_CONFIG_PROVIDERS."
                    ),
                    importance=Importance.BASIC,
                ),
            ),
        },
        default="ERSEM",
    )

    # Dict order here IS the section order everywhere downstream (TUI
    # navigation tree, generated YAML template, ...) -- pygetm_config.schema.
    # build_schema() preserves registration order rather than alphabetizing
    # it (see that module's own comment). Matches run_model.py's real
    # create_simulation() processing order exactly: cfg_ic.create (hydrography,
    # line 172) -> cfg_boundaries.data_2d/data_3d (boundaries, lines 174-176)
    # -> cfg_rivers.data (river_discharge, line 178) -> cfg_airsea.data
    # (meteo, line 180) -> cfg_fabm.configure (boundaries.fabm has no own
    # position -- read directly by fabm's own configure_fabm, not a
    # separate step; fabm, line 193, last).
    return {
        "hydrography": hydrography,
        "boundaries.barotropic": boundary_barotropic,
        "boundaries.baroclinic": boundary_baroclinic,
        "river_discharge": river_discharge,
        "meteo": meteo,
        "boundaries.fabm": boundary_fabm,
        "fabm": fabm,
    }


# ----------------------------------------------------------------------
# data_assignments deriver -- registered via PYGETM_CONFIG_DATA_ASSIGNMENTS_
# DERIVERS (pygetm_config.providers.derive_extra_data_assignments), so ALL
# THREE generation modes (oceanicu_driver.py direct/--dump-python, TUI/web
# 'Generate script') compute the SAME boundaries.baroclinic/meteo
# data_assignments -- not just whichever ran oceanicu_driver.py's own
# main(). Moved out of oceanicu_driver.py's main() (this session's earlier,
# incomplete fix: a setdefault-style injection that only fired for driver
# runs, so a TUI-only edit of boundaries.baroclinic.source never produced
# the corresponding open_boundary.temp/salt.values at all).
#
# Reads the VALIDATED config shape (validate_config's own OUTPUT, same as
# codegen._emit_data_assignments/loader.apply_data_assignments both already
# pass in): the ACTIVE choice label's own fields are flattened directly
# onto the parent dict (config['boundaries']['baroclinic']['folder'], not
# ['boundaries']['baroclinic']['CMEMS']['folder']) -- see
# yaml_parse._validate_choice / loader._resolve_choice. Must stay
# pygetm-free (codegen.py calls this too, at GENERATION time).
def derive_data_assignments(config: dict) -> list[dict]:
    entries: list[dict] = []

    # boundaries.baroclinic 3D temp/salt boundary VALUES differ by source --
    # WOA: a global climatology (on_grid=False, climatology=True, cycling
    # the same 12-month pattern all run) vs CMEMS: a real time series
    # already sitting at the boundary points (on_grid=True,
    # climatology=False) -- mirrors cfg_boundaries.py::data_3d. The
    # boundary_type (SPONGE) entries are source-independent and stay static
    # in the YAML's own data_assignments block (unaffected by this).
    baroclinic = config.get("boundaries", {}).get("baroclinic", {})
    baroclinic_source = baroclinic.get("source")
    if baroclinic_source == "WOA":
        _woa_folder = Path(baroclinic.get("folder", ""))
        entries += [
            {"target": "open_boundary.temp.values", "kind": "file", "file": str(_woa_folder / "woa_t.nc"), "variable": "t_an", "on_grid": False, "climatology": True},
            {"target": "open_boundary.salt.values", "kind": "file", "file": str(_woa_folder / "woa_s.nc"), "variable": "s_an", "on_grid": False, "climatology": True},
        ]
    elif baroclinic_source == "CMEMS":
        _cmems_folder = Path(baroclinic.get("folder", ""))
        if baroclinic.get("folder_template"):
            _cmems_folder = _cmems_folder / baroclinic["folder_template"]
        _cmems_file = _cmems_folder / baroclinic.get("filename_template", "").format(
            start_date=baroclinic.get("start_date", ""), end_date=baroclinic.get("end_date", "")
        )
        entries += [
            {"target": "open_boundary.temp.values", "kind": "file", "file": str(_cmems_file), "variable": "thetao", "on_grid": True, "climatology": False},
            {"target": "open_boundary.salt.values", "kind": "file", "file": str(_cmems_file), "variable": "so", "on_grid": True, "climatology": False},
        ]
    elif baroclinic_source == "CMIP6":
        # ocean-prep's run-delta-boundaries writes one file PER VARIABLE
        # (bdy_3d_{variable}_{start}_{end}.nc), unlike CMEMS's single file
        # holding both thetao/so -- filename_template is formatted once per
        # target with variable='thetao'/'so' rather than reused as-is.
        _cmip6_folder = Path(baroclinic.get("folder", ""))
        if baroclinic.get("folder_template"):
            _cmip6_folder = _cmip6_folder / baroclinic["folder_template"].format(
                model=baroclinic.get("model", ""), scenario=baroclinic.get("scenario", "")
            )
        _fn_tmpl = baroclinic.get("filename_template", "")
        _temp_file = _cmip6_folder / _fn_tmpl.format(
            variable="thetao", start_date=baroclinic.get("start_date", ""), end_date=baroclinic.get("end_date", "")
        )
        _salt_file = _cmip6_folder / _fn_tmpl.format(
            variable="so", start_date=baroclinic.get("start_date", ""), end_date=baroclinic.get("end_date", "")
        )
        entries += [
            {"target": "open_boundary.temp.values", "kind": "file", "file": str(_temp_file), "variable": "thetao", "on_grid": True, "climatology": False},
            {"target": "open_boundary.salt.values", "kind": "file", "file": str(_salt_file), "variable": "so", "on_grid": True, "climatology": False},
        ]

    # meteo's straightforward 1:1 file-read fields -- differ by SOURCE (ERA5
    # vs CMIP6 use different target fields entirely: d2m/DEW_POINT_
    # TEMPERATURE vs qa/SPECIFIC_HUMIDITY), so still can't be one single
    # static list independent of source the way boundaries.baroclinic's
    # matching targets could theoretically share. swr/swr_downwards/ql/
    # ql_downwards emitted unconditionally for ERA5 (like the already-
    # migrated real domains' own static YAML entries) -- pygetm_config.
    # loader._airsea_flux_target_inactive/codegen.py's own copy already
    # skip whichever doesn't match simulation.airsea's actual shortwave_
    # method/longwave_method, so no need to duplicate that condition here.
    meteo = config.get("meteo", {})
    meteo_source = meteo.get("source")
    if meteo_source == "ERA5":
        _folder = Path(meteo.get("folder", ""))
        entries += [
            {"target": "simulation.airsea.t2m", "kind": "file", "file": str(_folder / "era5_t2m_????.nc"), "variable": "t2m", "pre_transform_offset": -273.15},
            {"target": "simulation.airsea.d2m", "kind": "file", "file": str(_folder / "era5_d2m_????.nc"), "variable": "d2m", "pre_transform_offset": -273.15},
            {"target": "simulation.airsea.u10", "kind": "file", "file": str(_folder / "era5_u10_????.nc"), "variable": "u10"},
            {"target": "simulation.airsea.v10", "kind": "file", "file": str(_folder / "era5_v10_????.nc"), "variable": "v10"},
            {"target": "simulation.airsea.sp", "kind": "file", "file": str(_folder / "era5_sp_????.nc"), "variable": "sp"},
            {"target": "simulation.airsea.tp", "kind": "file", "file": str(_folder / "era5_tp_????.nc"), "variable": "tp", "pre_transform_scale": 1 / 3600.0},
            {"target": "simulation.airsea.tcc", "kind": "file", "file": str(_folder / "era5_tcc_????.nc"), "variable": "tcc"},
            {"target": "simulation.airsea.swr", "kind": "file", "file": str(_folder / "era5_ssr_????.nc"), "variable": "ssr", "pre_transform_scale": 1 / 3600.0},
            {"target": "simulation.airsea.swr_downwards", "kind": "file", "file": str(_folder / "era5_ssrd_????.nc"), "variable": "ssrd", "pre_transform_scale": 1 / 3600.0},
            {"target": "simulation.airsea.ql", "kind": "file", "file": str(_folder / "era5_str_????.nc"), "variable": "str", "pre_transform_scale": 1 / 3600.0},
            {"target": "simulation.airsea.ql_downwards", "kind": "file", "file": str(_folder / "era5_strd_????.nc"), "variable": "strd", "pre_transform_scale": 1 / 3600.0},
        ]
    elif meteo_source == "CMIP6":
        _folder = Path(meteo.get("folder", ""))
        if meteo.get("folder_template"):
            _folder = _folder / meteo["folder_template"].format(
                model=meteo.get("model", ""), scenario=meteo.get("scenario", "")
            )
        # "*" stands in for each variable's own bc-correct regridding.method
        # (e.g. 'conservative' vs 'bilinear') -- do NOT hardcode 'bilinear'
        # here: bc-correct's own disaggregated-output writer has a latent
        # bug (real per-variable method/scenario not threaded through to
        # that specific write path, silently defaulting to 'bilinear'/'' for
        # every variable), and this glob is deliberately robust on both
        # sides of that bug -- matches today's wrongly-named files AND
        # correctly-named ones once that's fixed upstream, no change needed
        # here either way. See driver/scripts/meteo.py's own set_meteo_data
        # docstring for the same reasoning.
        entries += [
            {"target": "simulation.airsea.t2m", "kind": "file", "file": str(_folder / "tas_bc_*_disagg_????.nc"), "variable": "tas", "pre_transform_offset": -273.15},
            {"target": "simulation.airsea.qa", "kind": "file", "file": str(_folder / "huss_bc_*_disagg_????.nc"), "variable": "huss"},
            {"target": "simulation.airsea.u10", "kind": "file", "file": str(_folder / "uas_bc_*_disagg_????.nc"), "variable": "uas"},
            {"target": "simulation.airsea.v10", "kind": "file", "file": str(_folder / "vas_bc_*_disagg_????.nc"), "variable": "vas"},
            {"target": "simulation.airsea.sp", "kind": "file", "file": str(_folder / "psl_bc_*_disagg_????.nc"), "variable": "psl"},
            {"target": "simulation.airsea.tp", "kind": "file", "file": str(_folder / "pr_bc_*_disagg_????.nc"), "variable": "pr", "pre_transform_scale": 1 / 1000.0},
            # Placeholder/fallback. When meteo.CMIP6.radiation_source ==
            # "pseudo_tcc" (the default), meteo.data_script (set_meteo_data,
            # default scripts/meteo.py:set_meteo_data) overwrites this with a
            # real, derived-from-rsds value right after apply_data_assignments
            # runs. With radiation_source == "net"/"components", set_meteo_data
            # sets swr/ql directly instead and tcc is unused (as long as
            # shortwave_method/longwave_method are set to NET_FLUX to match --
            # see set_meteo_data's own docstring). Kept as a real entry (not
            # omitted) so the field has a sane value in either case.
            {"target": "simulation.airsea.tcc", "kind": "constant", "constant_value": 0.5},
        ]

    # FABM tracer boundary type + values (WOA-sourced) -- mirrors
    # boundaries.baroclinic's own WOA branch above exactly (straightforward
    # 1:1 climatology file reads, no computation needed), the same
    # meteo/CMIP6-vs-ERA5 split reasoning: ERA5's plain reads live here as
    # data_assignments, only CMIP6's genuinely COMPUTED radiation_source
    # branches stay imperative in scripts/meteo.py. FABM's ERSEM dependency
    # setup (gelbstoff/CO2/EMEP -- needs pygetm.input preprocessing) and the
    # IC's one-time `.isel(time=imonth)` pick genuinely can't be
    # data_assignments (same reason hydrography.py's own T/S IC pick can't
    # be either -- climatology=True cycles all 12 months for the whole run,
    # not "pick one month once") -- both stay in scripts/fabm.py.
    #
    # Gated here (not static in each nse_*.yaml, unlike open_boundary.temp/
    # salt's SPONGE entries) because FABM tracer targets have NO runtype/
    # enabled-state skip coverage in pygetm_config.loader --
    # _tracer_target_invalid_for_runtype's own docstring: "FABM-added
    # tracers are dynamic/model-specific and NOT covered ... a config with
    # one still crashes at execution time". An unconditional
    # open_boundary.N3_n entry would crash every nse_*.yaml today (FABM off
    # by default, fabm.ERSEM.file unset) -- gating on the same condition
    # oceanicu_driver.py itself uses to decide whether simulation.fabm gets
    # set at all (fabm.source == "ERSEM" and fabm.ERSEM.file truthy) is the
    # only way to keep this safe.
    fabm_cfg = config.get("fabm", {})
    if fabm_cfg.get("source") == "ERSEM" and fabm_cfg.get("file"):
        boundaries_fabm = config.get("boundaries", {}).get("fabm", {})
        boundaries_fabm_source = boundaries_fabm.get("source")
        # WOA vs CMEMS: same on_grid/climatology distinction as
        # boundaries.baroclinic's own WOA/CMEMS branches above -- a global
        # climatology vs a real time series already at the boundary points.
        # Which TRACERS get a boundary at all is config-driven either way
        # (boundaries.fabm.<source>.tracers -- see that ParameterSpec's own
        # help text for why this isn't a fixed list).
        _fabm_grid_kwargs = {
            "WOA": {"on_grid": False, "climatology": True},
            "CMEMS": {"on_grid": True, "climatology": False},
        }.get(boundaries_fabm_source)
        _fabm_tracers = boundaries_fabm.get("tracers") or {}
        if _fabm_grid_kwargs is not None and _fabm_tracers:
            _fabm_folder = Path(boundaries_fabm.get("folder", ""))
            for _tracer, _spec in _fabm_tracers.items():
                entries += [
                    {"target": f"open_boundary.{_tracer}", "kind": "boundary_type", "boundary_condition_type": "SPONGE"},
                    {
                        "target": f"open_boundary.{_tracer}.values",
                        "kind": "file",
                        "file": str(_fabm_folder / _spec["file"]),
                        "variable": _spec["variable"],
                        **_fabm_grid_kwargs,
                    },
                ]

    return entries
