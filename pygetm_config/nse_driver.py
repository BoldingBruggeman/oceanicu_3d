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


def add_rivers(domain, config: dict):
    """Mirrors cfg_rivers.py's create() -- dynamic, threshold-filtered, read from
    an EMORID/JRC discharge file at run time. The *set* of rivers depends on the
    threshold and the domain's exact footprint, so (per pygetm-config's
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


def set_river_data(sim, domain, config: dict) -> int:
    """Mirrors cfg_rivers.py's own data() -- the second half of
    river_discharge's job (user request): add_rivers (above) only sets
    POSITION; this attaches the REAL, time-varying discharge to each river
    actually present in this subdomain (sim.rivers -- a pygetm.rivers.
    LocalRiverCollection, keyed by name, only rivers that fall within THIS
    subdomain -- not necessarily every one add_rivers positioned on the
    global domain). Needs a live sim.rivers (only exists once the
    Simulation object is built), so this runs via
    river_discharge.data_script (see pygetm_config.loader.
    run_river_discharge_data_script), a separate hook from
    river_discharge.script's own add_rivers (which runs before sim exists).

    Salt is set to 0.0 (matching cfg_rivers.py's own
    river["salt"].set(0.0) -- river water is fresh). FABM biogeochemistry
    (NO3/NH4/PO4/Si/TALK/DIC, present in the real EMORID file) is NOT wired
    up here -- this repo has no FABM model configured yet; cfg_rivers.py's
    own data() only does this when sim.fabm is truthy, so it's a real,
    deliberate scope limit, not an oversight.
    """
    import xarray as xr

    rcfg = config["river_discharge"]
    path = Path(rcfg["folder"]) / rcfg["file"]
    # CFDatetimeCoder(use_cftime=True), matching cfg_rivers.py's own real
    # data() exactly -- needed for Q's time dimension, unlike add_rivers
    # above (which never reads a time-varying variable at all).
    time_coder = xr.coders.CFDatetimeCoder(use_cftime=True)
    n_set = 0
    with xr.open_dataset(path, engine="netcdf4", decode_times=time_coder) as ds:
        # Same "site_name" vs "name" fallback as add_rivers above -- both
        # read the same file, so if one needs it the other might too.
        name_var = next((v for v in ("site_name", "name") if v in ds), None)
        site_names = ds[name_var].values if name_var else range(ds.sizes["site"])
        name_to_index = {str(n): i for i, n in enumerate(site_names)}
        for name, river in sim.rivers.items():
            idx = name_to_index.get(name)
            if idx is None:
                continue
            river.flow.set(ds["Q"].isel(site=idx))
            river["salt"].set(0.0)
            n_set += 1
    return n_set


def set_meteo_data(sim, domain, config: dict) -> None:
    """Mirrors cfg_airsea.py's own data() -- the piece that would need real
    Python (not a static data_assignments entry): CMIP6's net shortwave/
    longwave, `swr = rsds - rsus`/`ql = rlds - rlus`, a subtraction of TWO
    separate files (pre_transform only supports a scale/offset on ONE
    file's value; pre_transform_expression is deliberately refused for
    security reasons).

    CURRENTLY A NO-OP (user-confirmed real data gap, 2026-08-06): the
    actual bias-corrected CMIP6 dataset this project uses
    (/data/BiasCorrected/CMIP6/<model>/<scenario>/meteo/, re-interpolated
    onto ERA5's own grid/calendar/hourly convention -- verified directly
    against a real sample file: latitude/longitude coords, calendar
    proleptic_gregorian, matching ERA5 exactly) provides evspsbl/huss/pr/
    psl/tas/uas/vas -- NO rsds/rsus/rlds/rlus (shortwave/longwave) and NO
    cloud cover at all. "Not been finally fixed yet" (user's own words) on
    the data-provider side -- not fabricated here. sim.airsea.swr/.ql are
    left to pygetm's own FluxesFromMeteo auto-compute (ROSATI_MIYAKODA/
    CLARK, the schema's own default shortwave_method/longwave_method),
    same as ERA5's own default path already does when it isn't given an
    explicit NET_FLUX/DOWNWARD_FLUX file either.

    Registered via meteo.data_script (see pygetm_config.loader.
    run_meteo_data_script) so the wiring is ready the moment real
    radiation/cloud-cover data exists -- until then this intentionally
    does nothing.
    """
    return


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
    -- registered via post_data_script (see pygetm_config.loader.
    run_post_data_script), not river_discharge.script (that hook runs
    before the simulation object even exists, too early for this).
    """
    import pygetm

    if sim.runtype < pygetm.RunType.BAROCLINIC:
        sim.sst = sim.airsea.t2m


def set_hydrography_ic(sim, domain, config: dict) -> None:
    """Mirrors cfg_ic.py's own create() -- WOA/CMEMS branches specifically
    ("constant" hydrography is plain data_assignments, no Python needed).
    Real Python needed for: (1) `.isel(time=imonth)` -- a monthly
    CLIMATOLOGY index PICK for the initial, one-time value (imonth derived
    from config['runtime']['time'], matching run_model.py's own real
    `simstart.month - 1`) -- NOT pygetm-config's own `climatology: True`
    data_assignments flag, which means something different (keep cycling
    the whole 12-month pattern for the entire run, wrong for an initial
    condition). (2) sim.density.convert_ts(sim.salt, sim.temp) -- pyGETM's
    internal state is conservative temperature/absolute salinity, WOA/CMEMS
    provide in-situ/practical values (user request, TODO item 21) --
    called for BOTH WOA and CMEMS, matching cfg_ic.py's own real code
    exactly (present in both real-data branches, absent from "constant").

    Masks out land points afterward (sim.temp/sim.salt set to
    pygetm.constants.FILL_VALUE where sim.T.mask == 0), matching cfg_ic.py's
    own real code -- both WOA/CMEMS climatology files are GLOBAL, so
    horizontal interpolation can leave real (non-fill) values sitting at
    domain points outside the real ocean mask.

    Registered via hydrography.data_script (see pygetm_config.loader.
    run_hydrography_data_script) -- only called when NOT loading from a
    restart (checked there, not here). Checks runtype == BAROCLINIC itself
    (matching cfg_ic.py's own identical gate) since the core loader
    function doesn't special-case runtype for any of its three data_script
    hooks.
    """
    import datetime

    import pygetm
    import pygetm.constants
    import pygetm.input

    if sim.runtype != pygetm.RunType.BAROCLINIC:
        return

    hydro = config["hydrography"]
    source = hydro.get("source")
    if source not in ("WOA", "CMEMS"):
        return

    time = config["runtime"]["time"]
    if isinstance(time, str):
        time = datetime.datetime.fromisoformat(time)
    imonth = time.month - 1

    folder = Path(hydro["folder"])
    if source == "WOA":
        salt_file, salt_var = folder / "woa_s.nc", "s_an"
        temp_file, temp_var = folder / "woa_t.nc", "t_an"
    else:  # CMEMS
        salt_file, salt_var = folder / "so_2025_monthly_ic.nc", "so_ff"
        temp_file, temp_var = folder / "thetao_2025_monthly_ic.nc", "thetao_ff"

    sim.salt.set(pygetm.input.from_nc(salt_file, salt_var).isel(time=imonth), on_grid=False)
    sim.temp.set(pygetm.input.from_nc(temp_file, temp_var).isel(time=imonth), on_grid=False)
    sim.density.convert_ts(sim.salt, sim.temp)

    sim.temp[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE
    sim.salt[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE


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
        "Rivers are included: river_discharge.emorid.script (resolved above) points back at "
        "this file's own add_rivers(), whose real source gets embedded in the generated "
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
        "per open_boundaries entry (rivers stay at DEBUG regardless since this script's own "
        "add_rivers, not loader.add_rivers, is what actually adds them here -- see that "
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
    if river_discharge.get("source") == "emorid":
        if not emorid_cfg.get("script"):
            emorid_cfg["script"] = f"{Path(__file__).parent / 'nse_driver.py'}:add_rivers"
        # data_script (user request): the second half of river_discharge's
        # job -- real discharge data, mirroring cfg_rivers.py's own
        # create()/data() split. Same resolve-portably-at-run-time reasoning
        # as `script` above.
        if not emorid_cfg.get("data_script"):
            emorid_cfg["data_script"] = f"{Path(__file__).parent / 'nse_driver.py'}:set_river_data"
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
            hydro_cfg["data_script"] = f"{Path(__file__).parent / 'nse_driver.py'}:set_hydrography_ic"
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
            meteo_cfg["data_script"] = f"{Path(__file__).parent / 'nse_driver.py'}:set_meteo_data"
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
    config.setdefault("post_data_script", f"{Path(__file__).parent / 'nse_driver.py'}:set_sst_proxy")

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
    # rivers. add_rivers() itself is unchanged and still lives in this file
    # -- river_discharge.emorid.script (resolved above) just points back at
    # it by path.
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
    # below -- river_discharge.emorid.data_script (resolved above) points
    # back at set_river_data() in this same file.
    n_river_data = loader.run_river_discharge_data_script(sim, domain, config)
    print(f"{n_river_data} river(s) given real discharge data", file=sys.stderr)

    # Goes through the SAME generic mechanism --dump-python's generated
    # scripts use (post_data_script, see pygetm_config.loader.
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
