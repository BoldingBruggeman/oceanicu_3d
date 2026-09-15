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
import pygetm.output
import numpy as np
import datetime
import pathlib
from pathlib import Path
from typing import Optional, Sequence
import awex
import yaml
import netCDF4
import cftime

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

def expand_year_glob(pattern: str, start: Optional[str], stop: Optional[str]) -> list:
    """Expand a ``????``-year-wildcard file-path pattern (e.g.
    ``"${ERA5_FOLDER}/era5_t2m_????.nc"``) into an explicit list of paths
    for just the years this run actually needs, instead of matching every
    year physically present in the folder.

    Exists because a bare ``????`` glob passed to ``pygetm.input.from_nc``
    opens (and permanently caches -- see ``pygetm.input.open_nc_files``,
    which has no eviction) every matching file up front, regardless of
    whether this run's own period touches it. A 30+ year ERA5 archive
    behind a multi-decade-spanning wildcard means dozens of needless open
    files for a run that only ever reads a handful of them.

    ``start``/``stop`` are the generated script's own ``args.start``/
    ``args.stop`` (ISO 8601 strings, or ``None``) -- always both present
    for a real chunked run (``chunk_runner.py`` passes ``--start``
    unconditionally, even alongside ``--load-restart``; only
    ``--stop`` is ever strictly required). If either is missing (e.g. a
    ``--dry-run`` invocation with neither given), restricting isn't
    possible -- falls back to matching every file present, same as the
    original glob would have. Likewise if ``pattern`` has no ``????`` at
    all, it isn't a year-wildcard to begin with -- returns it resolved,
    unchanged otherwise.

    ``stop`` of exactly ``YYYY-01-01T00:00:00`` still needs ``YYYY``'s own
    file, not just up through ``YYYY - 1``: the run's own
    ``while sim.time < stop:`` loop makes its last request at an instant
    just *under* ``stop``, and pygetm's time interpolator
    (``TemporalInterpolation``) brackets every request between the record
    just before and just after it -- for a request infinitesimally before
    ``YYYY-01-01T00:00:00``, the "after" bracket record *is* ``YYYY``'s
    very first one. So ``stop.year`` is always needed, full stop, no
    special-casing for a midnight-Jan-1 boundary.

    One extra year past ``stop.year`` is considered -- but only when
    ``stop`` actually falls in December, the one case where the "after"
    bracket record for the last request could plausibly land in the
    *following* year's file (e.g. stop=2020-12-31T23:45 with hourly data
    whose last 2020 record is 23:00). Any other month already guarantees
    plenty of later records within ``stop.year``'s own file, so there's
    nothing speculative to check -- e.g. stop=2020-09-01 never touches
    2021 at all. When it IS considered, it's still only included if it
    actually exists on disk (silently skipped otherwise, same as the
    original ``????`` glob would have -- never erroring just because the
    archive doesn't extend that far); underrunning this for real would be
    a hard failure ("Cannot interpolate ... because end of time series
    was reached"), not a quiet wrong-answer risk, which is why the check
    exists at all for December specifically.
    """
    if "????" not in pattern:
        return [resolve_data_path(pattern)]
    if not start or not stop:
        # Can't restrict without both bounds -- fall back to matching
        # everything present, same as pygetm.input.from_nc's own glob
        # would have (glob.glob here, not resolve_data_path(pattern)
        # handed to from_nc as a 1-element list: from_nc only glob-
        # expands a bare string/PathLike argument, not something already
        # wrapped in a list, so returning the literal unexpanded pattern
        # would try to open a file literally named e.g. "era5_t2m_????.nc"
        # and fail).
        import glob

        return sorted(glob.glob(resolve_data_path(pattern)))
    start_year = datetime.datetime.fromisoformat(start).year
    stop_dt = datetime.datetime.fromisoformat(stop)
    stop_year = max(stop_dt.year, start_year)

    import glob

    def _resolve_wildcard(path: str) -> str:
        # Only the YEAR was substituted above -- a pattern with its OWN
        # extra wildcard beyond ???? (e.g. CMIP6's own regridding-method
        # glob, "tas_bc_*_disagg_2020.nc") still has real glob syntax left
        # in it at this point. pygetm.input.from_nc does NOT glob-expand
        # list items (only a single bare string/PathLike argument -- see
        # this function's own fallback branch above), and building a
        # multi-year file LIST is exactly what this function exists to do,
        # so a leftover-wildcarded path handed to from_nc inside that list
        # would fail to open at all (a real, reproduced bug: CMIP6 meteo
        # splicing hit this first, but it affects every CMIP6 data_
        # assignment entry using ???? together with * once a real --start/
        # --stop restricts the glob, not just splicing specifically).
        # Resolved here instead, per-year, to the single matching file.
        # 2+ matches is ambiguous (e.g. two regridding methods' output both
        # present for the same year) -- raised immediately rather than
        # silently concatenating or picking one, same reasoning as this
        # codebase's other resolve_year_glob (lib/path_utils.py, used by
        # river_projection.py): a wrong silent pick here would interleave
        # records from two different methods, corrupting the correction,
        # not just being inconvenient. Zero matches is left AS-IS (still
        # wildcarded) -- from_nc's own FileNotFoundError on that literal
        # string is a perfectly fine, loud failure for a genuinely missing
        # year, matching this function's own designed philosophy for the
        # per-year paths below (missing data should hard-fail, not silently
        # vanish -- see this function's own docstring).
        if not glob.has_magic(path):
            return path
        matches = sorted(glob.glob(path))
        if len(matches) > 1:
            raise ValueError(
                f"expand_year_glob: {path!r} matches {len(matches)} files "
                f"{matches!r} -- ambiguous, refusing to guess which one"
            )
        return matches[0] if matches else path

    paths = [
        _resolve_wildcard(resolve_data_path(pattern.replace("????", str(year))))
        for year in range(start_year, stop_year + 1)
    ]

    if stop_dt.month == 12:
        buffer_path = resolve_data_path(pattern.replace("????", str(stop_year + 1)))
        if glob.has_magic(buffer_path):
            _matches = sorted(glob.glob(buffer_path))
            if len(_matches) > 1:
                raise ValueError(
                    f"expand_year_glob: {buffer_path!r} matches {len(_matches)} "
                    f"files {_matches!r} -- ambiguous, refusing to guess which one"
                )
            if _matches:
                paths.append(_matches[0])
        elif os.path.exists(buffer_path):
            paths.append(buffer_path)

    return paths

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
parser.add_argument('--load-restart-time', default=None, metavar='ISO8601', help="only relevant with --load-restart. Which internal snapshot to resume from, for a restart file holding more than one (see add_restart(interval=...)); must exactly match one of the file's own time coordinate values, or sim.load_restart() raises listing the file's actual span. Omit for the last snapshot in the file (sim.load_restart()'s own default).")
parser.add_argument('--save-restart', default=None, metavar='PATH', help='write a restart file for this run')
parser.add_argument('--save-restart-interval', type=float, default=None, metavar='N', help="only relevant with --save-restart. How often to write a snapshot, in --save-restart-interval-units -- overrides runtime.save_restart_interval (read LIVE from the companion config.yaml if this script has one, same as runtime.dump_on_error; otherwise whatever was baked in at generation time). Omit both for pygetm's own default (one snapshot, at the end of this invocation). A restart file with more than one snapshot pairs with --load-restart-time on the NEXT invocation, to resume from any of them, not just the last.")
parser.add_argument('--save-restart-interval-units', default=None, metavar='UNIT', help='only relevant with --save-restart-interval. One of pygetm.output.TimeUnit\'s own member names: TIMESTEPS/SECONDS/MINUTES/HOURS/DAYS/MONTHS/YEARS -- overrides runtime.save_restart_interval_units (same live-read as --save-restart-interval above). Default TIMESTEPS if an interval is given but this is not.')
parser.add_argument('--skip-unavailable-output', action='store_true', default=False, help="drop individual requested output fields that don't exist for the chosen runtype with a warning, instead of failing")
parser.add_argument('--fabm', nargs='?', const=True, default=None, metavar='PATH', help="enable FABM at runtime, optionally with a specific fabm.yaml path (bare --fabm reuses the configured path, or pygetm's own default location if none was configured); overrides the configured value (default when nothing is specified: None)")
parser.add_argument('--no-fabm', dest='fabm', action='store_const', const=False, help='disable FABM at runtime, regardless of the configured value')
parser.add_argument('--data-root', action='append', metavar='NAME=VALUE', help='override a data-path environment variable (e.g. ERA5_FOLDER=/data/ERA5); repeatable, always wins')
parser.add_argument('--data-roots-file', default=None, metavar='PATH', help='YAML file of NAME: value data-path env vars; only fills gaps not already set (see apply_data_roots)')
args = parser.parse_args()
apply_data_roots(args.data_root, args.data_roots_file)

# TRIMMED config -- only the keys a script-hook function actually reads
# at runtime (see generated_nse_cmip6_raw_config.yaml alongside this script).
# Everything else (domain, simulation, output, ...) is already baked into
# literal Python calls above/below -- editing it here has NO effect. runtime's
# own time/check_finite/dump_on_error/timestep/split_factor/report/
# report_totals ARE read live here (see _emit_start/_emit_run_loop_and_finish);
# debug_output is NOT (a generation-time structural choice, not a runtime one).
with open(pathlib.Path(__file__).parent / 'generated_nse_cmip6_raw_config.yaml') as f:
    config = yaml.safe_load(f)

if args.start:
    config.setdefault('runtime', {})['time'] = args.start
if args.stop:
    config.setdefault('runtime', {})['stop'] = args.stop

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
    folder = Path(resolve_data_path(rcfg["folder"]))
    if rcfg.get("folder_template"):
        folder = folder / rcfg["folder_template"].format(
            model=rcfg.get("model", ""), scenario=rcfg.get("scenario", "")
        )
    # .format() is a no-op for "emorid"'s literal filename (no {} in it) --
    # only source=CMIP6 actually has {model}/{scenario} placeholders here,
    # same substitution folder_template already does elsewhere (meteo.py's
    # own folder_template.format(model=..., scenario=...)) -- avoids the
    # filename and `scenario:` field ever disagreeing with each other.
    filename = rcfg["file"].format(model=rcfg.get("model", ""), scenario=rcfg.get("scenario", ""))
    if config.get("runtime", {}).get("calendar") == "noleap":
        filename = filename.removesuffix(".nc") + "_noleap.nc"
    path = folder / filename
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
    folder = Path(resolve_data_path(rcfg["folder"]))
    if rcfg.get("folder_template"):
        folder = folder / rcfg["folder_template"].format(
            model=rcfg.get("model", ""), scenario=rcfg.get("scenario", "")
        )
    filename = rcfg["file"].format(model=rcfg.get("model", ""), scenario=rcfg.get("scenario", ""))
    if config.get("runtime", {}).get("calendar") == "noleap":
        filename = filename.removesuffix(".nc") + "_noleap.nc"
    path = folder / filename
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

    Handles TWO CMIP6 sources, both spliced across the historical/scenario
    boundary the same way (see "Historical/scenario splicing" below):
    meteo.source == "CMIP6" reads the BIAS-CORRECTED archive (ocean-prep's
    own bc-correct pipeline output); meteo.source == "CMIP6-raw" reads
    straight from the RAW CMIP6 archive fetched by ocean-data's DataLoader
    (/data/CMIP6/{model}/{scenario}/, CMIP6_RAW_FOLDER -- see machines.yaml
    and meteo.CMIP6-raw.folder/folder_template), no bias correction applied
    at all. Everything below that says "CMIP6" without qualification
    applies to the bias-corrected path only; the raw path's own filename
    convention, variable-name quirk, and units are covered in its own
    branch inline below (search "CMIP6-raw" in this function's body).

    meteo.CMIP6.radiation_source picks how CMIP6 radiation is supplied
    ("net" | "components" | "pseudo_tcc", default "pseudo_tcc") --
    meteo.CMIP6-raw.radiation_source is the same idea with only
    "components"/"pseudo_tcc" (no "net": that's a bias-correction-pipeline
    composite, not a raw CMIP6 variable):

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

    Whichever radiation_source is chosen, simulation.airsea.shortwave_method/
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
    run_meteo_data_script) -- runs AFTER apply_data_assignments, so this
    correctly overwrites oceanicu_providers.py's own static, single-scenario
    CMIP6 meteo entries (t2m/qa/u10/v10/sp/tp, and the tcc constant_value=0.5
    placeholder) rather than being overwritten by them -- those stay in
    providers.py as a sane fallback for the (only ever config-validation/
    dry-run) case where no meteo.data_script hook runs at all, same
    reasoning as tcc's own placeholder.

    Historical/scenario splicing (t2m/qa/u10/v10/sp/tp and whichever
    radiation_source is active): CMIP6's `historical` experiment is fixed at
    1850-2014 for every model (a protocol constant, not a config choice --
    see bc_meteo.yaml's own calibration-window fix, ocean-prep repo), so any
    run whose own [start, stop) period reaches back before 2015 needs
    `historical`'s own files for those early years, spliced together with
    meteo.CMIP6.scenario's files for 2015 onward, as ONE continuous series
    (pygetm.input.from_nc reads a list of files as one time series, same as
    a multi-year ERA5 glob already does). This is NOT a config toggle --
    purely derived from the run's own start/stop against the fixed 2014/2015
    boundary, so a run entirely on one side of it reduces to exactly the
    single-folder read this always used to be (per user, 2026-09-07: "if
    start of the simulation is before 2015 it is always historical - so why
    not enforce it"). Needs the run's own stop as well as start, so
    config['runtime']['stop'] is threaded in next to the existing
    config['runtime']['time'] (=start) -- see oceanicu_driver.py's own
    raw.setdefault("runtime", {})["stop"] = args.stop and pygetm_config's
    codegen.py equivalent for generated scripts.

    Deliberately self-contained (no module-level helper functions/
    constants): pygetm_config.codegen._emit_script_hook embeds only THIS
    function's own source via inspect.getsource -- a helper defined outside
    it would silently vanish from a --dump-python'd script (NameError at
    runtime), a real gap confirmed while implementing this. The nested
    _spliced_paths() below is fine -- it's part of this function's own
    source text, not a separate module-level definition.
    """
    meteo = config.get("meteo") or {}
    source = meteo.get("source")
    if source not in ("CMIP6", "CMIP6-raw"):
        return

    # Same gap/fix as oceanicu_providers.derive_data_assignments' own ERA5
    # branch (see its comment, 2026-09-14): this function only knows how to
    # write FluxesFromMeteo-shaped targets (t2m/qa/u10/v10/sp/tp/swr/ql/tcc
    # below). `simulation.airsea.type: Fluxes` has none of those attributes
    # -- raise a clear error rather than an AttributeError deep in a
    # sim.airsea.<x>.set(...) call, or (worse) silently doing nothing and
    # leaving Fluxes with no real forcing at all.
    _airsea_type = config.get("simulation", {}).get("airsea", {}).get("type")
    if _airsea_type not in (None, "FluxesFromMeteo"):
        raise ValueError(
            f"meteo.source={source!r} (meteo.data_script) writes FluxesFromMeteo's own fields "
            f"(t2m/qa/u10/v10/sp/tp/swr/ql/tcc), but simulation.airsea.type={_airsea_type!r} -- those "
            "targets don't exist on that airsea class. See oceanicu_providers.derive_data_assignments' "
            "own ERA5-branch comment for the same gap/options."
        )

    import datetime

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
    folder_root = Path(resolve_data_path(meteo["folder"]))
    folder_template = meteo.get("folder_template")
    model = meteo.get("model", "")
    scenario = meteo.get("scenario", "")

    HIST_CUTOFF_YEAR = 2014  # CMIP6 'historical' experiment always ends here

    runtime_cfg = config.get("runtime") or {}
    _start = runtime_cfg.get("time")
    _stop = runtime_cfg.get("stop")

    def _experiment_folder(experiment: str) -> Path:
        if folder_template:
            return folder_root / folder_template.format(model=model, scenario=experiment)
        return folder_root

    def _spliced_paths(filename_template: str) -> list:
        """One continuous file list for a `????`-year-glob filename (e.g.
        "tas_bc_*_disagg_????.nc"; "{exp}" optionally substituted with the
        experiment name for filenames that embed it, e.g. pseudo_tcc's own
        "rsds_bc_*_{exp}_????.nc") -- splicing historical/scenario as needed
        for THIS run's own start/stop, per this function's own docstring.
        """
        if not _start or not _stop:
            # No restriction possible (e.g. a --dry-run with neither given)
            # -- match the pre-splicing single-scenario behavior exactly.
            pattern = str(_experiment_folder(scenario) / filename_template.format(exp=scenario))
            return expand_year_glob(pattern, _start, _stop)

        start_year = datetime.datetime.fromisoformat(_start).year
        stop_year = datetime.datetime.fromisoformat(_stop).year

        paths = []
        if start_year <= HIST_CUTOFF_YEAR:
            hist_stop = _stop if stop_year <= HIST_CUTOFF_YEAR else f"{HIST_CUTOFF_YEAR}-12-31"
            pattern = str(_experiment_folder("historical") / filename_template.format(exp="historical"))
            paths += expand_year_glob(pattern, _start, hist_stop)
        if stop_year > HIST_CUTOFF_YEAR:
            scen_start = _start if start_year > HIST_CUTOFF_YEAR else f"{HIST_CUTOFF_YEAR + 1}-01-01"
            pattern = str(_experiment_folder(scenario) / filename_template.format(exp=scenario))
            paths += expand_year_glob(pattern, scen_start, _stop)
        return paths

    # CMIP6-raw: one whole-period file per variable per experiment (not a
    # year-per-file glob like the bias-corrected archive), so splicing only
    # ever needs at most 2 files (historical + scenario), not expand_year_
    # glob's per-year expansion. Defined unconditionally (same pattern as
    # _spliced_paths above) -- only ever CALLED from inside a `source ==
    # "CMIP6-raw"` branch below, but a helper defined only inside one such
    # branch and called from a separate, later one is unbound the moment
    # they're not literally the same `if` block (Python has no block
    # scoping, but a conditionally-executed def still isn't executed on a
    # path that skips it) -- keeping every closure's OWN def unconditional,
    # like _spliced_paths already is, avoids that trap entirely.
    #
    # rsds/rsus/pr previously came back renamed (to a shared 'sw', and to
    # 'precip' respectively) by ocean-data's own CMIP6 normalisation --
    # name_mappings.py's CF-attribute-based standardize_variable_names,
    # meant for OCEAN CMIP6 variables (thetao/so/tos -> temp/salt/sst)
    # matching on generic CF standard_name/long_name substrings, which
    # unintentionally also caught these atmospheric ones. Fixed at the
    # source (ocean-data's data_loader.py:_normalise_cmip6_conventions now
    # skips that rename for known atmospheric variable_ids) and the 9
    # already-fetched files affected were repaired in place (ncrename) --
    # every raw-fetched file now keeps its own real CMIP6 variable_id, so
    # this translation table is no longer needed. Kept as an explicit
    # empty mapping (not deleted outright) so a REGRESSION in that fix
    # shows up as a clear KeyError-style failure in _raw_var below rather
    # than silently reading the wrong file's variable under a stale name.
    _RAW_STORED_VARNAME: dict = {}

    def _raw_paths(var: str) -> list:
        """At most 2 whole-period files (historical, scenario), picked by
        whether this run's own [start, stop) reaches into each side of
        HIST_CUTOFF_YEAR -- same splicing intent as _spliced_paths,
        simplified because there's only one file per side to pick, not a
        year range to expand. Matched with a `*` wildcard rather than a
        fixed suffix: the real fetched files carry a fetch-method tag
        ("_onfly"/"_nostream", an ocean-data implementation detail, not a
        stable naming convention) that this must not hardcode.
        """
        def _one(experiment: str) -> list:
            pattern = f"{var}_3hr_{experiment}_*.nc"
            candidates = sorted(_experiment_folder(experiment).glob(pattern))
            if not candidates:
                raise FileNotFoundError(
                    f"meteo.CMIP6-raw: no file matching {pattern!r} in "
                    f"{_experiment_folder(experiment)}"
                )
            return [str(candidates[0])]

        if not _start or not _stop:
            return _one(scenario)
        start_year = datetime.datetime.fromisoformat(_start).year
        stop_year = datetime.datetime.fromisoformat(_stop).year
        paths = []
        if start_year <= HIST_CUTOFF_YEAR:
            paths += _one("historical")
        if stop_year > HIST_CUTOFF_YEAR:
            paths += _one(scenario)
        return paths

    def _raw_var(var: str):
        return pygetm.input.from_nc(
            _raw_paths(var), _RAW_STORED_VARNAME.get(var, var),
        )

    if source == "CMIP6-raw":
        # RAW_HIST_START_YEAR guards against silently reading a truncated
        # series: only 1990-2014 was ever fetched for the historical
        # experiment (a deliberate, narrower choice than CMIP6's own full
        # 1850-2014 protocol range -- see fetch_historical.py in ocean-
        # data's own scratch scripts, not this repo), so a run starting
        # earlier than that has no raw data to read at all.
        RAW_HIST_START_YEAR = 1990
        if _start and datetime.datetime.fromisoformat(_start).year < RAW_HIST_START_YEAR:
            raise ValueError(
                f"meteo.CMIP6-raw: no raw historical data before "
                f"{RAW_HIST_START_YEAR} (run starts "
                f"{datetime.datetime.fromisoformat(_start).year}) -- only "
                f"{RAW_HIST_START_YEAR}-{HIST_CUTOFF_YEAR} was fetched for "
                "the historical experiment, not the full 1850-2014 CMIP6 "
                "protocol range."
            )

        # Units: CMIP6's own native units, unconverted by any bias-
        # correction step -- tas/huss/uas/vas/ps all match pygetm.airsea's
        # own expected units directly (K needs -> degC for t2m same as the
        # bias-corrected branch below; huss kg/kg, uas/vas m/s, ps Pa all
        # need no conversion). pr is "kg m-2 s-1" (a mass flux -- 1 kg/m^2
        # of water is 1 mm depth, so this IS already "mm s-1" numerically);
        # sim.airsea.tp wants "m s-1" (pygetm.airsea's own real Array attrs,
        # confirmed via introspection) -- same /1000.0 factor as the bias-
        # corrected branch below, because bias-correction adjusts VALUES
        # only, never units, so raw and bias-corrected pr share this same
        # native-CMIP6 unit convention.
        sim.airsea.t2m.set(_raw_var("tas") - 273.15)
        sim.airsea.qa.set(_raw_var("huss"))
        sim.airsea.u10.set(_raw_var("uas"))
        sim.airsea.v10.set(_raw_var("vas"))
        # Real surface pressure ("ps"), NOT sea-level pressure ("psl") --
        # unlike the bias-corrected branch below (which only ever had psl
        # bias-corrected, per the ocean-prep pipeline's own historical
        # choice), the raw fetch pulled ps directly, which is the
        # meteorologically correct field for an airsea flux model anyway.
        sim.airsea.sp.set(_raw_var("ps"))
        sim.airsea.tp.set(_raw_var("pr") / 1000.0)
    else:
        sim.airsea.t2m.set(pygetm.input.from_nc(_spliced_paths("tas_bc_*_disagg_????.nc"), "tas") - 273.15)
        sim.airsea.qa.set(pygetm.input.from_nc(_spliced_paths("huss_bc_*_disagg_????.nc"), "huss"))
        sim.airsea.u10.set(pygetm.input.from_nc(_spliced_paths("uas_bc_*_disagg_????.nc"), "uas"))
        sim.airsea.v10.set(pygetm.input.from_nc(_spliced_paths("vas_bc_*_disagg_????.nc"), "vas"))
        sim.airsea.sp.set(pygetm.input.from_nc(_spliced_paths("psl_bc_*_disagg_????.nc"), "psl"))
        sim.airsea.tp.set(pygetm.input.from_nc(_spliced_paths("pr_bc_*_disagg_????.nc"), "pr") / 1000.0)

    radiation_source = meteo.get("radiation_source") or "pseudo_tcc"
    # The REAL pygetm value (simulation.airsea.shortwave_method/
    # longwave_method, introspected pygetm.airsea.FluxesFromMeteo fields,
    # choice-flattened onto simulation.airsea directly -- confirmed via a
    # real --print-config, same flattening this function's own docstring
    # already warns about for meteo["CMIP6"] vs meteo itself). NOT a
    # meteo.<source>-level copy -- that used to exist here but could
    # silently drift from this real value with no warning of its own
    # (removed 2026-09-07, see oceanicu_providers.py's _meteo_shared
    # comment for the full story).
    _airsea = config.get("simulation", {}).get("airsea", {})
    shortwave_method = _airsea.get("shortwave_method")
    longwave_method = _airsea.get("longwave_method")
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
            "simulation.airsea.shortwave_method/longwave_method to -1, or "
            "use radiation_source: pseudo_tcc."
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

    def _pseudo_tcc(rsds) -> None:
        """Shared clearness-index-derived cloud-fraction proxy -- same
        computation for both CMIP6 sources, differing only in how `rsds`
        (already the resolved DataArray) was itself read. See this
        function's own top docstring for the calibration/formula details.
        """
        import numpy as np

        solar_constant = 1361.0
        fit_a, fit_b, fit_c = -2.234072161944959, 0.6895789115452573, 0.8957591501340265

        # Raw CMIP6 files keep their native short coordinate names ("lat"/
        # "lon"); the bias-corrected archive's own files use "latitude"/
        # "longitude" -- read whichever this particular DataArray actually
        # has rather than hardcoding one.
        lat_name = "lat" if "lat" in rsds.coords else "latitude"
        lat = np.deg2rad(rsds[lat_name])
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

    if source == "CMIP6-raw":
        if radiation_source == "components":
            sim.airsea.swr.set(_raw_var("rsds") - _raw_var("rsus"))
            sim.airsea.ql.set(_raw_var("rlds") - _raw_var("rlus"))
        elif radiation_source == "pseudo_tcc":
            _pseudo_tcc(_raw_var("rsds"))
        else:
            raise ValueError(
                f"meteo.CMIP6-raw.radiation_source: {radiation_source!r} not "
                "recognized (expected 'components' or 'pseudo_tcc' -- raw "
                "CMIP6 has no net_sw/net_lw composite)"
            )
    else:
        if radiation_source == "net":
            sim.airsea.swr.set(
                pygetm.input.from_nc(_spliced_paths("net_sw_bc_*_disagg_????.nc"), "net_sw")
            )
            sim.airsea.ql.set(
                pygetm.input.from_nc(_spliced_paths("net_lw_bc_*_disagg_????.nc"), "net_lw")
            )
        elif radiation_source == "components":
            sim.airsea.swr.set(
                pygetm.input.from_nc(_spliced_paths("rsds_bc_*_disagg_????.nc"), "rsds")
                - pygetm.input.from_nc(_spliced_paths("rsus_bc_*_disagg_????.nc"), "rsus")
            )
            sim.airsea.ql.set(
                pygetm.input.from_nc(_spliced_paths("rlds_bc_*_disagg_????.nc"), "rlds")
                - pygetm.input.from_nc(_spliced_paths("rlus_bc_*_disagg_????.nc"), "rlus")
            )
        elif radiation_source == "pseudo_tcc":
            _pseudo_tcc(pygetm.input.from_nc(_spliced_paths("rsds_bc_*_{exp}_????.nc"), "rsds"))
        else:
            raise ValueError(
                f"meteo.CMIP6.radiation_source: {radiation_source!r} not recognized "
                "(expected 'net', 'components', or 'pseudo_tcc')"
            )

