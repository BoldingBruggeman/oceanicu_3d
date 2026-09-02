#!/usr/bin/env python
"""Utility/boilerplate companion to the generated script that imports from
this module -- argparse CLI setup, the full validated config
(loaded from the companion *_config.yaml, see emit_config_literal_once), and
any embedded script-hook function definitions (river_discharge.script/
data_script, hydrography.data_script, meteo.data_script, post_data_script).
In principle a user only needs to read the OTHER generated file -- this one
is "in the way" of the actual pyGETM API call sequence, kept separate so it
is.
"""

import argparse
import os
import re
import sys
import pygetm
import pygetm.domain
import pygetm.simulation
import pygetm.input
import numpy as np
import datetime
import pathlib
from pathlib import Path
from typing import Optional, Sequence
import awex
import yaml
import netCDF4
import pygetm.input.tpxo

# --- data-path portability -- real loader.py source, embedded
# verbatim (inspect.getsource, at generation time) so this can never drift ---
def resolve_data_path(path: str) -> str:
    """Lazily expand $VAR/${VAR} references in a data file/folder path against
    the environment, at the point of actual use -- NOT at config-validate time,
    and (for codegen.py's generated scripts, which embed this exact function
    via inspect.getsource -- see _DATA_PATH_HELPERS_SOURCE) NOT at generation
    time either. This is what makes a config, and a generated script + its
    companion config YAML, portable across machines: only the FOLDER varies
    per machine, the filename doesn't -- so the config keeps a path like
    "${ERA5_FOLDER}/era5_t2m_2025.nc" literally, and each machine supplies
    its own ERA5_FOLDER via a real env var, --data-root NAME=VALUE, or
    apply_data_roots's optional gap-filling roots file.

    Standard POSIX $VAR/${VAR} syntax (os.path.expandvars), not a bespoke
    templating format -- composes for free with anything that already
    exports environment variables (a shell profile, an HPC module system,
    direnv, CI secrets), no pygetm-config-specific convention to learn. A
    path with no $VAR reference at all passes through unchanged.

    The regex is compiled INSIDE the function body, not as a module-level
    constant, deliberately -- inspect.getsource only captures this function's
    own text, and codegen.py embeds exactly that (no separate mechanism to
    also carry a module-level constant along with it) into every generated
    script.
    """
    var_pattern = re.compile(r"\$\{?(\w+)\}?")
    resolved = os.path.expandvars(path)
    missing = sorted(set(var_pattern.findall(resolved)))
    if missing:
        raise RuntimeError(
            f"{path!r}: environment variable(s) {', '.join(missing)} not set "
            "(needed to resolve a data file/folder path) -- set them directly, "
            "via --data-root NAME=VALUE, or in a data roots file (see --data-roots-file)"
        )
    return resolved

def apply_data_roots(overrides: Optional[Sequence[str]] = None, roots_file: Optional[str] = None) -> None:
    """Populate os.environ for the $VAR names resolve_data_path expands,
    before any real file access happens. Not called automatically -- opt-in
    via cli.py's/a project's own driver script's --data-root/
    --data-roots-file flags (and the same two flags on every
    codegen.py-generated script, see _emit_argparse), so a run with no
    flags at all behaves exactly as before (plain os.environ, nothing
    injected).

    Two sources, in increasing priority ("env vars vs. a file" -- hybrid):
    `roots_file` (a flat YAML
    mapping of NAME: value, no hostname-keying -- each machine only ever
    reads its OWN file) is applied with setdefault, so it only fills gaps and
    NEVER clobbers an already-exported real env var -- an explicit export
    reflects a deliberate choice for THIS session (an HPC module system, or a
    manual override for one test run) and shouldn't be silently overridden by
    a bulk convenience file. --data-root NAME=VALUE overrides are applied
    last, unconditionally, and always win over both.
    """
    if roots_file:
        import yaml

        with open(roots_file) as f:
            roots = yaml.safe_load(f) or {}
        for key, value in roots.items():
            os.environ.setdefault(key, str(value))
    for item in overrides or []:
        if "=" not in item:
            # ValueError, not LoaderError -- this function's source is embedded
            # verbatim into every codegen.py-generated script (see
            # codegen._DATA_PATH_HELPERS_SOURCE), which has no LoaderError
            # class of its own and shouldn't need one just for this.
            raise ValueError(f"--data-root {item!r}: expected NAME=VALUE")
        key, _, value = item.partition("=")
        os.environ[key] = value

def _request_output_fields(file_obj, fields, *, skip_unavailable, label, sim=None, z_index=None, **kwargs):
    if z_index is not None:
        # A real, discrete-layer slice (e.g. surface=-1, bottom=0) via
        # Array.isel(z=...) -- File.request() itself has no such concept,
        # only fields (str/Array) plus the separate, INTERPOLATING z=
        # param. One field at a time, each under its OWN name unless an
        # explicit output_name override was given (only valid for exactly
        # one field -- checked at generation time, same output_name
        # single-field constraint pygetm's own request() has) -- lets a
        # single entry apply the same z_index to many fields (e.g. a whole
        # FABM group at the surface) instead of needing one entry per field.
        explicit_output_name = kwargs.pop("output_name", None)
        for field_name in fields:
            output_name = explicit_output_name or field_name
            try:
                file_obj.request(sim[field_name].isel(z=z_index), output_name=output_name, **kwargs)
            except Exception as exc:
                if not skip_unavailable:
                    raise
                print(f"{label}: skipping field {field_name!r} ({exc})", file=sys.stderr)
        return
    if not skip_unavailable:
        file_obj.request(*fields, **kwargs)
        return
    try:
        file_obj.request(*fields, **kwargs)
    except Exception:
        for field in fields:
            try:
                file_obj.request(field, **kwargs)
            except Exception as exc:
                print(f"{label}: skipping field {field!r} ({exc})", file=sys.stderr)


def _coerce_runtime_value(raw):
    if isinstance(raw, dict):
        return datetime.timedelta(**raw)
    return raw


parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('--start', default=None, metavar='ISO8601', help='start time; overrides the configured runtime.time (ignored if --load-restart is given)')
parser.add_argument('--stop', default=None, metavar='ISO8601', help='stop time; required to actually advance')
parser.add_argument('--dry-run', action=argparse.BooleanOptionalAction, default=None, help="build and start but don't advance if neither --stop nor --dry-run/--no-dry-run is given (default when nothing is specified: False); --stop alone is enough to actually run, --dry-run always wins if given explicitly")
parser.add_argument('--load-restart', default=None, metavar='PATH', help='resume from a restart file; overrides --start/the configured start time')
parser.add_argument('--save-restart', default=None, metavar='PATH', help='write a restart file for this run')
parser.add_argument('--skip-unavailable-output', action='store_true', default=False, help="drop individual requested output fields that don't exist for the chosen runtype with a warning, instead of failing")
parser.add_argument('--fabm', nargs='?', const=True, default=None, metavar='PATH', help="enable FABM at runtime, optionally with a specific fabm.yaml path (bare --fabm reuses the configured path, or pygetm's own default location if none was configured); overrides the configured value (default when nothing is specified: None)")
parser.add_argument('--no-fabm', dest='fabm', action='store_const', const=False, help='disable FABM at runtime, regardless of the configured value')
parser.add_argument('--data-root', action='append', metavar='NAME=VALUE', help='override a data-path environment variable (e.g. ERA5_FOLDER=/data/ERA5); repeatable, always wins')
parser.add_argument('--data-roots-file', default=None, metavar='PATH', help='YAML file of NAME: value data-path env vars; only fills gaps not already set (see apply_data_roots)')
args = parser.parse_args()
apply_data_roots(args.data_root, args.data_roots_file)

# TRIMMED config -- only the keys a script-hook function actually reads
# at runtime (see generated_nse_woa_config.yaml alongside this script).
# Everything else (domain, simulation, output, ...) is already baked into
# literal Python calls above/below -- editing it here has NO effect. runtime's
# own time/check_finite/dump_on_error/timestep/split_factor/report/
# report_totals ARE read live here (see _emit_start/_emit_run_loop_and_finish);
# debug_output is NOT (a generation-time structural choice, not a runtime one).
with open(pathlib.Path(__file__).parent / 'generated_nse_woa_config.yaml') as f:
    config = yaml.safe_load(f)

if args.start:
    config.setdefault('runtime', {})['time'] = args.start

# --- river_discharge.script (${SCRIPT_FOLDER}/rivers.py:add_rivers) ---
def add_rivers(domain, config: dict):
    """Mirrors cfg_rivers.py's create() -- dynamic, threshold-filtered, read from
    an EMORID/JRC discharge file at run time. The *set* of rivers depends on the
    threshold and the domain's exact footprint, so (per pygetm-config's
    docs/yaml_vs_python.md) this cannot become a static YAML list without losing
    that behavior; it has to stay a loop over real data, same as in OceanICU
    today.

    Reads the validated `river_discharge:` section (a `nested_by_label`
    ChoiceSpec -- see oceanicu_providers.py), not a free-form dict: whichever
    source is active ("emorid" or "CMIP6") has its fields flattened onto
    `config["river_discharge"]` directly by validate_config, regardless of
    whether the YAML wrote them nested under `emorid:`/`CMIP6:` or flat. Both
    sources use this SAME function unchanged -- CMIP6's delta-change river
    projection (stats/cli/river_projection.py's river_flows_future_
    {scenario}.nc) is keyed by the same EMORID stations, just with a Q_mean
    variable added so the threshold lookup below finds a match; only
    folder/folder_template/file (see `file`'s own {model}/{scenario}
    templating below) differ between the two sources.

    `folder` may be a "${VAR}"/"$VAR" reference (pygetm-config's own lazy
    data-path resolution mechanism) -- resolve_data_path expands it here,
    at actual use time, exactly like pygetm-config's own generic kind="path"/
    kind="file" handling does for core schema fields. This function isn't
    core pygetm-config code (it's a project-specific script hook, loaded via
    load_dotted_target), so it has to opt into that resolution explicitly --
    unlike domain.path/tpxo_folder/data_assignments file, which get it for
    free via loader._coerce_value's own "path" TypeKind branch.
    """
    import xarray as xr
    import pygetm

    rcfg = config["river_discharge"]
    # .format() is a no-op for "emorid"'s literal filename (no {} in it) --
    # only source=CMIP6 actually has {model}/{scenario} placeholders here,
    # same substitution folder_template already does elsewhere (meteo.py's
    # own folder_template.format(model=..., scenario=...)) -- avoids the
    # filename and `scenario:` field ever disagreeing with each other.
    filename = rcfg["file"].format(model=rcfg.get("model", ""), scenario=rcfg.get("scenario", ""))
    path = Path(resolve_data_path(rcfg["folder"])) / filename
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

# --- hydrography.data_script (${SCRIPT_FOLDER}/hydrography.py:set_hydrography_ic) ---
def set_hydrography_ic(sim, domain, config: dict) -> None:
    """Mirrors cfg_ic.py's own create() -- WOA/CMEMS branches specifically
    ("constant" hydrography is plain data_assignments, no Python needed).
    Real Python needed for: (1) `.isel(time=imonth)` -- a monthly
    CLIMATOLOGY index PICK for the initial, one-time value (imonth derived
    from config['runtime']['time'], matching run_model.py's own
    `simstart.month - 1`) -- NOT pygetm-config's own `climatology: True`
    data_assignments flag, which means something different (keep cycling
    the whole 12-month pattern for the entire run, wrong for an initial
    condition). (2) sim.density.convert_ts(sim.salt, sim.temp) -- pyGETM's
    internal state is conservative temperature/absolute salinity, WOA/CMEMS
    provide in-situ/practical values -- called for BOTH WOA and CMEMS,
    matching cfg_ic.py's own code exactly (present in both real-data
    branches, absent from "constant").

    Masks out land points afterward (sim.temp/sim.salt set to
    pygetm.constants.FILL_VALUE where sim.T.mask == 0), matching cfg_ic.py's
    own code -- both WOA/CMEMS climatology files are GLOBAL, so horizontal
    interpolation can leave real (non-fill) values sitting at domain points
    outside the real ocean mask.

    Registered via hydrography.data_script (see pygetm_config.loader.
    run_hydrography_data_script) -- only called when NOT loading from a
    restart (checked there, not here). Checks runtype == BAROCLINIC itself
    (matching cfg_ic.py's own identical gate) since the core loader
    function doesn't special-case runtype for any of its three data_script
    hooks.

    `folder` may be a "${VAR}"/"$VAR" reference (pygetm-config's own lazy
    data-path resolution mechanism) -- resolve_data_path expands it here,
    at actual use time. See scripts/rivers.py's own docstring for why this
    function has to opt into that explicitly (a project-specific script
    hook, not core pygetm-config code going through loader._coerce_value's
    generic "path" TypeKind handling).
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

    # runtime.time is deliberately never in the static config (start/stop
    # are per-invocation, not per-setup -- see nse_from_oceanicu.yaml's own
    # header comment) -- both oceanicu_driver.py's live path and codegen's
    # generated scripts fill it in from --start before this hook ever runs
    # (`config.setdefault('runtime', {})['time'] = args.start`), but only if
    # --start was actually given somewhere (at generation time, baked into
    # the script's own --start default, or at the script's own invocation).
    # A bare KeyError here (real, reproduced case: a --dry-run invocation
    # with no --start anywhere) gave no hint why; this hook's own monthly-
    # climatology index pick genuinely can't proceed without a real date.
    time = config.get("runtime", {}).get("time")
    if time is None:
        raise RuntimeError(
            "hydrography's monthly-climatology initial condition needs a real start time, but "
            "runtime.time isn't set anywhere -- pass --start explicitly (either when generating "
            "this script, or when running it)."
        )
    if isinstance(time, str):
        time = datetime.datetime.fromisoformat(time)
    imonth = time.month - 1

    folder = Path(resolve_data_path(hydro["folder"]))
    if source == "WOA":
        salt_file, salt_var = folder / "woa_s.nc", "s_an"
        temp_file, temp_var = folder / "woa_t.nc", "t_an"
    else:  # CMEMS
        salt_file, salt_var = folder / "so_2025_monthly_ic.nc", "so_ff"
        temp_file, temp_var = folder / "thetao_2025_monthly_ic.nc", "thetao_ff"

    sim.salt.set(pygetm.input.from_nc(salt_file, salt_var).isel(time=imonth), on_grid=False)
    sim.temp.set(pygetm.input.from_nc(temp_file, temp_var).isel(time=imonth), on_grid=False)
    #sim.density.convert_ts(sim.salt, sim.temp)

    sim.temp[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE
    sim.salt[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE

# --- river_discharge.data_script (${SCRIPT_FOLDER}/rivers.py:set_river_data) ---
def set_river_data(sim, domain, config: dict) -> int:
    """Mirrors cfg_rivers.py's own data() -- the second half of
    river_discharge's job: add_rivers (above) only sets POSITION; this
    attaches the REAL, time-varying discharge to each river
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
    filename = rcfg["file"].format(model=rcfg.get("model", ""), scenario=rcfg.get("scenario", ""))
    path = Path(resolve_data_path(rcfg["folder"])) / filename
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

# --- meteo.data_script (${SCRIPT_FOLDER}/meteo.py:set_meteo_data) ---
def set_meteo_data(sim, domain, config: dict) -> None:
    """Mirrors cfg_airsea.py's own data() -- the piece(s) of meteo forcing
    that genuinely can't be a static data_assignments entry (pre_transform
    only supports a scale/offset on ONE file's value; pre_transform_expression
    is deliberately refused for security reasons).

    meteo.CMIP6.radiation_source picks how CMIP6 radiation is supplied
    ("net" | "components" | "pseudo_tcc", default "pseudo_tcc"):

    - "net": net_sw/net_lw are ALREADY bias-corrected as one direct file
      each (net_sw = rsds - rsus, net_lw = rlds - rlus, corrected as a
      single composite quantity) -- ocean-prep's own bias-correction tool's
      recommended approach whenever net_sw/net_lw files exist for the
      model/scenario in use. Sets simulation.airsea.swr/ql DIRECTLY.
    - "components": the older convention -- 4 separate bias-corrected files
      (rsds/rsus/rlds/rlus), subtracted here at runtime. Kept for models/
      scenarios that only have these 4 files, not net_sw/net_lw.
    - "pseudo_tcc" (default): CMIP6 has no cloud-cover field, and net_sw/
      net_lw/rsus/rlds/rlus have been unreliable to source for some models
      (GFDL THREDDS outages, CNRM ESGF 403s, GFDL's rsus/rlus only at
      3-hourly) -- derives a pseudo cloud fraction from bias-corrected
      daily-mean rsds ALONE via a clearness-index proxy: kt = rsds / TOA,
      tcc = quadratic fit of kt. TOA is the analytic daily-mean
      top-of-atmosphere shortwave insolation (W/m^2) -- depends only on
      latitude and day-of-year (longitude drops out once averaged over a
      full rotation), the standard closed-form extraterrestrial-radiation
      formula (Duffie & Beckman eq. 1.10.3). The quadratic fit coefficients
      were calibrated against ERA5 daily-mean ssrd/tcc over NW-Europe
      (-20..30E, 45..75N), 2024-2025 (n=1,546,042 daily grid-cell samples;
      R^2=0.636, RMSE=0.155, Pearson r=-0.755 -- see
      driver/calibration/kt_tcc_daily.py and kt_tcc_daily_fit.json in this
      repo) using this SAME TOA formula, so both sides must be
      changed together if either is ever recalibrated. Sets
      simulation.airsea.tcc -- FluxesFromMeteo's ROSATI_MIYAKODA (shortwave)
      AND CLARK (longwave) bulk formulas BOTH read tcc (confirmed in
      pygetm's own airsea.py: update_shortwave_radiation passes
      self.tcc.all_values to awex.shortwave_radiation, update_longwave_
      radiation passes it to awex.longwave_radiation alongside sst/t2m/ea/
      qa) -- so this single derived cloud fraction drives both fluxes, not
      just shortwave.

    Whichever radiation_source is chosen, meteo.CMIP6.shortwave_method/
    longwave_method must be set consistently: "net"/"components" need
    NET_FLUX (-1) on both, or FluxesFromMeteo's default ROSATI_MIYAKODA/
    CLARK bulk formulas silently recompute (and discard) the swr/ql this
    sets from tcc every step instead; "pseudo_tcc" needs shortwave_method/
    longwave_method left at a bulk-formula method (e.g. the default 1) or
    FluxesFromMeteo never reads tcc and this hook's computation has no
    effect. A mismatch fails silently rather than loudly -- this genuinely
    happened once already (an in-progress domain config carried
    shortwave_method=1/longwave_method=1 while radiation_source defaulted
    to "net", so the net_sw/net_lw files being read here were being
    computed and then silently discarded every step). A warning is printed
    below if the two look inconsistent.

    ERA5's own equivalent (a real tcc file plus net/downward-flux file
    reads) is handled directly via oceanicu_providers.
    derive_data_assignments's own ERA5 data_assignments instead -- it
    doesn't need this hook at all.

    Filenames follow the bias-corrected CMIP6 dataset's own convention.
    "net"/"components" read the `_disagg_` sub-daily files (matching tas/
    huss/uas/vas/psl/pr). "pseudo_tcc" deliberately reads the DAILY-mean
    file instead (NOT `_disagg_`): the kt->tcc fit was calibrated on daily
    means (hourly instantaneous kt was too noisy, R^2=0.29 vs 0.64 daily --
    see driver/calibration/kt_tcc_calibration.py vs kt_tcc_daily.py in this
    repo), and pygetm's own temporal interpolation smooths the daily tcc
    series across each day's sub-daily timesteps same as any other daily
    forcing field.
    `{method}` (each variable's own regridding method) is matched with a
    glob wildcard rather than hardcoded in all three branches --
    bc-correct's own disaggregated-output writer has a latent bug (doesn't
    thread the real per-variable method/scenario through to that specific
    write path, silently defaulting to 'bilinear'/'' for every variable)
    that this glob is deliberately robust to on both sides: it matches
    today's wrongly-named files AND correctly-named files once that
    upstream bug is fixed, with no further change needed here. See
    oceanicu_driver.py's own CMIP6 branch for the same reasoning.

    Registered via meteo.data_script (see pygetm_config.loader.
    run_meteo_data_script) -- runs AFTER apply_data_assignments, so
    "pseudo_tcc" correctly overwrites oceanicu_providers.py's own
    constant_value=0.5 tcc placeholder rather than being overwritten by it.

    Deliberately self-contained (no module-level helper functions/
    constants): pygetm_config.codegen._emit_script_hook embeds only THIS
    function's own source via inspect.getsource -- a helper defined outside
    it would silently vanish from a --dump-python'd script (NameError at
    runtime), a real gap confirmed while implementing this.
    """
    meteo = config.get("meteo") or {}
    if meteo.get("source") != "CMIP6":
        return

    import pygetm

    # NOT meteo.get("CMIP6") -- validate_config's choice-flattening puts the
    # ACTIVE choice's own fields directly on the parent dict (here, `meteo`
    # itself, since source == "CMIP6"); only the INACTIVE alternative (ERA5)
    # stays nested under its own label key. A real, reproduced bug: reading
    # meteo["CMIP6"] found nothing (that key doesn't exist once CMIP6 is
    # active) and silently fell back to {}, then KeyError'd on "folder" --
    # exactly the same pitfall oceanicu_providers.derive_data_assignments's
    # own CMIP6 branch already avoids by reading meteo.get("folder") etc.
    # directly.
    folder = Path(resolve_data_path(meteo["folder"]))
    folder_template = meteo.get("folder_template")
    if folder_template:
        folder = folder / folder_template.format(
            model=meteo.get("model", ""), scenario=meteo.get("scenario", "")
        )

    radiation_source = meteo.get("radiation_source") or "pseudo_tcc"
    shortwave_method = meteo.get("shortwave_method")
    longwave_method = meteo.get("longwave_method")
    NET_FLUX = -1
    if radiation_source in ("net", "components") and (
        shortwave_method != NET_FLUX or longwave_method != NET_FLUX
    ):
        import warnings

        warnings.warn(
            f"meteo.CMIP6.radiation_source={radiation_source!r} sets "
            "simulation.airsea.swr/ql directly, but shortwave_method="
            f"{shortwave_method!r}/longwave_method={longwave_method!r} isn't "
            "NET_FLUX (-1) -- FluxesFromMeteo will silently recompute and "
            "discard these values from tcc every step instead. Set "
            "meteo.CMIP6.shortwave_method/longwave_method to -1, or use "
            "radiation_source: pseudo_tcc."
        )
    elif radiation_source == "pseudo_tcc" and (
        shortwave_method == NET_FLUX or longwave_method == NET_FLUX
    ):
        import warnings

        warnings.warn(
            "meteo.CMIP6.radiation_source='pseudo_tcc' derives "
            "simulation.airsea.tcc, but shortwave_method="
            f"{shortwave_method!r}/longwave_method={longwave_method!r} is "
            "NET_FLUX (-1), so FluxesFromMeteo never reads tcc and this "
            "hook's computation has no effect. Leave shortwave_method/"
            "longwave_method at a bulk-formula method (e.g. the default 1)."
        )

    if radiation_source == "net":
        sim.airsea.swr.set(
            pygetm.input.from_nc(str(folder / "net_sw_bc_*_disagg_????.nc"), "net_sw")
        )
        sim.airsea.ql.set(
            pygetm.input.from_nc(str(folder / "net_lw_bc_*_disagg_????.nc"), "net_lw")
        )
    elif radiation_source == "components":
        sim.airsea.swr.set(
            pygetm.input.from_nc(str(folder / "rsds_bc_*_disagg_????.nc"), "rsds")
            - pygetm.input.from_nc(str(folder / "rsus_bc_*_disagg_????.nc"), "rsus")
        )
        sim.airsea.ql.set(
            pygetm.input.from_nc(str(folder / "rlds_bc_*_disagg_????.nc"), "rlds")
            - pygetm.input.from_nc(str(folder / "rlus_bc_*_disagg_????.nc"), "rlus")
        )
    elif radiation_source == "pseudo_tcc":
        import numpy as np

        scenario = meteo.get("scenario", "")
        rsds = pygetm.input.from_nc(str(folder / f"rsds_bc_*_{scenario}_????.nc"), "rsds")

        solar_constant = 1361.0
        fit_a, fit_b, fit_c = -2.234072161944959, 0.6895789115452573, 0.8957591501340265

        lat = np.deg2rad(rsds["latitude"])
        doy = rsds["time"].dt.dayofyear
        decl = np.deg2rad(23.45) * np.sin(2 * np.pi * (284 + doy) / 365.0)
        dist_factor = 1.0 + 0.033 * np.cos(2 * np.pi * doy / 365.0)
        sunset_hour_angle = np.arccos(np.clip(-np.tan(lat) * np.tan(decl), -1.0, 1.0))
        toa = (solar_constant * dist_factor / np.pi) * (
            np.cos(lat) * np.cos(decl) * np.sin(sunset_hour_angle)
            + sunset_hour_angle * np.sin(lat) * np.sin(decl)
        )

        kt = (rsds / toa.where(toa > 1.0)).clip(0.0, 1.2).fillna(0.0)
        tcc = (fit_a * kt**2 + fit_b * kt + fit_c).clip(0.0, 1.0)
        sim.airsea.tcc.set(pygetm.input.wrap(tcc, name="tcc"))
    else:
        raise ValueError(
            f"meteo.CMIP6.radiation_source: {radiation_source!r} not recognized "
            "(expected 'net', 'components', or 'pseudo_tcc')"
        )

# --- fabm.data_script (${SCRIPT_FOLDER}/fabm.py:configure_fabm) ---
def configure_fabm(sim, domain, config: dict) -> None:
    """Mirrors cfg_fabm.py's own configure(sim, cfg, imonth): the piece of
    FABM setup that genuinely can't be a static data_assignments entry.
    data_assignments' own fabm.<tracer_name>.<attr> target only reaches
    FABM *state variables* (sim.fabm.state_variables) -- FABM *dependencies*
    (sim.fabm.get_dependency(...), e.g. atmospheric CO2, gelbstoff
    absorption, N-deposition) are a separate, narrower API with no
    data_assignments equivalent, and need real Python here instead.

    Checks BOTH that FABM is enabled at all (sim.fabm, from
    simulation.fabm -- see cfg_fabm.py's own `if not sim.fabm: return`
    guard) AND which specific FABM-compatible biogeochemical model is
    configured (config["fabm"]["source"], e.g. "ERSEM" -- pyGETM/FABM
    supports many models, not just one, so both checks matter
    independently, matching cfg_fabm.py's own `cfg.fabm.config == "ersem"`
    branch).

    Guarded to runtype == BAROCLINIC only (explicit user decision -- unlike
    cfg_fabm.py's own real code, which has a BAROTROPIC_3D branch supplying
    constant temperature/practical_salinity/density dependencies since a
    barotropic run has no real T/S field to read from, this driver commits
    to BAROCLINIC-only FABM: no barotropic support, no dead branch for it).
    Matches hydrography.py's own identical gate (`if sim.runtype !=
    pygetm.RunType.BAROCLINIC: return`) and its own reasoning -- the core
    loader function (run_fabm_data_script) doesn't special-case runtype for
    any of its four data_script hooks, so this is checked here instead.

    ERSEM dependency setup (gelbstoff absorption from a satellite product,
    atmospheric CO2, EMEP N-deposition) is ported from cfg_fabm.py as-is,
    reading its folder from fabm.ERSEM.folder. WOA-sourced FABM tracer
    ICs/SPONGE boundaries are ALSO ported, but now gated on this driver's
    own boundaries.fabm.source (config["boundaries"]["fabm"]["source"])
    instead of cfg_fabm.py's real cfg.hydrography.source check -- FABM
    boundary data gets its own role, independent of T/S hydrography (see
    oceanicu_providers.py's boundaries.fabm comment for why), so a config
    can enable ERSEM dependencies without necessarily also using WOA for
    FABM boundaries, and vice versa -- these two blocks below are
    deliberately independent, not an if/elif.

    NOT YET VERIFIED against real NSe input files (mesh_mask.nc-style grid
    file, AMM7-EMEP-style N-deposition netCDFs, gelbstoff/CDOM product, WOA
    tracer climatologies) -- cfg_fabm.py's own inputs are AMM-domain-
    specific; this port keeps the same dependency names/file-shape
    expectations, but NSe's own equivalent files need confirming before a
    real run. The `_add_coord` regridding helper below in particular
    assumes a `mesh_mask.nc` with `nav_lat`/`nav_lon` 2D coordinate arrays
    (cfg_fabm.py's own AMM convention) -- verify NSe's own EMEP-equivalent
    source uses the same shape before trusting this unmodified.
    """
    import datetime

    import pygetm
    import pygetm.input

    if not sim.fabm:
        return

    if sim.runtype != pygetm.RunType.BAROCLINIC:
        return

    fabm_cfg = config.get("fabm") or {}
    source = fabm_cfg.get("source")

    if source != "ERSEM":
        sim.logger.critical(
            f"configure_fabm: no FABM configuration for source={source!r} "
            "-- add a branch in driver/scripts/fabm.py"
        )
        return

    sim.logger.info("configure_fabm: providing FABM configuration for ERSEM (not provided by pyGETM)")

    # NOT fabm_cfg.get("ERSEM") -- validate_config's choice-flattening puts
    # the ACTIVE choice's own fields directly on the parent dict (here,
    # fabm_cfg itself, since source == "ERSEM"); only an INACTIVE
    # alternative stays nested under its own label key. Real, reproduced
    # bug (caught on a remote production run): fabm_cfg.get("ERSEM") found
    # nothing (that key doesn't exist once ERSEM is active) and silently
    # fell back to {}, then KeyError'd on "folder" -- exactly the same
    # pitfall scripts/meteo.py's own set_meteo_data docstring already
    # documents and avoids (reading meteo.get("folder") directly, not
    # meteo.get("CMIP6")).
    fabm_folder = Path(resolve_data_path(fabm_cfg["folder"]))

    # A routine by Gennadi to make data compatible with the input manager --
    # nested here (not module-level) since codegen's _emit_script_hook only
    # embeds the directly-referenced hook function's own source via
    # inspect.getsource, not any module-level helpers it calls (a real,
    # confirmed gap -- see driver/scripts/meteo.py's own established
    # convention of staying fully self-contained for the same reason).
    def _add_coord(nc):
        import xarray as xr

        fmesh = xr.open_dataset(fabm_folder / "mesh_mask.nc")
        nc = nc.drop_vars(("lon", "lat"))
        nc = nc.rename_dims(x="longitude", y="latitude")
        lat_AMM = fmesh.nav_lat.data
        lon_AMM = fmesh.nav_lon.data
        nc = nc.rename_dims({"t": "time"})
        nc = nc.rename_vars({"t": "time"})
        nc = nc.assign_coords(
            latitude=("latitude", lat_AMM[:, 0]), longitude=("longitude", lon_AMM[0, :])
        )
        nc.coords["latitude"] = nc.latitude.assign_attrs(long_name="latitude", units="degrees_north")
        nc.coords["longitude"] = nc.longitude.assign_attrs(long_name="longitude", units="degrees_east")
        nc.coords["time"] = nc.time.assign_attrs(long_name="time")
        nc = nc.fillna(0.0)
        return nc

    # --- ERSEM dependencies (ported from cfg_fabm.py's configure()) ---
    # No BAROTROPIC_3D constant-dependency branch here -- see this
    # function's own docstring for why (BAROCLINIC-only by design, so
    # sim.temp/sim.salt/sim.density already exist for real).
    sim.fabm.get_dependency("gelbstoff_absorption_satellite").set(
        pygetm.input.from_nc(fabm_folder / "ADY_gle.nc", "gelbstoff_absorption_satellite"),
        on_grid=False,
        climatology=True,
    )
    sim.fabm.get_dependency("mole_fraction_of_carbon_dioxide_in_air").set(400.0)

    emep_path = fabm_folder / "Ndep/AMM7-EMEP-NDeposition_y????.nc"
    sim.fabm.get_dependency("N3_flux/flux").set(
        pygetm.input.from_nc(str(emep_path), "N3_flux", preprocess=_add_coord)
    )
    sim.fabm.get_dependency("N4_flux/flux").set(
        pygetm.input.from_nc(str(emep_path), "N4_flux", preprocess=_add_coord)
    )

    # --- WOA/CMEMS-sourced FABM tracer initial conditions ---
    # Independent of the dependency setup above -- gated on this driver's
    # own boundaries.fabm role, NOT cfg_fabm.py's real cfg.hydrography.source
    # check (see this function's own docstring for why). SPONGE boundary
    # type + values are NOT set here -- oceanicu_providers.derive_data_
    # assignments emits them as plain data_assignments entries instead
    # (open_boundary.N3_n / open_boundary.N3_n.values, etc.), mirroring
    # boundaries.baroclinic's own WOA/CMEMS/CMIP6 branches there exactly,
    # since they're a straightforward 1:1 file read with no computation
    # needed (same meteo.py ERA5-vs-CMIP6 split reasoning: plain reads are
    # data_assignments, only genuinely computed values stay in a script).
    # Only the IC below stays here, because it needs a one-time
    # `.isel(time=imonth)` pick that data_assignments' climatology=True flag
    # can't express (that flag cycles all 12 months for the whole run, not
    # "pick one month once") -- exactly the same reason hydrography.py's own
    # T/S IC pick isn't a data_assignments entry either.
    #
    # CMIP6 is deliberately NOT accepted here -- mirrors hydrography.py's
    # own set_hydrography_ic, which only ever takes WOA/CMEMS for the IC
    # (`if source not in ("WOA", "CMEMS"): return`). CMIP6 delta-change
    # boundary output has no equivalent "monthly_ic" snapshot file
    # convention -- CMEMS's own IC below isn't its real time-series
    # boundary file either, it's a separate, purpose-built monthly-
    # climatology-shaped file (matches hydrography.py's own CMEMS IC
    # branch: so_2025_monthly_ic.nc/thetao_2025_monthly_ic.nc, NOT the
    # real time series boundaries.baroclinic.CMEMS reads for boundary
    # VALUES) -- so boundaries.fabm.CMEMS.tracers[*].file is expected to
    # point at that same kind of monthly-IC file, not the real time series
    # oceanicu_providers.derive_data_assignments reads for the boundary.
    boundaries_fabm_cfg = config.get("boundaries", {}).get("fabm") or {}
    if boundaries_fabm_cfg.get("source") in ("WOA", "CMEMS"):
        # Monthly-climatology index pick (.isel(time=imonth)) -- a ONE-TIME
        # initial value, not pygetm-config's own climatology:True
        # data_assignments flag (which cycles the whole 12-month pattern
        # for the entire run). configure_fabm's fixed (sim, domain, config)
        # signature has no imonth parameter, so it's derived from
        # runtime.time here exactly like hydrography.py's own
        # set_hydrography_ic does (that function's own CMEMS IC branch
        # ALSO uses isel(time=imonth), on its own separate monthly-IC file
        # -- same precedent this mirrors).
        time = config.get("runtime", {}).get("time")
        if time is None:
            raise RuntimeError(
                "configure_fabm's FABM tracer initial condition needs a real start "
                "time, but runtime.time isn't set anywhere -- pass --start explicitly (either "
                "when generating this script, or when running it)."
            )
        if isinstance(time, str):
            time = datetime.datetime.fromisoformat(time)
        imonth = time.month - 1

        ic_folder = Path(resolve_data_path(boundaries_fabm_cfg["folder"]))

        # Same tracer set as derive_data_assignments' boundary_type/values
        # entries (boundaries.fabm.<source>.tracers) -- defined once, used
        # for both, so the IC and boundary tracer lists can't drift apart.
        # A FABM state variable NOT listed here is simply never touched by
        # this loop -- it keeps whatever `initial_value` its own fabm.yaml
        # declares (standard FABM behavior when the host model doesn't
        # override it), and, on the boundary side, pygetm.tracer.Tracer.
        # __init__ already gives EVERY tracer -- FABM ones included --
        # ArrayOpenBoundaries(self, ZERO_GRADIENT) at construction time,
        # unconditionally, before any config runs. So an unlisted tracer
        # needs no explicit handling anywhere: it's ZERO_GRADIENT by
        # pygetm's own default, not by anything this driver has to arrange.
        for tracer, spec in (boundaries_fabm_cfg.get("tracers") or {}).items():
            sim[tracer].set(
                pygetm.input.from_nc(ic_folder / spec["file"], spec["variable"]).isel(time=imonth)
            )

