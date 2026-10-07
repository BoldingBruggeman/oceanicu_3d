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
    # Same setdefault, same caveat (TUI/web's pre-built schema.json path
    # doesn't run this function either -- export explicitly there too):
    # bridges fabm.ERSEM.file (this project's own custom "fabm" role) to
    # simulation.fabm (pygetm-config's core field, what codegen._emit_
    # simulation actually bakes into the Simulation() call) -- see
    # derive_config_value_overrides's own docstring for why this needs to
    # be a real deriver, not just oceanicu_driver.py's own main() setting
    # it by hand (real gap, caught 2026-09-26: that only ever helped
    # oceanicu_driver.py's own entrypoint, not plain `pygetm-config run`/
    # `dump-python` or the TUI).
    os.environ.setdefault(
        "PYGETM_CONFIG_VALUE_DERIVERS",
        f"{Path(__file__)}:derive_config_value_overrides",
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
    # so this is declared here and passed to both choices below instead.
    #
    # shortwave_method/longwave_method used to live here too (a cfg_airsea.py-
    # era leftover from before simulation.airsea.shortwave_method/
    # longwave_method existed as real, introspected pygetm.airsea.
    # FluxesFromMeteo fields in their own right) -- removed 2026-09-07 (per
    # user: "it seems short and long wave radiation methods are set two
    # places in airsea and meteo?"). They were never real pygetm inputs from
    # here; set_meteo_data's own radiation_source-consistency check read
    # this COPY instead of the real simulation.airsea value, so the two
    # could silently drift apart (a real, reproduced case: simulation.
    # airsea's own value got updated to NET_FLUX for radiation_source:
    # 'net', this copy didn't, and set_meteo_data warned using stale data).
    # set_meteo_data now reads the real value directly -- see its own
    # docstring/code for exactly where.
    _meteo_shared = (
        ParameterSpec(
            name="evaporation",
            type=TypeRef(kind="scalar", scalar_type="bool"),
            default=True,
            help="derive evaporation from latent heat flux (cfg.meteo.evaporation, shared across sources)",
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

    # CMIP6-raw: meteo read directly from the RAW (not bias-corrected) CMIP6
    # archive -- /data/CMIP6/{model}/{scenario}/ (CMIP6_RAW_FOLDER, see
    # machines.yaml), one whole-period file per variable per experiment
    # (fetched via ocean-data's DataLoader, either Pangeo/GCS or ESGF's own
    # prefer_streaming=False full-file-download path -- see that repo's
    # esgf_loader.py). No net_sw/net_lw composite exists in the raw archive
    # (that's a bias-correction-pipeline-computed quantity) -- only
    # 'components' and 'pseudo_tcc' apply, not 'net'.
    _meteo_cmip6_raw_radiation = (
        ParameterSpec(
            name="radiation_source",
            type=TypeRef(kind="scalar", scalar_type="str", nullable=True),
            default="pseudo_tcc",
            choices=("components", "pseudo_tcc"),
            help=(
                "'components' | 'pseudo_tcc' (default) -- which RAW CMIP6 "
                "radiation data scripts/meteo.py:set_meteo_data uses. No "
                "'net' option here (unlike meteo.CMIP6.radiation_source): "
                "net_sw/net_lw are a bias-correction-pipeline composite, "
                "not a raw CMIP6 variable. 'components': rsds-rsus and "
                "rlds-rlus, subtracted at runtime from the raw files. "
                "'pseudo_tcc': same clearness-index-derived cloud-fraction "
                "proxy as meteo.CMIP6.radiation_source='pseudo_tcc', fed "
                "from the raw rsds file instead of the bias-corrected one. "
                "Whichever is chosen must be matched with the right "
                "shortwave_method/longwave_method (NET_FLUX=-1 for "
                "'components', a bulk-formula method e.g. the default 1 "
                "for 'pseudo_tcc') -- see set_meteo_data's own docstring."
            ),
            importance=Importance.ADVANCED,
        ),
    )

    meteo = make_provider_slot(
        "meteo",
        {
            # humidity_measure differs by source (DEW_POINT_TEMPERATURE for ERA5
            # vs SPECIFIC_HUMIDITY for CMIP6/CMIP6-raw, per cfg_airsea.py) but
            # that's a fixed consequence of the source choice, not itself a
            # configurable field -- not modeled as a param here.
            "ERA5": _meteo_shared,
            "CMIP6": _meteo_shared + _CMIP6_SHARED + _meteo_cmip6_radiation,
            "CMIP6-raw": _meteo_shared + _CMIP6_SHARED + _meteo_cmip6_raw_radiation,
            # For simulation.airsea.type: Fluxes (prescribed taux/tauy/sp/shf/
            # swr/pe) ONLY -- reads each field as a real time/spatially-varying
            # NetCDF file instead of Fluxes' own static YAML constants. Added
            # 2026-09-14 per user: constant (Fluxes' own static values, no
            # meteo section needed) or from-file (this) should both be
            # straightforward. Only `folder` needed (make_provider_slot's
            # shared base) -- see derive_data_assignments' own "Fluxes" branch
            # below for the fixed flux_<field>_????.nc naming convention (one
            # glob per field, all six always required, matching ERA5's own
            # all-or-nothing pattern -- a field you want to stay constant
            # instead just means not using this provider at all).
            "Fluxes": (),
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

    # CMIP6-only: river_flows_future_{scenario}.nc is monthly-mean,
    # timestamped at month-END -- its own real coverage only starts
    # 2015-01-31, so any run/chunk requesting 2015-01-01 hits a real
    # ~30-day gap before that first record (found running
    # running/check_inputs.py's --extended-dry-run, 2026-09-24). ocean-prep's
    # river-projection --disaggregate now also writes a day-of-year-shaped
    # daily sibling (river_flows_future_{scenario}_daily.nc, real coverage
    # starting exactly 2015-01-01) -- this flag switches scripts/rivers.py's
    # add_rivers/set_river_data to read that file instead. Default False:
    # existing configs keep reading the monthly file unchanged.
    _river_cmip6_only = (
        ParameterSpec(
            name="daily",
            type=TypeRef(kind="scalar", scalar_type="bool"),
            default=False,
            help=(
                "read the daily-disaggregated river_flows_future_"
                "{scenario}_daily.nc instead of the monthly-mean file -- "
                "closes the monthly file's ~30-day start-of-scenario "
                "coverage gap (its first record is month-end-stamped, "
                "2015-01-31, not 2015-01-01)"
            ),
            importance=Importance.BASIC,
        ),
        # No real future-projected nutrient product exists (see
        # set_river_data's docstring) -- flow gets the real splice above,
        # but nutrients beyond EMORID's own real coverage (2024-12-31) are
        # held at a MONTHLY CLIMATOLOGY built from that same real record,
        # not a real projection. Three pre-generated baseline variants sit
        # in ${RIVER_FOLDER} (EMORID_nutrient_climatology_monthly_{this}.
        # nc, site x month=12, all 6 tracers) -- 5yr/10yr/full differ
        # materially at some stations (confirmed 2026-10-01: AURAJOKI's
        # NO3 and Adour's Si both show real, baseline-dependent shifts, not
        # just noise), so this is a real modeling choice, not cosmetic.
        # Day-of-year climatology was ALSO generated and compared (noisier,
        # especially for Si -- fewer real samples per calendar day than
        # per calendar month) but dropped entirely: per user, 2026-10-01,
        # pygetm's own climatology support only handles 12 monthly records,
        # not 366 daily ones.
        ParameterSpec(
            name="nutrient_climatology",
            type=TypeRef(kind="scalar", scalar_type="str"),
            default="5yr",
            choices=("5yr", "10yr", "full"),
            help=(
                "baseline period for the monthly river-nutrient climatology "
                "used beyond EMORID's own real record (2024-12-31) -- "
                "'5yr'=2020-2024, '10yr'=2015-2024, 'full'=1993-2024. Flow "
                "itself is unaffected (always the real CMIP6 projection, "
                "see `daily` above); this only governs how nutrient "
                "concentrations are extended into the future."
            ),
            importance=Importance.BASIC,
        ),
    )

    river_discharge = make_provider_slot(
        "river_discharge",
        {
            "emorid": _river_shared_fields,
            "CMIP6": _river_shared_fields + _CMIP6_SHARED + _river_cmip6_only,
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
        help="FABM state variable name -> {file, variable, boundary_condition_type} "
        "for tracers that get a boundary + real values from this source. "
        "boundary_condition_type is optional, defaults to SPONGE (cfg_fabm.py's own "
        "real, only-ever-used value) -- other valid values are the same "
        "boundary_condition_type names data_assignments' own kind=boundary_type "
        "entries accept (ZERO_GRADIENT, CLAMPED, SPONGE; FLATHER_ELEV/"
        "FLATHER_TRANSPORT/SOMMERFELD don't apply to a 3D tracer), useful for "
        "testing a tracer's sensitivity to boundary treatment without editing "
        "Python. `file`/`variable` are REQUIRED for SPONGE/CLAMPED (both read real "
        "prescribed values at the boundary) but OPTIONAL for ZERO_GRADIENT (computed "
        "from the interior, never reads a file) -- omit them entirely for a tracer "
        "you just want pinned to ZERO_GRADIENT explicitly. Tracers NOT listed here "
        "keep pygetm's own ZERO_GRADIENT default "
        "(every Tracer, FABM ones included, gets ArrayOpenBoundaries(self, "
        "ZERO_GRADIENT) at construction, before any config runs -- see "
        "pygetm/tracer.py's own Tracer.__init__) and their fabm.yaml-declared "
        "initial_value for the interior IC -- not every FABM state variable needs "
        "an explicit boundary/IC override. `file` is resolved relative to this "
        "choice's own `folder`.\n\n"
        "ic_file/ic_variable (2026-10-01): OPTIONAL, CMEMS-only, free keys in "
        "this same per-tracer mapping (no separate ParameterSpec needed -- this "
        "field's own type is already a generic string mapping) -- a separate, "
        "grid-shaped file/variable for this tracer's one-time initial condition, "
        "resolved against the CMEMS choice's own sibling `ic_folder` field, NOT "
        "`folder`/`variable` (the real boundary-forcing product/variable name, "
        "not usable as a full-domain IC -- see `ic_folder`'s own comment in "
        "oceanicu_providers.py for why). Read by scripts/hydrography.py's own "
        "set_hydrography_ic, not here.",
        importance=Importance.BASIC,
    )

    # Shared between CMEMS and CMIP6 (2026-10-02, real gap hit directly:
    # a genuine NSe/CMEMS run spanning 2010-2011 crashed with no
    # dissic/talk boundary at all -- nse_cmems.yaml's own tracers never
    # had O3_c/O3_TA entries, since the real CMEMS product
    # (bio_carbon_new) only starts 2024-02-28/07-29, same real-data gap
    # CMIP6 already has its own historical-bridge splice for. Not a
    # CMIP6-only concern: EVERY source whose real dissic/talk file
    # doesn't reach back far enough needs the SAME bridge file, not a
    # separate mechanism) -- see derive_data_assignments' own
    # _fabm_hist_files dict for how each source's bridge file set
    # differs (CMIP6: all 6 tracers, since its own real projection file
    # only starts 2015; CMEMS: dissic/talk only, since CMEMS's own
    # no3/po4/si/o2 file already has full real historical coverage).
    #
    # Two real methods exist (2026-10-01, per user: "we might want to
    # use both methods later"), kept available side by side rather than
    # one replacing the other:
    #   cycled_climatology (default, unchanged behavior) -- a flat
    #     day-of-year climatology cycled from the real ~2-year ANFC
    #     record, no secular trend. Generated by a since-deleted
    #     scratchpad script (2026-09-25); the file itself
    #     (bio_carbon_climatology_cycled_20100101_20141231.nc) still
    #     exists.
    #   pml_trend -- a real secular-trend extrapolation (Atlantic/
    #     NW-shelf segments) + a real salinity regression (Baltic
    #     segment 7), both reusing published coefficients from
    #     Partridge et al. (PML, Dec 2025) directly rather than
    #     independently re-fitting our own (confirmed 2026-10-01: our
    #     own regional GLODAP subset is too sparse for that -- see
    #     docs/pml-amm7-ersem-comparison.md and ocean-prep/cli/
    #     derive_historical_dic_ta.py, the script that builds
    #     bio_carbon_pml_trend_20100101_20141231.nc). Default stays
    #     cycled_climatology (unchanged, already-proven behavior) until
    #     pml_trend has been validated against a real run -- an
    #     explicit opt-in, not a silent switch.
    _dic_ta_historical_method_param = ParameterSpec(
        name="dic_ta_historical_method",
        type=TypeRef(kind="scalar", scalar_type="str"),
        default="cycled_climatology",
        choices=("cycled_climatology", "pml_trend"),
        help="which historical-bridge source dissic/talk use, for whatever period "
        "this source's own real dissic/talk product doesn't reach back to -- "
        "'cycled_climatology' (default, flat day-of-year climatology, no trend) or "
        "'pml_trend' (real secular-trend extrapolation + Baltic salinity regression, "
        "Partridge et al. 2025's own published coefficients -- see docs/"
        "pml-amm7-ersem-comparison.md). Only affects dissic/talk; no3/po4/si/o2 are "
        "unaffected either way.",
        importance=Importance.BASIC,
    )

    boundary_fabm = make_provider_slot(
        "boundaries.fabm",
        {
            # WOA: a global climatology (on_grid=False, climatology=True,
            # cycling the same 12-month pattern all run) -- mirrors
            # boundary_baroclinic's own WOA branch.
            "WOA": (_fabm_tracers_param,),
            # CMEMS: real biogeochemistry data already sitting at the
            # boundary points (on_grid=True always). `climatology` picks
            # between reading it as a real, non-cycling time series
            # (default -- e.g. a historical/near-term run covering the
            # period the real CMEMS file actually spans) or cycling a
            # single representative year (e.g. a 12-month mean derived from
            # that same file) for periods no real data can cover at all --
            # a future CMIP6-scenario run's own future years, since no
            # reliable CMIP6-projected biogeochemical boundary is
            # achievable (per user, 2026-09-07: "we have to do the best we
            # can"). pygetm.core.Array.set's own climatology kwarg already
            # supports on_grid=True + climatology=True together (confirmed
            # directly against pygetm.input.InputManager.add's docstring --
            # climatology only requires "a single climatological year...
            # representative for any true year", independent of on_grid) --
            # this was previously unreachable here only because
            # derive_data_assignments hardcoded climatology=False for every
            # CMEMS use, not a real pygetm limitation.
            "CMEMS": (
                _dic_ta_historical_method_param,
                ParameterSpec(
                    name="climatology",
                    type=TypeRef(kind="scalar", scalar_type="bool"),
                    default=False,
                    help="cycle this file as a single representative year "
                    "(pygetm's own climatology=True) instead of reading it "
                    "as a real, non-cycling time series -- see this choice's "
                    "own comment in oceanicu_providers.py for when to use "
                    "each.",
                    importance=Importance.ADVANCED,
                ),
                # Separate from `tracers[*].file` (2026-10-01, real bug hit
                # directly, twice): that field is a real, nbdyp-shaped
                # (boundary-points-only) product -- correct for the SPONGE
                # boundary read, but structurally unusable as a full-domain
                # initial condition (no horizontal grid dimension for
                # pygetm.input's own vertical_interpolation to find). First
                # attempt pointed a (then-named) `ic_file` at bio_monthly_
                # climatology.nc instead, on the assumption that file was a
                # full regional grid (matching the OLD, never-actually-run
                # CMEMS fabm boundary design this replaced) -- confirmed
                # directly on bb-server1 (ncdump -h) that it is ALSO
                # (time=12, nbdyp=310, depth=57): every CMEMS bio product in
                # this project turned out to be boundary-point-shaped, none
                # usable as a full-domain IC. There is no genuinely
                # grid-shaped CMEMS/CMIP6 biogeochemistry product anywhere
                # in this project's data (confirmed directly, same session)
                # -- WOA's own global climatology (/data/FABM/woa_{n,p,i,
                # o}.nc) is the only one, so it's the fallback IC source
                # here too, same as WOA's own tracers[*].file already is
                # for ITS own boundary+IC. ic_folder (this field) + each
                # tracer's own OPTIONAL ic_file/ic_variable (free keys in
                # `tracers[*]`, no separate ParameterSpec needed -- that
                # field's own type is already a generic string mapping)
                # point at WOA's real per-tracer file/variable naming
                # (woa_n.nc/n_an, woa_p.nc/p_an, woa_i.nc/i_an, woa_o.nc/
                # o_an -- same convention boundaries.fabm.WOA's own
                # `tracers` already uses) -- deliberately NOT reusing
                # `folder` (this choice's own boundary-forcing folder) nor
                # `tracers[*].variable` (this choice's own boundary
                # variable name, e.g. "no3", which doesn't match WOA's
                # "n_an" convention). Neither ic_folder nor a tracer's own
                # ic_file/ic_variable set -> no explicit tracer IC is set
                # from this source at all (see scripts/hydrography.py's
                # own set_hydrography_ic for the fallback -- either fabm.
                # ERSEM.restart_file's seed, if configured, or (absent that
                # too) each tracer's own fabm.yaml-declared initial_value,
                # same as any tracer not listed in `tracers` at all).
                ParameterSpec(
                    name="ic_folder",
                    type=TypeRef(kind="scalar", scalar_type="str", nullable=True),
                    default=None,
                    help="OPTIONAL: folder for a separate, grid-shaped (NOT "
                    "boundary-point-shaped) initial-condition read per "
                    "tracer -- see each tracer's own OPTIONAL ic_file/"
                    "ic_variable keys in `tracers`, and this field's own "
                    "comment in oceanicu_providers.py for why this can't "
                    "just reuse `folder`/`tracers[*].variable`. Typically "
                    "${BOUNDARY_FOLDER_FABM_WOA}, reusing WOA's own real "
                    "per-tracer files (no genuinely grid-shaped CMEMS/CMIP6 "
                    "biogeochemistry product exists in this project).",
                    importance=Importance.BASIC,
                ),
                _fabm_tracers_param,
            ),
            # CMIP6: same real time series shape as CMEMS (on_grid=True,
            # climatology=False) -- boundary-VALUES only, mirrors
            # boundary_baroclinic's own CMIP6 branch (_CMIP6_SHARED gives
            # model/scenario, filling folder_template's {model}/{scenario}
            # placeholders). NOT a valid initial-condition source -- mirrors
            # hydrography.py's set_hydrography_ic, which only ever accepts
            # WOA/CMEMS for the IC (`if source not in ("WOA", "CMEMS"):
            # return`); CMIP6 delta-change output has no equivalent
            # "monthly_ic" snapshot file convention. See scripts/fabm.py's
            # own configure_fabm docstring for the matching IC-side gate.
            "CMIP6": _CMIP6_SHARED + (_fabm_tracers_param, _dic_ta_historical_method_param),
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
            # Explicit "off" choice -- previously FABM could only be disabled
            # implicitly (omit the whole `fabm:` section, or set source:
            # ERSEM with no `file`), which isn't discoverable in a TUI/web
            # frontend's own source dropdown (ERSEM was the ONLY visible
            # choice). oceanicu_driver.py's `if fabm_source == "ERSEM":`
            # gate (and run_fabm_data_script's own no-op-if-unconfigured
            # design) already skip every FABM data-setting step for any
            # OTHER source value, "none" included -- no other code change
            # needed. Zero extra params: the 5 shared base params (folder,
            # folder_template, ...) still show up (make_provider_slot always
            # merges those in) but are genuinely unused for this choice,
            # same as any other minimal provider.
            "none": (),
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
                ParameterSpec(
                    name="restart_file",
                    type=TypeRef(kind="path", nullable=True),
                    default=None,
                    help=(
                        "optional pygetm restart-format NetCDF (written by add_restart()/"
                        "read by load_restart() -- NOT a plain forcing/climatology file; "
                        "may also carry temperature/salinity/other physical fields, which "
                        "are ignored here) to seed FABM's OWN state variables from, ONLY "
                        "on a genuine fresh start -- e.g. a converged 'perpetual ERSEM' "
                        "state reused across short test runs. Read by scripts/hydrography."
                        "py's own set_hydrography_ic (NOT scripts/fabm.py's configure_fabm "
                        "-- that hook runs on every chunk unconditionally, including "
                        "restart continuations, where this must NOT re-apply; hydrography."
                        "data_script's own call site already skips it under `if not args."
                        "load_restart`), which temporarily narrows sim.output_manager."
                        "fields to just the FABM state variable names before calling "
                        "sim.load_restart() -- physical fields (temp/salt/u/v/...) are "
                        "never touched, even if also present in this file. Leave unset "
                        "for a normal run (FABM tracers keep their own fabm.yaml "
                        "initial_value, or whatever boundaries.fabm.<source> IC already "
                        "sets)."
                    ),
                    importance=Importance.BASIC,
                ),
                ParameterSpec(
                    name="ghg_scenario",
                    type=TypeRef(kind="scalar", scalar_type="str", nullable=True),
                    default=None,
                    help=(
                        "which SSP scenario's atmospheric CO2/N2O/N-deposition pathway "
                        "scripts/fabm.py's configure_fabm splices in past the historical "
                        "GHG file's own real 2014-12 end (e.g. 'ssp126', 'ssp245', "
                        "'ssp370', 'ssp585' -- matching files already on disk under "
                        "${GHG_CONCENTRATION_FOLDER}). ONLY consulted when boundaries.fabm "
                        "isn't itself CMIP6-scenario-driven (a real CMIP6 run always reuses "
                        "boundaries.fabm.CMIP6.scenario instead, deliberately, so atmosphere "
                        "and ocean boundary tracers share the same scenario -- see "
                        "configure_fabm's own comment) -- this is CMEMS/WOA's own "
                        "equivalent, since those sources have no scenario concept at all. "
                        "Leave unset only for a run that never extends past 2014-12-31: "
                        "configure_fabm raises loudly at setup time otherwise, rather than "
                        "letting pygetm's own TemporalInterpolation crash deep into the run "
                        "once it runs out of historical data."
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
# config-value deriver -- registered via PYGETM_CONFIG_VALUE_DERIVERS
# (pygetm_config.providers.derive_config_value_overrides), so ALL THREE
# generation modes (oceanicu_driver.py direct/--dump-python, TUI/web
# 'Generate script') get the SAME simulation.fabm value -- not just
# whichever ran oceanicu_driver.py's own main() (real gap, caught
# 2026-09-26: setting fabm.ERSEM.file in the TUI alone produced a
# generated script whose fabm= always fell back to None, since only
# oceanicu_driver.py's main() ever bridged fabm.ERSEM.file to
# simulation.fabm by hand -- the TUI's "Generate script" path never ran
# that code at all).
#
# Reads the VALIDATED, choice-flattened config shape, same as
# derive_data_assignments below -- config['fabm']['file'] directly, not
# config['fabm']['ERSEM']['file'] (see that function's own comment on
# why). Must stay pygetm-free (codegen.py calls this too, at GENERATION
# time) and side-effect-free (returns overrides for the caller to apply/
# consult, never mutates `config`).
def derive_config_value_overrides(config: dict) -> dict:
    fabm_cfg = config.get("fabm") or {}
    if fabm_cfg.get("source") == "ERSEM" and fabm_cfg.get("file"):
        return {"simulation.fabm": fabm_cfg["file"]}
    return {}


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

    # runtime.calendar (see schema._build_runtime_section) forces the WHOLE
    # simulation onto a non-standard calendar (currently only 'noleap', to
    # match a raw CMIP6 model like GFDL-ESM4 -- see driver/scripts/meteo.py)
    # -- every OTHER real-world-calendar data source the sim reads then
    # needs a matching pre-converted copy, or pygetm's own calendar check
    # raises (Concatenate(...) is incompatible with simulation calendar
    # noleap). '_noleap' is the fixed suffix these pre-converted copies use
    # (ocean-prep's run-tidal-boundaries/run-delta-boundaries with
    # --calendar noleap for bdy_2d/bdy_3d, a one-off convert_calendar +
    # to_netcdf for EMORID rivers -- see nse_tidal_bdy_noleap.yaml /
    # nse_delta_bdy_noleap.yaml / nse_delta_bdy_historical_noleap.yaml).
    # Empty string when calendar is None/'standard' (the overwhelming
    # majority of configs) -- every existing experiment's file paths stay
    # byte-identical to before this was added.
    _calendar_suffix = (
        "_noleap" if config.get("runtime", {}).get("calendar") == "noleap" else ""
    )

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
        #
        # Historical/scenario splice (same trick as meteo's own CMIP6
        # splicing in scripts/meteo.py's set_meteo_data, per user,
        # 2026-09-07): unlike meteo's per-year files, run-delta-boundaries
        # writes ONE file per whole period -- a fixed historical bridge
        # file (2010-01-01..2014-12-31, ocean-prep's own nse_delta_bdy_
        # historical.yaml -- see that config's own header for why this
        # period specifically) and one scenario file covering the real
        # future period (baroclinic.start_date/end_date, e.g.
        # 2015-01-01..2100-12-31 for ssp126). Both real files already exist
        # on disk (confirmed 2026-09-07). Simpler than meteo's glob-based
        # splice: `file:` accepts an exact-path LIST as-is (codegen.py's
        # own list branch, no glob/wildcard resolution involved at all,
        # unlike expand_year_glob), so this just concatenates the two
        # whole-period files -- pygetm.input.from_nc reads them as one
        # continuous series, same mechanism as meteo's per-year list.
        # Unconditional (not start/stop-gated): a run entirely within one
        # period only ever touches its own file's real time range, so
        # listing both is harmless even then -- no need to thread runtime.
        # stop into this deriver the way meteo's script-hook needed to.
        _HIST_START, _HIST_END = "20100101", "20141231"

        def _cmip6_bdy_folder(experiment: str) -> Path:
            folder = Path(baroclinic.get("folder", ""))
            if baroclinic.get("folder_template"):
                folder = folder / baroclinic["folder_template"].format(
                    model=baroclinic.get("model", ""), scenario=experiment
                )
            return folder

        _fn_tmpl = baroclinic.get("filename_template", "")

        def _cmip6_bdy_files(variable: str) -> list[str]:
            hist_name = _fn_tmpl.format(variable=variable, start_date=_HIST_START, end_date=_HIST_END)
            scen_name = _fn_tmpl.format(
                variable=variable, start_date=baroclinic.get("start_date", ""), end_date=baroclinic.get("end_date", "")
            )
            if _calendar_suffix:
                hist_name = hist_name.removesuffix(".nc") + _calendar_suffix + ".nc"
                scen_name = scen_name.removesuffix(".nc") + _calendar_suffix + ".nc"
            hist_path = _cmip6_bdy_folder("historical") / hist_name
            scen_path = _cmip6_bdy_folder(baroclinic.get("scenario", "")) / scen_name
            return [str(hist_path), str(scen_path)]

        entries += [
            {"target": "open_boundary.temp.values", "kind": "file", "file": _cmip6_bdy_files("thetao"), "variable": "thetao", "on_grid": True, "climatology": False},
            {"target": "open_boundary.salt.values", "kind": "file", "file": _cmip6_bdy_files("so"), "variable": "so", "on_grid": True, "climatology": False},
        ]

    # boundaries.barotropic 2D z/u/v boundary VALUES differ by source too --
    # TPXO: a harmonic tidal-constituent lookup (kind="tpxo", keyed by the
    # open boundary's own lon/lat, no file+variable pair) vs CMEMS/CMIP6: a
    # real time series already sitting at the boundary points, read directly
    # (kind="file", zos/uo/vo) -- mirrors cfg_boundaries.py::data_2d exactly,
    # same TPXO-vs-generic-else split. Used to be three STATIC `kind: tpxo`
    # entries baked into every nse_*.yaml's own data_assignments -- harmless
    # only because every domain happened to also set source: TPXO; silently
    # wrong the moment one didn't (confirmed 2026-09-02: nse_cmems.yaml sets
    # boundaries.barotropic.source: CMEMS but the generated script still
    # called pygetm.input.tpxo.get, because nothing ever read that source
    # flag). Made dynamic here instead, matching boundaries.baroclinic's own
    # already-dynamic branch above -- this is also what makes a TUI-only
    # switch of boundaries.barotropic.source (no direct YAML edit) "just
    # work", same as it already does for boundaries.baroclinic.source.
    barotropic = config.get("boundaries", {}).get("barotropic", {})
    barotropic_source = barotropic.get("source")
    if barotropic_source == "TPXO":
        _tpxo_folder = barotropic.get("tpxo_folder", "")
        entries += [
            {"target": "open_boundaries.z", "kind": "tpxo", "tpxo_folder": _tpxo_folder, "tpxo_variable": "h", "on_grid": True},
            {"target": "open_boundaries.u", "kind": "tpxo", "tpxo_folder": _tpxo_folder, "tpxo_variable": "u", "on_grid": True},
            {"target": "open_boundaries.v", "kind": "tpxo", "tpxo_folder": _tpxo_folder, "tpxo_variable": "v", "on_grid": True},
        ]
    elif barotropic_source == "CMEMS":
        # Single file holding zos/uo/vo together -- matches
        # cfg_boundaries.py::data_2d's own generic branch, which reads all
        # three off one resolved `fn`.
        _folder = Path(barotropic.get("folder", ""))
        if barotropic.get("folder_template"):
            _folder = _folder / barotropic["folder_template"].format(
                setup=config.get("setup", ""),
                model=barotropic.get("model", ""),
                scenario=barotropic.get("scenario", ""),
            )
        _file = _folder / barotropic.get("filename_template", "").format(
            start_date=barotropic.get("start_date", ""), end_date=barotropic.get("end_date", "")
        )
        entries += [
            {"target": "open_boundaries.z", "kind": "file", "file": str(_file), "variable": "zos", "on_grid": True},
            {"target": "open_boundaries.u", "kind": "file", "file": str(_file), "variable": "uo", "on_grid": True},
            {"target": "open_boundaries.v", "kind": "file", "file": str(_file), "variable": "vo", "on_grid": True},
        ]
    elif barotropic_source == "CMIP6":
        # Same historical/scenario splice as boundaries.baroclinic's own
        # CMIP6 branch above (see that branch's comment for the full
        # reasoning) -- real files confirmed on disk for both periods
        # 2026-09-07, same NSe/CMIP6/{model}/{scenario}/bdy/ tree
        # baroclinic's own files sit in (bdy_2d_*.nc there, alongside
        # baroclinic's bdy_3d_{variable}_*.nc). ONE file holds zos/uo/vo
        # together (unlike baroclinic's one-file-per-variable), so this
        # only needs a single two-element file list, reused for all three
        # targets.
        _HIST_START, _HIST_END = "20100101", "20141231"

        def _cmip6_bdy_folder(experiment: str) -> Path:
            folder = Path(barotropic.get("folder", ""))
            if barotropic.get("folder_template"):
                folder = folder / barotropic["folder_template"].format(
                    setup=config.get("setup", ""),
                    model=barotropic.get("model", ""),
                    scenario=experiment,
                )
            return folder

        _fn_tmpl = barotropic.get("filename_template", "")
        _hist_name = _fn_tmpl.format(start_date=_HIST_START, end_date=_HIST_END)
        _scen_name = _fn_tmpl.format(
            start_date=barotropic.get("start_date", ""), end_date=barotropic.get("end_date", "")
        )
        if _calendar_suffix:
            _hist_name = _hist_name.removesuffix(".nc") + _calendar_suffix + ".nc"
            _scen_name = _scen_name.removesuffix(".nc") + _calendar_suffix + ".nc"
        _hist_file = _cmip6_bdy_folder("historical") / _hist_name
        _scen_file = _cmip6_bdy_folder(barotropic.get("scenario", "")) / _scen_name
        _files = [str(_hist_file), str(_scen_file)]
        entries += [
            {"target": "open_boundaries.z", "kind": "file", "file": _files, "variable": "zos", "on_grid": True},
            {"target": "open_boundaries.u", "kind": "file", "file": _files, "variable": "uo", "on_grid": True},
            {"target": "open_boundaries.v", "kind": "file", "file": _files, "variable": "vo", "on_grid": True},
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
    # BUT that only covers picking between FluxesFromMeteo's OWN swr/ql
    # sub-choices -- it says nothing about simulation.airsea not being
    # FluxesFromMeteo at all. Real, reproduced bug (2026-09-14): a config
    # with `simulation.airsea.type: Fluxes` (prescribed taux/tauy/sp/shf/
    # swr/pe -- a completely different pygetm.airsea class with no t2m/d2m/
    # u10/v10/tp/tcc attributes at all) but a `meteo:` section still active
    # (e.g. left over from copying a template) crashed at generation-time
    # call-site execution with `AttributeError: 'Fluxes' object has no
    # attribute 't2m'`.
    #
    # NOT fixed by silently skipping these entries when airsea.type !=
    # FluxesFromMeteo: per user, 2026-09-14, `Fluxes` with time/spatially-
    # varying values sourced from FILES (feeding taux/tauy/shf/swr/pe
    # directly, instead of FluxesFromMeteo's own bulk-formula inputs) is a
    # legitimate thing to want -- a silent skip would leave `Fluxes` at its
    # literal YAML defaults with NO real forcing at all, and LOOK like it
    # worked. So: meteo.source="Fluxes" is its own real branch below
    # (targets Fluxes' own field names), and a MISMATCHED combination
    # (an ERA5/CMIP6/CMIP6-raw source, which only know FluxesFromMeteo's
    # field names, paired with a non-FluxesFromMeteo airsea.type -- or,
    # symmetrically, meteo.source="Fluxes" paired with an airsea.type that
    # isn't "Fluxes") raises a clear, actionable error instead of either
    # crashing on an AttributeError deep in a set() call or silently doing
    # nothing.
    meteo = config.get("meteo", {})
    meteo_source = meteo.get("source")
    _airsea_type = config.get("simulation", {}).get("airsea", {}).get("type")
    _FLUXES_FROM_METEO_SOURCES = ("ERA5", "CMIP6", "CMIP6-raw")
    if meteo_source in _FLUXES_FROM_METEO_SOURCES and _airsea_type not in (None, "FluxesFromMeteo"):
        raise ValueError(
            f"meteo.source={meteo_source!r} derives data_assignments for FluxesFromMeteo's own fields "
            f"(t2m/d2m/u10/v10/sp/tp/tcc/swr/ql), but simulation.airsea.type={_airsea_type!r} -- those "
            "targets don't exist on that airsea class. Use meteo.source: Fluxes instead if you want "
            "file-sourced values feeding Fluxes' taux/tauy/shf/swr/pe directly, or set "
            "simulation.airsea.type back to FluxesFromMeteo, or remove the meteo: section if Fluxes' "
            "own static/data_assignments-set values are all you want."
        )
    if meteo_source == "Fluxes" and _airsea_type not in (None, "Fluxes"):
        raise ValueError(
            f"meteo.source='Fluxes' derives data_assignments for Fluxes' own fields (taux/tauy/sp/shf/"
            f"swr/pe), but simulation.airsea.type={_airsea_type!r} -- those targets don't exist on that "
            "airsea class. Set simulation.airsea.type: Fluxes, or use a different meteo.source that "
            "matches FluxesFromMeteo instead."
        )
    if meteo_source == "Fluxes":
        # Two real modes in ONE provider, not two: no `folder` configured
        # (default) means "constant" -- stay a valid, active source: Fluxes
        # choice (satisfies the discriminator, keeps this whole meteo:
        # section legal) that derives NOTHING, so simulation.airsea.Fluxes'
        # own static YAML values (taux/tauy/sp/shf/swr/pe) win untouched.
        # A real `folder` switches to "from file" -- per user, 2026-09-14:
        # "constant... but file method shall be configurable" -- configuring
        # the folder IS the switch, no separate mode flag needed.
        _folder_raw = meteo.get("folder")
        if _folder_raw:
            _folder = Path(_folder_raw)
            entries += [
                {"target": "simulation.airsea.taux", "kind": "file", "file": str(_folder / "flux_taux_????.nc"), "variable": "taux"},
                {"target": "simulation.airsea.tauy", "kind": "file", "file": str(_folder / "flux_tauy_????.nc"), "variable": "tauy"},
                {"target": "simulation.airsea.sp", "kind": "file", "file": str(_folder / "flux_sp_????.nc"), "variable": "sp"},
                {"target": "simulation.airsea.shf", "kind": "file", "file": str(_folder / "flux_shf_????.nc"), "variable": "shf"},
                {"target": "simulation.airsea.swr", "kind": "file", "file": str(_folder / "flux_swr_????.nc"), "variable": "swr"},
                {"target": "simulation.airsea.pe", "kind": "file", "file": str(_folder / "flux_pe_????.nc"), "variable": "pe"},
            ]
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
    elif meteo_source in ("CMIP6", "CMIP6-raw"):
        # t2m/qa/u10/v10/sp/tp deliberately have NO entry here at all (not
        # even a placeholder) -- set_meteo_data (scripts/meteo.py,
        # meteo.data_script's own default) sets all six UNCONDITIONALLY,
        # before its own radiation_source branching even starts, so a
        # static entry here would NEVER be the value actually used, only
        # ever immediately overwritten. A `kind: constant` placeholder used
        # to sit here for these six (and before that, an even-more-fragile
        # real `kind: file` read of the active scenario's own folder, which
        # could crash for years outside that one scenario's own coverage --
        # see the historical/scenario splice's own comment in set_meteo_data
        # for that story) -- removed entirely 2026-09-07, per user: "but if
        # we know it is overwritten - write them in the first place [why]".
        # Writing a value nothing ever reads is pure noise, and previously
        # WAS a real point of confusion (a constant sitting right next to
        # real file reads reads as a real, permanent value, not a
        # placeholder about to vanish a few lines later).
        #
        # tcc is genuinely different, and keeps its own real placeholder
        # below: set_meteo_data only overwrites it conditionally (when
        # radiation_source == "pseudo_tcc"); for "net"/"components" it's
        # never touched again after this, yet still needs SOME real value
        # (pygetm's own FluxesFromMeteo requires every field it reads to be
        # set before sim.start(), even one it then never actually consults
        # because shortwave_method/longwave_method route around it).
        entries += [
            {
                "target": "simulation.airsea.tcc",
                "kind": "constant",
                "constant_value": 0.5,
                "comment": (
                    "placeholder -- overwritten below by meteo.data_script "
                    "(set_meteo_data) when radiation_source: pseudo_tcc; "
                    "unused (but still required to have a real value) for "
                    "net/components"
                ),
            },
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
        # WOA vs CMEMS vs CMIP6: same on_grid distinction as boundaries.
        # baroclinic's own WOA/CMEMS/CMIP6 branches above -- a global
        # climatology (WOA) vs a real time series already at the boundary
        # points (CMEMS, CMIP6 delta-change output). Which TRACERS get a
        # boundary at all is config-driven either way (boundaries.
        # fabm.<source>.tracers -- see that ParameterSpec's own help text
        # for why this isn't a fixed list).
        #
        # CMEMS's own `climatology` is config-driven (boundaries.fabm.
        # CMEMS.climatology, default False) rather than hardcoded like WOA/
        # CMIP6 -- see that field's own ParameterSpec comment above for why
        # (a future CMIP6-scenario run's own future years need CMEMS cycled
        # as a stand-in climatology, since no real CMIP6-projected BGC
        # boundary is achievable; a historical/near-term run reads the same
        # source as a real, non-cycling time series instead).
        _fabm_grid_kwargs = {
            "WOA": {"on_grid": False, "climatology": True},
            # NOT boundaries_fabm["CMEMS"]["climatology"] -- validate_config's
            # choice-flattening puts the ACTIVE choice's own fields directly
            # on boundaries_fabm itself (see _fabm_tracers below, and
            # scripts/meteo.py's own set_meteo_data docstring for the same,
            # previously-reproduced pitfall).
            "CMEMS": {"on_grid": True, "climatology": bool(boundaries_fabm.get("climatology", False))},
            "CMIP6": {"on_grid": True, "climatology": False},
        }.get(boundaries_fabm_source)
        _fabm_tracers = boundaries_fabm.get("tracers") or {}
        if _fabm_grid_kwargs is not None and _fabm_tracers:
            _fabm_folder = Path(boundaries_fabm.get("folder", ""))
            if boundaries_fabm_source == "CMIP6" and boundaries_fabm.get("folder_template"):
                # Mirrors boundaries.baroclinic's own CMIP6 branch exactly
                # (model/scenario fill the folder_template placeholders).
                # Unlike that branch, `file` per tracer stays a literal
                # filename (not filename_template.format(variable=...)) --
                # real CMIP6 BGC delta-change output naming isn't confirmed
                # for NSe yet, same status as CMEMS's own tracers[*].file.
                _fabm_folder = _fabm_folder / boundaries_fabm["folder_template"].format(
                    model=boundaries_fabm.get("model", ""), scenario=boundaries_fabm.get("scenario", "")
                )
            # Historical/scenario splice (2026-09-25, per user), same trick
            # as boundaries.baroclinic/barotropic's own CMIP6 branches above
            # -- FABM nutrient boundaries had none until now, a real gap
            # found running the extended-dry-run over a pre-2015 period.
            # Per-tracer historical source, NOT the raw CMIP6 historical
            # experiment used first (rejected, per user: CMEMS is the more
            # correct real-observation source for this bridge period):
            #   no3/po4/si/o2: real, already-dated CMEMS reanalysis --
            #     bio_daily_20100101_20141231.nc, a pre-trimmed slice of
            #     BOUNDARY_FOLDER_FABM_CMEMS's own bio_daily_2010-01-01_to_
            #     2026-08-17.nc (trimmed to avoid overlapping the scenario
            #     file's own 2015+ coverage, which would break
            #     TemporalInterpolation's monotonic-time assumption --
            #     same reason baroclinic/barotropic's own historical files
            #     are dedicated 2010-2014-only files, not full-length ones).
            #   dissic/talk: no real CMEMS coverage exists that far back
            #     (bio_carbon_new only starts 2024-07-29) -- instead a
            #     day-of-year climatology built from that real ~2-year
            #     record (bio_carbon_climatology_cycled_20100101_
            #     20141231.nc, generated 2026-09-25; Feb 29 -- absent from
            #     the source window entirely -- falls back to Feb 28,
            #     same convention ocean-prep's own delta_change.py.
            #     _analog_date uses for an analogous gap).
            # BOUNDARY_FOLDER_FABM_CMEMS stays an UNRESOLVED "${VAR}"
            # literal here, like every other folder in this whole function
            # -- derive_data_assignments runs at GENERATION time (always on
            # orca, which never has this data or a matching data-roots-file),
            # not at the generated script's own runtime; os.environ.get
            # here was a real, caught bug (found 2026-09-25 testing this
            # exact splice) -- it read as unset during generation, silently
            # falling through to the plain single-file (no splice) case.
            # `Path("${VAR}") / "name"` still does plain string-join (no
            # actual filesystem resolution happens until the generated
            # script's own resolve_data_path(...) call, baked in later by
            # codegen), so this is safe.
            _cmems_fabm_folder = Path("${BOUNDARY_FOLDER_FABM_CMEMS}")
            # dissic/talk's own historical-bridge file -- see this choice's
            # own dic_ta_historical_method ParameterSpec comment above for
            # the two available methods and why both are kept. Date range
            # of the bridge file ITSELF differs by source (2026-10-02,
            # real gap hit directly): CMIP6's own real projection file
            # starts 2015-01-01, so its bridge only needs to cover
            # 2010-2014. CMEMS's own real dissic/talk product
            # (bio_carbon_new) only starts 2024-07-29 (confirmed directly
            # -- NOT 2024-02-28, an earlier, stale assumption) -- a bridge
            # covering only 2010-2014 would leave a real ~10-year gap
            # (2015-01-01 to 2024-07-28) with NEITHER a bridge NOR real
            # coverage, exactly the gap that broke a real NSe/CMEMS
            # 2010-2011 run. CMEMS's own bridge file is therefore extended
            # to cover 2010-01-01 to 2024-07-28 (the day before real
            # coverage begins, zero overlap) -- same derive_historical_
            # dic_ta.py script, just a longer --stop. Only regenerated for
            # pml_trend (the method actually selected for CMEMS, see
            # nse_cmems.yaml) -- cycled_climatology's own extended-range
            # equivalent does not exist (that method's generator script
            # was a since-deleted scratchpad, never recreated) and is
            # NOT safe to select for CMEMS until/unless it is.
            _dic_ta_historical_method = boundaries_fabm.get("dic_ta_historical_method", "cycled_climatology")
            _dic_ta_hist_file = {
                ("cycled_climatology", "CMIP6"): "bio_carbon_climatology_cycled_20100101_20141231.nc",
                ("pml_trend", "CMIP6"): "bio_carbon_pml_trend_20100101_20141231.nc",
                ("pml_trend", "CMEMS"): "bio_carbon_pml_trend_20100101_20240728.nc",
            }.get((_dic_ta_historical_method, boundaries_fabm_source))
            if boundaries_fabm_source == "CMEMS" and _dic_ta_hist_file is None:
                # cycled_climatology's own CMEMS-range (2010-01-01 to
                # 2024-07-28) equivalent does not exist -- that method's
                # generator script was a since-deleted scratchpad, never
                # recreated for this range (see dic_ta_historical_method's
                # own ParameterSpec comment). Fail loudly at generation
                # time rather than crash later with `Path / None` deep
                # inside this function, or -- worse -- silently resolve to
                # a short, still-gapped bridge file.
                raise ValueError(
                    "boundaries.fabm.CMEMS.dic_ta_historical_method=cycled_climatology has no real "
                    "bridge file covering CMEMS's own real dissic/talk gap (2010-01-01 to 2024-07-28) -- "
                    "use pml_trend instead, or build bio_carbon_climatology_cycled_20100101_20240728.nc "
                    "first."
                )
            # Per-source bridge-file set (2026-10-02, real gap hit
            # directly on a genuine NSe/CMEMS 2010-2011 run: no dissic/
            # talk boundary at all, since nse_cmems.yaml's own tracers
            # had no O3_c/O3_TA entries and CMEMS's own real dissic/talk
            # product -- bio_carbon_new -- only starts 2024-02-28/07-29).
            # CMIP6: ALL 6 tracers need a bridge -- its own real
            # projection file (river_flows_future-style delta-change
            # output) only starts 2015-01-01. CMEMS: only dissic/talk
            # need one -- no3/po4/si/o2's own real file (bio_daily_
            # 2010-01-01_to_2026-08-17.nc) already has full real
            # historical coverage, no bridge needed there at all. WOA
            # needs no bridge file set (empty dict) -- it's a global
            # climatology, not a real time series with any coverage gap
            # to bridge.
            # NOT a single dict literal keyed by boundaries_fabm_source (a
            # real bug hit directly, 2026-10-07, generating a genuine NSe/
            # WOA/validation/E combo -- WOA's own ERSEM-enabled boundaries.
            # fabm.WOA branch reaches this code too, now that
            # add_experiments.py supports it): a dict literal's values are
            # ALL evaluated eagerly, regardless of which key is later
            # looked up -- so the CMEMS/CMIP6 branches' own `_cmems_fabm_
            # folder / _dic_ta_hist_file` crashed with `Path / None` for
            # WOA even though neither branch is WOA's own and `.get(...,
            # {})` would have returned {} for it. Only construct the
            # branch that actually matches.
            if boundaries_fabm_source == "CMIP6":
                _fabm_hist_files = {
                    "no3": _cmems_fabm_folder / "bio_daily_20100101_20141231.nc",
                    "po4": _cmems_fabm_folder / "bio_daily_20100101_20141231.nc",
                    "si": _cmems_fabm_folder / "bio_daily_20100101_20141231.nc",
                    "o2": _cmems_fabm_folder / "bio_daily_20100101_20141231.nc",
                    "dissic": _cmems_fabm_folder / _dic_ta_hist_file,
                    "talk": _cmems_fabm_folder / _dic_ta_hist_file,
                }
            elif boundaries_fabm_source == "CMEMS":
                _fabm_hist_files = {
                    "dissic": _cmems_fabm_folder / _dic_ta_hist_file,
                    "talk": _cmems_fabm_folder / _dic_ta_hist_file,
                }
            else:
                # WOA -- no bridge file needed (global climatology, not a
                # real time series with any coverage gap to bridge).
                _fabm_hist_files = {}
            for _tracer, _spec in _fabm_tracers.items():
                # boundary_condition_type is optional per-tracer -- defaults
                # to SPONGE (cfg_fabm.py's own real, only-ever-used value)
                # when omitted.
                _bc_type = _spec.get("boundary_condition_type", "SPONGE")
                entries.append(
                    {"target": f"open_boundary.{_tracer}", "kind": "boundary_type", "boundary_condition_type": _bc_type}
                )
                if _bc_type == "ZERO_GRADIENT":
                    # ZERO_GRADIENT computes the boundary value FROM the
                    # interior, unlike SPONGE/CLAMPED -- it never reads
                    # prescribed values at all, so `file`/`variable` are
                    # optional (and simply unused if given) for this type;
                    # no .values entry is emitted. Real, user-caught gap:
                    # this used to unconditionally require `_spec["file"]`,
                    # which crashed with a bare KeyError for a tracer that
                    # genuinely never needed one.
                    continue
                if "file" not in _spec or "variable" not in _spec:
                    raise ValueError(
                        f"boundaries.fabm.{boundaries_fabm_source}.tracers.{_tracer}: "
                        f"boundary_condition_type={_bc_type!r} needs both 'file' and "
                        "'variable' (only ZERO_GRADIENT can omit them -- SPONGE/CLAMPED "
                        "both read real prescribed values at the boundary)"
                    )
                _fabm_file = str(_fabm_folder / _spec["file"])
                # _fabm_hist_files is already scoped per-source above (empty
                # for WOA, CMIP6-vs-CMEMS-specific otherwise) -- no separate
                # "is this CMIP6" check needed here any more.
                if _spec["variable"] in _fabm_hist_files:
                    _fabm_file = [str(_fabm_hist_files[_spec["variable"]]), _fabm_file]
                entries.append(
                    {
                        "target": f"open_boundary.{_tracer}.values",
                        "kind": "file",
                        "file": _fabm_file,
                        "variable": _spec["variable"],
                        **_fabm_grid_kwargs,
                    }
                )

    return entries
