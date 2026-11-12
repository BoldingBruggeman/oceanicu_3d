"""A populated `pygetm_config.providers` registry for OceanICU. Provider
names/extra params below are taken directly from OceanICU's actual code --
`lib/cfg_airsea.py` (meteo: ERA5/CMIP6), `lib/cfg_boundaries.py` (barotropic:
TPXO/CMEMS/CMIP6, baroclinic: CMEMS/WOA), `lib/cfg_ic.py` (hydrography:
CMEMS/WOA/constant) -- the FULL set of real alternatives per role, not just
whichever single choice any one domain config happens to use (every domain
config only ever picks ONE choice per role; the `source:`-discriminated
`ChoiceSpec` this module builds, via `make_provider_slot`, supports all of
them at once).

Registered through the PYGETM_CONFIG_PROVIDERS / PYGETM_CONFIG_VALUE_DERIVERS
environment variables (set by oceanicu_driver.py and by register_oceanicu_
providers() below), not a packaging entry point: oceanicu_3d is a script
directory, not an installed package.

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

import sys
from pathlib import Path

import yaml

from pygetm_config import loader
from pygetm_config.model import ChoiceSpec, Importance, ParameterSpec, TypeRef
from pygetm_config.providers import make_provider_slot

# Absolute: pygetm_config.providers.load_dotted_target resolves a "*.py:name"
# target relative to the current working directory, not this file. Each hook
# in scripts/ is self-contained (codegen embeds a single function's source).
_SCRIPTS_DIR = Path(__file__).parent / "scripts"
_RIVERS_SCRIPT_PATH = _SCRIPTS_DIR / "rivers.py"
_METEO_SCRIPT_PATH = _SCRIPTS_DIR / "meteo.py"
_HYDROGRAPHY_SCRIPT_PATH = _SCRIPTS_DIR / "hydrography.py"
_FABM_SCRIPT_PATH = _SCRIPTS_DIR / "fabm.py"

# model/scenario fill the {model}/{scenario} folder_template placeholders
# of every CMIP6-sourced role (boundaries.{barotropic,baroclinic}, meteo).
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
    # setdefault: an explicit export wins. Reaches every caller that builds the
    # schema live (oceanicu_driver.py, `pygetm-config run`/`dump`), not the
    # pygetm-free `pygetm-config edit --schema schema.json`/`web`, which must
    # export both variables explicitly (driver/README.md).
    import os

    os.environ.setdefault(
        "PYGETM_CONFIG_DATA_ASSIGNMENTS_DERIVERS",
        f"{Path(__file__)}:derive_data_assignments",
    )
    # Bridges fabm.ERSEM.file to simulation.fabm (and the mizer output group)
    # for every entrypoint -- see derive_config_value_overrides.
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
                "expressible as a data_assignments entry, plus the optional "
                "density conversion, convert_ts -- see "
                "pygetm_config.loader.run_hydrography_data_script's own "
                "docstring). Only runs when NOT loading from a restart. Same "
                "convention as river_discharge.data_script/meteo.data_script/"
                "PYGETM_CONFIG_PROVIDERS. 'constant' hydrography doesn't need "
                "this -- it's plain data_assignments (simulation.temp/"
                "simulation.salt, kind=constant), no Python at all."
            ),
            importance=Importance.BASIC,
        ),
        ParameterSpec(
            name="convert_ts",
            type=TypeRef(kind="scalar", scalar_type="bool"),
            default=False,
            help=(
                "convert the initial T/S to pyGETM's conservative temperature and "
                "absolute salinity (sim.density.convert_ts) after reading them. "
                "Turn on when the files hold practical salinity and potential "
                "(CMEMS) or in-situ (WOA) temperature; leave off when they are "
                "already CT/SA."
            ),
            importance=Importance.BASIC,
        ),
    )

    hydrography = make_provider_slot(
        "hydrography",
        {
            # CMEMS/WOA: fixed filenames under `folder`; data_script does the
            # monthly pick and optional convert_ts.
            "CMEMS": _hydrography_data_script,
            "WOA": _hydrography_data_script,
            # constant: uniform values, no files (the shared folder params
            # are present but unused).
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
            # CMEMS: <variable>/uo/vo from one file.
            "CMEMS": (
                ParameterSpec(
                    name="variable",
                    type=TypeRef(kind="scalar", scalar_type="str"),
                    default="zos",
                    choices=("zos", "zos_datum_corrected"),
                    help=(
                        "'zos' (default) | 'zos_datum_corrected' -- which SSH "
                        "variable to read from the resolved CMEMS boundary "
                        "file for open_boundaries.z. 'zos_datum_corrected' "
                        "removes a ~15-25 cm reference-level artifact at the "
                        "NSe Kattegat/Baltic segment (see "
                        "docs/kattegat_ssh_datum_bias.md) while keeping zos "
                        "unchanged everywhere else -- see "
                        "nse_bdy_create.yaml's segment 7 datum_correction "
                        "block for how it's produced."
                    ),
                    importance=Importance.BASIC,
                ),
            ),
            "CMIP6": _CMIP6_SHARED,
        },
        default="TPXO",
    )

    boundary_baroclinic = make_provider_slot(
        "boundaries.baroclinic",
        {
            "CMEMS": (),
            # WOA: its own folder, independent of hydrography.WOA.folder.
            "WOA": (),
            "CMIP6": _CMIP6_SHARED,
        },
        default="CMEMS",
    )

    # Shared by the meteo sources: they configure the shared FluxesFromMeteo,
    # not anything source-specific. (shortwave/longwave_method live only in
    # simulation.airsea; set_meteo_data reads them from there.)
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

    # ERA5 has no model/scenario: `folder` is the extraction's directory and
    # the era5_<var>_????.nc naming is fixed (derive_data_assignments).

    # meteo-CMIP6 only; boundaries' CMIP6 choices have no radiation.
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

    # CMIP6-raw: the raw (not bias-corrected) archive, one whole-period file
    # per variable per experiment; no net_sw/net_lw (a bias-correction
    # composite), so only 'components' and 'pseudo_tcc'.
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
            # humidity_measure follows from the source (oceanicu_driver.py).
            "ERA5": _meteo_shared,
            "CMIP6": _meteo_shared + _CMIP6_SHARED + _meteo_cmip6_radiation,
            "CMIP6-raw": _meteo_shared + _CMIP6_SHARED + _meteo_cmip6_raw_radiation,
            # For simulation.airsea.type Fluxes: every flux field read from
            # flux_<field>_????.nc under `folder` (derive_data_assignments).
            "Fluxes": (),
        },
        default="ERA5",
    )

    # One flat choice: "emorid" (the historical record) or "CMIP6" (delta-
    # change projections on the same EMORID stations, read by the same
    # add_rivers/set_river_data; `file` may hold {model}/{scenario}).
    _river_shared_fields = (
        ParameterSpec(
            name="file",
            type=TypeRef(kind="scalar", scalar_type="str"),
            help=(
                "river NetCDF filename, relative to `folder`/`folder_template`, used for "
                "the river POSITIONS (and the threshold) -- an EMORID file for "
                "source=emorid, 'river_flows_future_{scenario}.nc' for source=CMIP6 "
                "({model}/{scenario} are filled in), which also gives the projected "
                "flow. Discharge history and nutrients always come from "
                "${RIVER_FOLDER}/$RIVER_FILE (data-roots file), for both sources."
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

    # CMIP6 only. `daily`: the monthly file's first record is 2015-01-31, so
    # a run from 2015-01-01 needs the daily-disaggregated sibling.
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
        # No projected nutrients exist: beyond EMORID's record (2024-12-31)
        # they follow a monthly climatology of it. The baselines differ
        # materially at some stations, so the choice is exposed. pygetm's
        # climatology support takes 12 monthly records, not day-of-year.
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

    # boundaries.fabm is independent of hydrography and of `fabm`: nutrient
    # boundaries may come from another source than T/S. `tracers` maps each
    # FABM state variable that gets a boundary to its file/variable (or a
    # constant); read by derive_data_assignments (boundary type + values) and
    # by hydrography.py's set_hydrography_ic (one-time IC).
    _fabm_tracers_param = ParameterSpec(
        name="tracers",
        type=TypeRef(
            kind="mapping",
            nullable=True,
            # per-tracer keys: file/variable/boundary_condition_type/sponge (str),
            # constant (number). Union order matters: number before str.
            inner=TypeRef(
                kind="mapping",
                inner=TypeRef(
                    kind="union",
                    union_options=(
                        TypeRef(kind="scalar", scalar_type="float"),
                        TypeRef(kind="scalar", scalar_type="str"),
                    ),
                ),
            ),
        ),
        default=None,
        help="FABM state variable name -> {file, variable, boundary_condition_type, "
        "constant, sponge, ic_file, ic_variable} for tracers that get a boundary from "
        "this source. boundary_condition_type defaults to SPONGE (also ZERO_GRADIENT, "
        "CLAMPED). `file` (relative to `folder`) and `variable` are required for "
        "SPONGE/CLAMPED, unused for ZERO_GRADIENT. `constant: <value>` prescribes a fixed "
        "boundary value instead (e.g. a low background for plankton/detritus pools). "
        "`sponge` (SPONGE only) names an open_boundary_sponges entry. A different type "
        "on one boundary goes in that open_boundaries entry's variable_types. Unlisted "
        "tracers keep pygetm's ZERO_GRADIENT and their fabm.yaml initial_value.\n\n"
        "ic_file/ic_variable (CMEMS only): a grid-shaped file/variable under `ic_folder` "
        "for the one-time initial condition (read by hydrography.py's set_hydrography_ic).",
        importance=Importance.BASIC,
    )

    # Historical bridge for dissic/talk, for the years a source's own product
    # doesn't cover (CMIP6 starts 2015; CMEMS bio_carbon_new 2024-07-29):
    #   cycled_climatology -- day-of-year climatology of the ~2-year ANFC
    #     record, no trend (bio_carbon_climatology_cycled_20100101_20141231.nc;
    #     CMIP6 range only).
    #   pml_trend -- secular-trend extrapolation + Baltic salinity regression
    #     from Partridge et al. (PML, 2025) coefficients (ocean-prep
    #     derive_historical_dic_ta.py; docs/pml-amm7-ersem-comparison.md).
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
            # WOA: global climatology (on_grid=False, climatology=True).
            "WOA": (_fabm_tracers_param,),
            # CMEMS: values at the boundary points (on_grid=True). `climatology`
            # cycles the file as one representative year, for periods no
            # real data covers (e.g. a scenario run's future years).
            "CMEMS": (
                _dic_ta_historical_method_param,
                ParameterSpec(
                    name="climatology",
                    type=TypeRef(kind="scalar", scalar_type="bool"),
                    default=False,
                    help="cycle this file as a single representative year "
                    "(pygetm's own climatology=True) instead of reading it "
                    "as a real, non-cycling time series -- for periods "
                    "no real data covers.",
                    importance=Importance.ADVANCED,
                ),
                # ic_folder + per-tracer ic_file/ic_variable: a grid-shaped IC
                # source. Every CMEMS/CMIP6 bio product here is boundary-point
                # shaped (nbdyp), so `folder`/`tracers[*].variable` can't serve as
                # an IC; typically WOA's files (woa_n.nc/n_an, ...). Unset ->
                # restart_file or the fabm.yaml initial_value.
                ParameterSpec(
                    name="ic_folder",
                    type=TypeRef(kind="scalar", scalar_type="str", nullable=True),
                    default=None,
                    help="OPTIONAL: folder for a grid-shaped initial condition per "
                    "tracer (each tracer's ic_file/ic_variable in `tracers`). The "
                    "CMEMS boundary files only hold boundary points, so they can't "
                    "serve as an IC. Typically ${BOUNDARY_FOLDER_FABM_WOA}.",
                    importance=Importance.BASIC,
                ),
                _fabm_tracers_param,
            ),
            # CMIP6: boundary values only (on_grid=True, climatology=False);
            # not an IC source -- no CMIP6 "monthly_ic" files exist.
            "CMIP6": _CMIP6_SHARED + (_fabm_tracers_param, _dic_ta_historical_method_param),
        },
        default="WOA",
    )

    # Source-discriminated so another model can be added as a choice. FABM
    # needs both simulation.fabm (from `file`, via derive_config_value_
    # overrides) and the model choice itself. `folder` holds its input files.
    fabm = make_provider_slot(
        "fabm",
        {
            # Explicit "off" choice, visible in the TUI/web source dropdown;
            # every FABM step is skipped for any source other than ERSEM.
            "none": (),
            "ERSEM": (
                ParameterSpec(
                    name="file",
                    type=TypeRef(kind="path"),
                    default=None,
                    help="path to the fabm.yaml driving this run -- becomes simulation.fabm "
                    "(pygetm.Simulation's fabm= kwarg). Required to enable FABM.",
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

    # Dict order is the section order everywhere downstream (TUI tree,
    # generated YAML template): hydrography, boundaries, rivers, meteo, FABM.
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
# config-value deriver, registered via PYGETM_CONFIG_VALUE_DERIVERS so every
# entrypoint (driver, --dump-python, TUI/web "Generate script") gets the same
# values. Reads the validated, choice-flattened config (config['fabm']['file'],
# not ['fabm']['ERSEM']['file']); pygetm-free and side-effect-free, since
# codegen calls it at generation time.
def derive_config_value_overrides(config: dict) -> dict:
    overrides: dict = {}

    fabm_cfg = config.get("fabm") or {}
    if fabm_cfg.get("source") == "ERSEM" and fabm_cfg.get("file"):
        overrides["simulation.fabm"] = fabm_cfg["file"]

    # output.variable_groups.fabm_mizer: fish_c1..fish_c<nclass>, with nclass
    # read from the mizer instance in fabm.yaml (fallback 100, with a warning).
    # Only when some output file requests the group; always overrides the
    # YAML's placeholder (real Mizer diagnostics belong in another group).
    if any(
        "fabm_mizer" in (req.get("groups") or [])
        for file_entry in config.get("output", {}).get("files", [])
        for req in file_entry.get("variable_requests", [])
    ):
        _FISH_NCLASS_FALLBACK = 100
        nclass = _FISH_NCLASS_FALLBACK
        fabm_file = fabm_cfg.get("file")
        if fabm_file:
            try:
                fabm_file_resolved = fabm_file
                if (fabm_cfg.get("folder") and not Path(fabm_file).is_absolute()
                        and "$" not in fabm_file):
                    fabm_file_resolved = str(
                        Path(loader.resolve_data_path(fabm_cfg["folder"])) / fabm_file)
                fabm_yaml_path = Path(loader.resolve_data_path(fabm_file_resolved))
                fabm_yaml = yaml.safe_load(fabm_yaml_path.read_text())
                for inst in (fabm_yaml.get("instances") or {}).values():
                    if str(inst.get("model", "")).startswith("mizer/"):
                        nclass = int(inst["parameters"]["nclass"])
                        break
                else:
                    print(f"WARNING: no mizer instance found in {fabm_yaml_path} -- "
                          f"using fabm_mizer nclass={_FISH_NCLASS_FALLBACK}", file=sys.stderr)
            except (OSError, RuntimeError, KeyError, ValueError, yaml.YAMLError) as exc:
                print(f"WARNING: couldn't read nclass from fabm.yaml ({exc}) -- "
                      f"using fabm_mizer nclass={_FISH_NCLASS_FALLBACK}", file=sys.stderr)
        else:
            # No fabm.file (e.g. fabm.source none). Harmless: fabm_mizer
            # requests also require fish/fishing_pressure, so they are skipped
            # when FABM is off.
            print(f"WARNING: fabm.source={fabm_cfg.get('source')!r} has no fabm.file to read -- "
                  f"using fabm_mizer nclass={_FISH_NCLASS_FALLBACK}", file=sys.stderr)
        overrides["output.variable_groups.fabm_mizer"] = [f"fish_c{i}" for i in range(1, nclass + 1)]

    return overrides


# ----------------------------------------------------------------------
# data_assignments deriver, registered via PYGETM_CONFIG_DATA_ASSIGNMENTS_
# DERIVERS so every entrypoint computes the same boundaries/meteo entries.
# Same validated, choice-flattened config shape and pygetm-free rule as
# derive_config_value_overrides above.
_CMIP6_HIST_START, _CMIP6_HIST_END = "20100101", "20141231"


def _bdy_folder(role: dict, config: dict, scenario: str) -> Path:
    """A boundaries.<role>'s folder, with folder_template's {setup}/{model}/
    {scenario} filled in."""
    folder = Path(role.get("folder", ""))
    if role.get("folder_template"):
        folder = folder / role["folder_template"].format(
            setup=config.get("setup", ""), model=role.get("model", ""), scenario=scenario
        )
    return folder


def _cmip6_bdy_files(role: dict, config: dict, calendar_suffix: str, variable: str | None = None) -> list[str]:
    """[historical, scenario] boundary files of a CMIP6 boundaries.<role>.

    ocean-prep's run-delta-boundaries writes one file per whole period: a
    fixed historical bridge (2010-2014, nse_delta_bdy_historical.yaml) and
    the scenario file (role start_date..end_date). pygetm.input.from_nc reads
    the list as one series; listing both is harmless for a run inside one
    period. *variable* fills a per-variable filename_template (baroclinic)."""
    tmpl = role.get("filename_template", "")
    periods = [
        ("historical", _CMIP6_HIST_START, _CMIP6_HIST_END),
        (role.get("scenario", ""), role.get("start_date", ""), role.get("end_date", "")),
    ]
    files = []
    for experiment, start, end in periods:
        name = tmpl.format(variable=variable, start_date=start, end_date=end)
        if calendar_suffix:
            name = name.removesuffix(".nc") + calendar_suffix + ".nc"
        files.append(str(_bdy_folder(role, config, experiment) / name))
    return files


def derive_data_assignments(config: dict) -> list[dict]:
    entries: list[dict] = []

    # runtime.calendar noleap (raw CMIP6 models, e.g. GFDL-ESM4): every other
    # data source needs a pre-converted '_noleap' copy, or pygetm refuses to
    # mix calendars. Empty for the standard calendar.
    _calendar_suffix = (
        "_noleap" if config.get("runtime", {}).get("calendar") == "noleap" else ""
    )

    # boundaries.baroclinic temp/salt values (cfg_boundaries.py::data_3d):
    # WOA a global climatology, CMEMS/CMIP6 time series at the boundary
    # points. The boundary types stay in the YAML's data_assignments.
    baroclinic = config.get("boundaries", {}).get("baroclinic", {})
    baroclinic_source = baroclinic.get("source")
    if baroclinic_source == "WOA":
        _woa_folder = Path(baroclinic.get("folder", ""))
        entries += [
            {"target": "open_boundary.temp.values", "kind": "file", "file": str(_woa_folder / "woa_t.nc"), "variable": "t_an", "on_grid": False, "climatology": True},
            {"target": "open_boundary.salt.values", "kind": "file", "file": str(_woa_folder / "woa_s.nc"), "variable": "s_an", "on_grid": False, "climatology": True},
        ]
    elif baroclinic_source == "CMEMS":
        _cmems_folder = _bdy_folder(baroclinic, config, baroclinic.get("scenario", ""))
        _cmems_file = _cmems_folder / baroclinic.get("filename_template", "").format(
            start_date=baroclinic.get("start_date", ""), end_date=baroclinic.get("end_date", "")
        )
        entries += [
            {"target": "open_boundary.temp.values", "kind": "file", "file": str(_cmems_file), "variable": "thetao", "on_grid": True, "climatology": False},
            {"target": "open_boundary.salt.values", "kind": "file", "file": str(_cmems_file), "variable": "so", "on_grid": True, "climatology": False},
        ]
    elif baroclinic_source == "CMIP6":
        # One file per variable (bdy_3d_{variable}_...), historical bridge +
        # scenario file -- see _cmip6_bdy_files.
        entries += [
            {"target": "open_boundary.temp.values", "kind": "file", "file": _cmip6_bdy_files(baroclinic, config, _calendar_suffix, "thetao"), "variable": "thetao", "on_grid": True, "climatology": False},
            {"target": "open_boundary.salt.values", "kind": "file", "file": _cmip6_bdy_files(baroclinic, config, _calendar_suffix, "so"), "variable": "so", "on_grid": True, "climatology": False},
        ]

    # boundaries.barotropic z/u/v (cfg_boundaries.py::data_2d): TPXO tidal
    # constituents, or CMEMS/CMIP6 time series at the boundary points.
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
        # One file holding zos/uo/vo.
        _folder = _bdy_folder(barotropic, config, barotropic.get("scenario", ""))
        _file = _folder / barotropic.get("filename_template", "").format(
            start_date=barotropic.get("start_date", ""), end_date=barotropic.get("end_date", "")
        )
        entries += [
            {"target": "open_boundaries.z", "kind": "file", "file": str(_file), "variable": barotropic.get("variable", "zos"), "on_grid": True},
            {"target": "open_boundaries.u", "kind": "file", "file": str(_file), "variable": "uo", "on_grid": True},
            {"target": "open_boundaries.v", "kind": "file", "file": str(_file), "variable": "vo", "on_grid": True},
        ]
    elif barotropic_source == "CMIP6":
        # One file holding zos/uo/vo, historical bridge + scenario file.
        _files = _cmip6_bdy_files(barotropic, config, _calendar_suffix)
        entries += [
            {"target": "open_boundaries.z", "kind": "file", "file": _files, "variable": "zos", "on_grid": True},
            {"target": "open_boundaries.u", "kind": "file", "file": _files, "variable": "uo", "on_grid": True},
            {"target": "open_boundaries.v", "kind": "file", "file": _files, "variable": "vo", "on_grid": True},
        ]

    # meteo's 1:1 file reads, by source (ERA5 d2m vs CMIP6 qa). The loader/
    # codegen skip whichever swr/ql target simulation.airsea's methods don't
    # use. A meteo source that doesn't match simulation.airsea.type
    # (FluxesFromMeteo fields vs Fluxes fields) raises: skipping silently
    # would leave the run without real forcing.
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
        # No `folder`: Fluxes' own static YAML values stay (nothing derived).
        # A `folder` switches every field to flux_<field>_????.nc files.
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
        # t2m/qa/u10/v10/sp/tp: no entries -- set_meteo_data always sets them.
        # tcc needs a value even when unused (FluxesFromMeteo requires every
        # field before sim.start()); set_meteo_data overwrites it for
        # pseudo_tcc only.
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

    # FABM tracer boundary types + values. Gated on FABM being on
    # (fabm.source ERSEM with a file): pygetm-config can't skip a FABM tracer
    # target when FABM is off, so an unconditional entry would crash. The IC
    # pick and the ERSEM dependencies are hooks (hydrography.py, fabm.py).
    fabm_cfg = config.get("fabm", {})
    if fabm_cfg.get("source") == "ERSEM" and fabm_cfg.get("file"):
        boundaries_fabm = config.get("boundaries", {}).get("fabm", {})
        boundaries_fabm_source = boundaries_fabm.get("source")
        # WOA: global climatology; CMEMS/CMIP6: time series at the boundary
        # points; CMEMS `climatology` optionally cycles one year.
        _fabm_grid_kwargs = {
            "WOA": {"on_grid": False, "climatology": True},
            # Validated config: the active choice's fields are on boundaries_fabm.
            "CMEMS": {"on_grid": True, "climatology": bool(boundaries_fabm.get("climatology", False))},
            "CMIP6": {"on_grid": True, "climatology": False},
        }.get(boundaries_fabm_source)
        _fabm_tracers = boundaries_fabm.get("tracers") or {}
        if _fabm_grid_kwargs is not None and _fabm_tracers:
            _fabm_folder = Path(boundaries_fabm.get("folder", ""))
            if boundaries_fabm_source == "CMIP6" and boundaries_fabm.get("folder_template"):
                # model/scenario fill folder_template; `file` stays literal.
                _fabm_folder = _fabm_folder / boundaries_fabm["folder_template"].format(
                    model=boundaries_fabm.get("model", ""), scenario=boundaries_fabm.get("scenario", "")
                )
            # Historical bridge files (all in ${BOUNDARY_FOLDER_FABM_CMEMS},
            # left unresolved: this runs at generation time):
            #   no3/po4/si/o2: bio_daily_20100101_20141231.nc, CMEMS reanalysis
            #     trimmed to end before the scenario file (no time overlap).
            #   dissic/talk: see dic_ta_historical_method.
            # CMIP6 needs all six (its projections start 2015); CMEMS only
            # dissic/talk (bio_carbon_new starts 2024-07-29); WOA none.
            _cmems_fabm_folder = Path("${BOUNDARY_FOLDER_FABM_CMEMS}")
            # dissic/talk bridge: CMIP6 2010-2014; CMEMS 2010-01-01..2024-07-28
            # (pml_trend only -- no cycled_climatology file for that range).
            _dic_ta_historical_method = boundaries_fabm.get("dic_ta_historical_method", "cycled_climatology")
            _dic_ta_hist_file = {
                ("cycled_climatology", "CMIP6"): "bio_carbon_climatology_cycled_20100101_20141231.nc",
                ("pml_trend", "CMIP6"): "bio_carbon_pml_trend_20100101_20141231.nc",
                ("pml_trend", "CMEMS"): "bio_carbon_pml_trend_20100101_20240728.nc",
            }.get((_dic_ta_historical_method, boundaries_fabm_source))
            # The bridge is only read for dissic/talk tracers, and not at all
            # when CMEMS is cycled as a climatology (no dated gap to fill).
            _needs_dic_ta_bridge = not _fabm_grid_kwargs["climatology"] and any(
                "constant" not in _spec and _spec.get("variable") in ("dissic", "talk")
                for _spec in _fabm_tracers.values()
            )
            if boundaries_fabm_source == "CMEMS" and _dic_ta_hist_file is None and _needs_dic_ta_bridge:
                # No cycled_climatology file for CMEMS's range: fail at
                # generation rather than read a gapped bridge.
                raise ValueError(
                    "boundaries.fabm.CMEMS.dic_ta_historical_method=cycled_climatology has no real "
                    "bridge file covering CMEMS's own real dissic/talk gap (2010-01-01 to 2024-07-28) -- "
                    "use pml_trend instead, or build bio_carbon_climatology_cycled_20100101_20240728.nc "
                    "first."
                )
            # Built per source, not as one eager dict literal: the CMEMS/CMIP6
            # values would raise `Path / None` for WOA.
            if boundaries_fabm_source == "CMIP6":
                _fabm_hist_files = {
                    "no3": _cmems_fabm_folder / "bio_daily_20100101_20141231.nc",
                    "po4": _cmems_fabm_folder / "bio_daily_20100101_20141231.nc",
                    "si": _cmems_fabm_folder / "bio_daily_20100101_20141231.nc",
                    "o2": _cmems_fabm_folder / "bio_daily_20100101_20141231.nc",
                    "dissic": _cmems_fabm_folder / _dic_ta_hist_file,
                    "talk": _cmems_fabm_folder / _dic_ta_hist_file,
                }
            elif boundaries_fabm_source == "CMEMS" and _needs_dic_ta_bridge:
                _fabm_hist_files = {
                    "dissic": _cmems_fabm_folder / _dic_ta_hist_file,
                    "talk": _cmems_fabm_folder / _dic_ta_hist_file,
                }
            else:
                # WOA, or CMEMS without a dissic/talk bridge to read.
                _fabm_hist_files = {}
            for _tracer, _spec in _fabm_tracers.items():
                # boundary_condition_type defaults to SPONGE.
                _bc_type = _spec.get("boundary_condition_type", "SPONGE")
                _bc_entry = {"target": f"open_boundary.{_tracer}", "kind": "boundary_type",
                             "boundary_condition_type": _bc_type}
                if _spec.get("sponge"):
                    # named open_boundary_sponges entry instead of the shared default
                    _bc_entry["sponge"] = _spec["sponge"]
                entries.append(_bc_entry)
                if "constant" in _spec:
                    # Fixed value, e.g. a low background for plankton/detritus pools.
                    entries.append(
                        {"target": f"open_boundary.{_tracer}.values", "kind": "constant",
                         "constant_value": float(_spec["constant"])}
                    )
                    continue
                if _bc_type == "ZERO_GRADIENT":
                    # ZERO_GRADIENT never reads prescribed values: no .values entry.
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
