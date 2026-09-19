#!/usr/bin/env python
"""check_inputs.py -- extended dry-run pre-flight checks for chunk_runner.py.

Given a pygetm_config.codegen --dump-python generated script and a
[start, stop] period (the WHOLE simulation, not one chunk), checks that
every input file the run actually depends on exists, is readable, and
covers the requested period, and reports where output will land.

Two kinds of dependency, handled differently:

- Codegen-time literals (bathymetry, open boundaries, gotm.yaml, output
  paths): baked into the generated script's own source as literal
  resolve_data_path(...)/add_netcdf_file(path=...) calls. Found by
  regex-scanning the script's source TEXT -- never imported/executed;
  a dry-run must not risk touching a real simulation, and some of
  these scripts are not import-safe anyway (argparse.parse_args() at
  module level in their _utils.py companion).
- Runtime-computed dependencies (meteo, rivers, initial-condition
  hydrography): computed from the run's own config dict by
  driver/scripts/meteo.py, rivers.py, hydrography.py -- but those
  files themselves `from pygetm_config.loader import ...`, which is
  NOT installed in the production `pygetm` conda env that actually
  runs chunk_runner.py (confirmed directly, 2026-09-18). So this
  module keeps its OWN self-contained duplicates of their path-
  building logic and of resolve_data_path/expand_year_glob (the exact
  same "duplicate rather than share" idiom already used throughout
  driver/scripts/*.py for the same reason), instead of importing any
  of them.
"""

from __future__ import annotations

import dataclasses
import datetime
import os
import re
from pathlib import Path
from typing import Optional


# --- self-contained boilerplate, duplicated (not imported) from the real
# pygetm_config.loader / generated *_utils.py source -- see module
# docstring for why this can't just import them. ---

def _resolve_data_path(path: str) -> str:
    var_pattern = re.compile(r"\$\{?(\w+)\}?")
    resolved = os.path.expandvars(path)
    missing = sorted(set(var_pattern.findall(resolved)))
    if missing:
        raise RuntimeError(
            f"{path!r}: environment variable(s) {', '.join(missing)} not set "
            "(needed to resolve a data file/folder path) -- set them directly, "
            "via a real env var, or via --data-roots-file"
        )
    return resolved


def _apply_data_roots(roots_file: Optional[str]) -> None:
    if not roots_file:
        return
    import yaml

    with open(roots_file) as f:
        roots = yaml.safe_load(f) or {}
    for key, value in roots.items():
        os.environ.setdefault(key, str(value))


def _expand_year_glob(pattern: str, start: Optional[str], stop: Optional[str]) -> list:
    import glob

    if "????" not in pattern:
        return [_resolve_data_path(pattern)]
    if not start or not stop:
        return sorted(glob.glob(_resolve_data_path(pattern)))
    start_year = datetime.datetime.fromisoformat(start).year
    stop_dt = datetime.datetime.fromisoformat(stop)
    stop_year = max(stop_dt.year, start_year)

    def _resolve_wildcard(path: str) -> str:
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
        _resolve_wildcard(_resolve_data_path(pattern.replace("????", str(year))))
        for year in range(start_year, stop_year + 1)
    ]
    if stop_dt.month == 12:
        buffer_path = _resolve_data_path(pattern.replace("????", str(stop_year + 1)))
        if glob.has_magic(buffer_path):
            matches = sorted(glob.glob(buffer_path))
            if len(matches) > 1:
                raise ValueError(
                    f"expand_year_glob: {buffer_path!r} matches {len(matches)} "
                    f"files {matches!r} -- ambiguous, refusing to guess which one"
                )
            if matches:
                paths.append(matches[0])
        elif os.path.exists(buffer_path):
            paths.append(buffer_path)
    return paths


def _list_meteo_files(config: dict, start: str, stop: str) -> list:
    """Mirrors driver/scripts/meteo.py's set_meteo_data _raw_paths/
    _spliced_paths resolution, for the whole [start, stop] instead of
    one chunk. Never raises on a missing file. Returns
    (description, path_or_pattern, found, var) tuples -- `var` is the
    variable name set_meteo_data actually reads from the file, so the
    caller can check it's really inside, not just that the file opens."""
    meteo = config.get("meteo") or {}
    source = meteo.get("source")
    if source not in ("CMIP6", "CMIP6-raw"):
        return []

    folder_root = Path(_resolve_data_path(meteo["folder"]))
    folder_template = meteo.get("folder_template")
    model = meteo.get("model", "")
    scenario = meteo.get("scenario", "")
    radiation_source = meteo.get("radiation_source") or "pseudo_tcc"

    HIST_CUTOFF_YEAR = 2014
    start_year = datetime.datetime.fromisoformat(start).year
    stop_year = datetime.datetime.fromisoformat(stop).year

    def _experiment_folder(experiment: str) -> Path:
        if folder_template:
            return folder_root / folder_template.format(model=model, scenario=experiment)
        return folder_root

    results = []

    if source == "CMIP6-raw":
        variables = ["tas", "huss", "uas", "vas", "ps", "pr"]
        if radiation_source == "components":
            variables += ["rsds", "rsus", "rlds", "rlus"]
        elif radiation_source == "pseudo_tcc":
            variables += ["rsds"]
        else:
            variables = []

        for var in variables:
            for experiment, active in (
                ("historical", start_year <= HIST_CUTOFF_YEAR),
                (scenario, stop_year > HIST_CUTOFF_YEAR),
            ):
                if not active:
                    continue
                pattern = f"{var}_3hr_{experiment}_*.nc"
                folder = _experiment_folder(experiment)
                candidates = sorted(folder.glob(pattern))
                desc = f"CMIP6-raw {var} ({experiment})"
                if candidates:
                    results.append((desc, str(candidates[0]), True, var))
                else:
                    results.append((desc, str(folder / pattern), False, var))

    else:  # bias-corrected CMIP6
        var_templates = [
            ("tas", "tas_bc_*_disagg_????.nc"),
            ("huss", "huss_bc_*_disagg_????.nc"),
            ("uas", "uas_bc_*_disagg_????.nc"),
            ("vas", "vas_bc_*_disagg_????.nc"),
            ("psl", "psl_bc_*_disagg_????.nc"),
            ("pr", "pr_bc_*_disagg_????.nc"),
        ]
        if radiation_source == "net":
            var_templates += [
                ("net_sw", "net_sw_bc_*_disagg_????.nc"),
                ("net_lw", "net_lw_bc_*_disagg_????.nc"),
            ]
        elif radiation_source == "components":
            var_templates += [
                ("rsds", "rsds_bc_*_disagg_????.nc"),
                ("rsus", "rsus_bc_*_disagg_????.nc"),
                ("rlds", "rlds_bc_*_disagg_????.nc"),
                ("rlus", "rlus_bc_*_disagg_????.nc"),
            ]
        elif radiation_source == "pseudo_tcc":
            var_templates += [("rsds", "rsds_bc_*_{exp}_????.nc")]

        segments = []
        if start_year <= HIST_CUTOFF_YEAR:
            hist_stop = stop if stop_year <= HIST_CUTOFF_YEAR else f"{HIST_CUTOFF_YEAR}-12-31"
            segments.append(("historical", start, hist_stop))
        if stop_year > HIST_CUTOFF_YEAR:
            scen_start = start if start_year > HIST_CUTOFF_YEAR else f"{HIST_CUTOFF_YEAR + 1}-01-01"
            segments.append((scenario, scen_start, stop))

        for var, template in var_templates:
            for experiment, seg_start, seg_stop in segments:
                pattern_str = str(_experiment_folder(experiment) / template.format(exp=experiment))
                matches = _expand_year_glob(pattern_str, seg_start, seg_stop)
                desc = f"CMIP6 {var} ({experiment}, {seg_start}..{seg_stop})"
                if matches:
                    for m in matches:
                        results.append((desc, m, True, var))
                else:
                    results.append((desc, pattern_str, False, var))

    return results


def _list_river_files(config: dict, start: str, stop: str) -> list:
    """Mirrors driver/scripts/rivers.py's add_rivers/set_river_data
    folder/filename logic -- each whole file's own coverage is checked
    by the caller (_time_coverage_ok), not here.

    For river_discharge.source == "CMIP6" specifically, mirrors
    set_river_data's own historical/scenario splice (same 2014/2015
    boundary as meteo.py): if [start, stop] reaches back before 2015,
    the real EMORID historical file (${RIVER_FOLDER}/RIVER_FILE, NOT
    ${RIVER_FOLDER_CMIP6}) is ALSO listed -- otherwise the checker would
    keep reporting a coverage gap that set_river_data's own splice
    already closes for real.
    Returns [(description, path, found, var)] -- both the projection
    and the real EMORID file store discharge as "Q"."""
    rcfg = config.get("river_discharge")
    if not rcfg:
        return []
    folder = Path(_resolve_data_path(rcfg["folder"]))
    if rcfg.get("folder_template"):
        folder = folder / rcfg["folder_template"].format(
            model=rcfg.get("model", ""), scenario=rcfg.get("scenario", "")
        )
    filename = rcfg["file"].format(model=rcfg.get("model", ""), scenario=rcfg.get("scenario", ""))
    if config.get("runtime", {}).get("calendar") == "noleap":
        filename = filename.removesuffix(".nc") + "_noleap.nc"
    path = folder / filename
    results = [("river discharge", str(path), path.is_file(), "Q")]

    HIST_CUTOFF_YEAR = 2014
    if rcfg.get("source") == "CMIP6" and datetime.datetime.fromisoformat(start).year <= HIST_CUTOFF_YEAR:
        hist_folder = Path(_resolve_data_path("${RIVER_FOLDER}"))
        hist_filename = os.environ.get("RIVER_FILE", "EMORID_1990_2024.nc")
        if config.get("runtime", {}).get("calendar") == "noleap":
            hist_filename = hist_filename.removesuffix(".nc") + "_noleap.nc"
        hist_path = hist_folder / hist_filename
        results.append(("river discharge (historical)", str(hist_path), hist_path.is_file(), "Q"))

    return results


def _list_ic_files(config: dict) -> list:
    """Mirrors driver/scripts/hydrography.py's set_hydrography_ic --
    only WOA/CMEMS read real files ("constant" hydrography has none).
    Returns [(description, path, found, var)]."""
    hydro = config.get("hydrography") or {}
    source = hydro.get("source")
    if source not in ("WOA", "CMEMS"):
        return []
    folder = Path(_resolve_data_path(hydro["folder"]))
    if source == "WOA":
        files = [("hydrography IC salt (WOA)", folder / "woa_s.nc", "s_an"),
                 ("hydrography IC temp (WOA)", folder / "woa_t.nc", "t_an")]
    else:
        files = [("hydrography IC salt (CMEMS)", folder / "so_2025_monthly_ic.nc", "so_ff"),
                 ("hydrography IC temp (CMEMS)", folder / "thetao_2025_monthly_ic.nc", "thetao_ff")]
    return [(desc, str(path), path.is_file(), var) for desc, path, var in files]


def _check_file(path: str, *, var: Optional[str] = None, expect_time: bool = False) -> tuple[bool, str]:
    """Existence + (for .nc files) a real open, to catch truncated/
    corrupt downloads, not just missing files -- plus, cheaply, since
    the file is already open: that `var` (the variable name the driver
    script actually reads, e.g. 'tas'/'Q'/'thetao') is really present,
    and (if `expect_time`) that it has a real, usable 'time' coordinate
    (present, with a 'units' attribute -- pygetm's TemporalInterpolation
    needs that to do anything at all). Always closes the handle
    explicitly (a leaked handle exhausted this session's fd limit once
    already, in an unrelated script -- see
    docs/hdf5-chunk-cache-and-memory.md)."""
    p = Path(path)
    if not p.is_file():
        return False, "missing"
    if p.suffix != ".nc":
        return True, "ok"
    import netCDF4

    d = None
    try:
        d = netCDF4.Dataset(path, "r")
    except Exception as exc:
        return False, f"unreadable/corrupt: {exc}"
    try:
        if var is not None and var not in d.variables:
            sample = ", ".join(sorted(d.variables)[:8])
            return False, f"opens fine but variable {var!r} not found (has: {sample}, ...)"
        if expect_time:
            if "time" not in d.variables:
                return False, "opens fine but has no 'time' variable"
            if not getattr(d.variables["time"], "units", None):
                return False, "'time' variable has no 'units' attribute -- unusable for interpolation"
        return True, "ok"
    finally:
        d.close()


def _as_tuple(dt) -> tuple:
    """(year, month, day, hour, minute, second) -- comparable regardless
    of calendar (cftime.datetime refuses to compare across calendars,
    e.g. a noleap river file's dates vs. a plain datetime.datetime -- a
    real, reproduced crash -- but every cftime/datetime flavor exposes
    these same plain int attributes)."""
    return (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def _read_time_bounds(path: str) -> Optional[tuple]:
    """(cov_start, cov_stop) of a .nc file's own time axis (as whatever
    cftime/datetime type netCDF4.num2date returns for its calendar), or
    None if it has none / can't be read (e.g. 360_day, or no time
    variable at all)."""
    import netCDF4

    d = None
    try:
        d = netCDF4.Dataset(path, "r")
        if "time" not in d.variables:
            return None
        v = d.variables["time"]
        units = getattr(v, "units", None)
        if not units:
            return None
        calendar = getattr(v, "calendar", "standard")
        return tuple(netCDF4.num2date([v[0], v[-1]], units=units, calendar=calendar))
    except Exception:
        return None
    finally:
        if d is not None:
            d.close()


@dataclasses.dataclass
class InputCheck:
    category: str
    description: str
    path: str
    ok: bool
    detail: str = ""


@dataclasses.dataclass
class OutputCheck:
    path: str
    parent_exists: bool
    writable: bool
    coverage_note: str = ""


@dataclasses.dataclass
class CheckReport:
    inputs: list
    outputs: list

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.inputs) and all(o.parent_exists and o.writable for o in self.outputs)


_DEF_RE = re.compile(r"^def (\w+)\(")
_RESOLVE_RE = re.compile(r"resolve_data_path\((['\"])(.*?)\1\)")
_DATE_RANGE_RE = re.compile(r"(\d{8})_(\d{8})")
_FROM_NC_VAR_RE = re.compile(r"from_nc\(\[.*\],\s*['\"](\w+)['\"]\)")
_OUTPUT_BLOCK_RE = re.compile(
    r"(output_file_\w+)\s*=\s*sim\.output_manager\.add_netcdf_file\(\s*"
    r"path=pathlib\.Path\(resolve_data_path\((['\"])(.*?)\2\)\)"
)


def _parse_cftime_args(args_text: str) -> Optional[datetime.datetime]:
    nums = []
    for part in args_text.split(","):
        part = part.strip()
        if "=" in part:
            continue
        try:
            nums.append(int(part))
        except ValueError:
            pass
    if len(nums) < 3:
        return None
    nums += [0] * (6 - len(nums))
    try:
        return datetime.datetime(*nums[:6])
    except ValueError:
        return None


def check_generated_script(
    script_path: str,
    start: str,
    stop: str,
    *,
    data_roots_file: Optional[str] = None,
    load_restart: Optional[str] = None,
    fabm: Optional[str] = None,
) -> CheckReport:
    """Pre-flight-check every input file `script_path` (a
    pygetm_config.codegen --dump-python generated script) will need for
    [start, stop] (the WHOLE run, not one chunk), and report where its
    configured output files will land.

    `load_restart`: pass whatever the real run would pass (or any
    truthy placeholder) to skip the initial-condition check -- it's
    only relevant for a fresh run (chunk 0), same gate
    hydrography.set_hydrography_ic itself uses.
    `fabm`: a real fabm.yaml path to check, or None to skip (a bare
    "enable FABM with its own configured default" can't be resolved
    without importing pygetm.RunType/etc, which this module
    deliberately never does).
    """
    _apply_data_roots(data_roots_file)

    script = Path(script_path)
    text = script.read_text()

    config_path = script.parent / f"{script.stem}_config.yaml"
    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f)

    req_start = datetime.datetime.fromisoformat(start)
    req_stop = datetime.datetime.fromisoformat(stop)

    inputs: list = [InputCheck("config", "companion config YAML", str(config_path), config_path.is_file())]
    outputs: list = []
    seen_paths: set = set()

    # --- static scan: bathymetry / open boundaries / other codegen-time
    # literals (gotm.yaml, etc) -- everything OUTSIDE configure_output(). ---
    lines = text.splitlines()
    def_at = [(i, m.group(1)) for i, line in enumerate(lines, start=1)
              for m in [_DEF_RE.match(line)] if m]

    def _enclosing_def(line_no: int) -> Optional[str]:
        name = None
        for i, n in def_at:
            if i <= line_no:
                name = n
            else:
                break
        return name

    for line_no, line in enumerate(lines, start=1):
        if _enclosing_def(line_no) == "configure_output":
            continue
        matches = list(_RESOLVE_RE.finditer(line))
        if not matches:
            continue
        func = _enclosing_def(line_no)
        literal_paths = [m.group(2) for m in matches]
        date_ranges = [_DATE_RANGE_RE.search(p) for p in literal_paths]
        date_ranges = [(m.group(1), m.group(2)) for m in date_ranges if m]

        coverage_note = ""
        coverage_ok = True
        boundary_var = None
        if date_ranges:
            category = "boundary"
            starts = [datetime.datetime.strptime(a, "%Y%m%d") for a, _ in date_ranges]
            stops = [datetime.datetime.strptime(b, "%Y%m%d") for _, b in date_ranges]
            cov_start, cov_stop = min(starts), max(stops)
            coverage_ok = cov_start <= req_start and req_stop <= cov_stop
            coverage_note = f"covers {cov_start.date()}..{cov_stop.date()}"
            if not coverage_ok:
                coverage_note += f" -- requested {req_start.date()}..{req_stop.date()} not fully covered"
            var_m = _FROM_NC_VAR_RE.search(line)
            boundary_var = var_m.group(1) if var_m else None
        else:
            category = "bathymetry" if func == "create_domain" else "simulation setup"

        for p in literal_paths:
            # Dedup key includes the variable, not just the path -- the
            # SAME bdy_2d_*.nc file is referenced on separate lines for
            # zos/uo/vo (three different variables, one file each); a
            # path-only dedup would check the first and silently skip
            # ever validating the other two exist (a real, caught bug).
            seen_key = (p, boundary_var)
            if seen_key in seen_paths:
                continue
            seen_paths.add(seen_key)
            try:
                resolved = _resolve_data_path(p)
            except RuntimeError as exc:
                inputs.append(InputCheck(category, p, p, False, str(exc)))
                continue
            ok, detail = _check_file(resolved, var=boundary_var, expect_time=(boundary_var is not None))
            if ok and not coverage_ok:
                ok, detail = False, coverage_note
            elif ok and coverage_note:
                detail = coverage_note
            inputs.append(InputCheck(category, p, resolved, ok, detail))

    # --- static scan: configure_output()'s own literal output paths,
    # each optionally paired with its own start/stop window. ---
    for m in _OUTPUT_BLOCK_RE.finditer(text):
        var_name, literal = m.group(1), m.group(3)
        idx = var_name.rsplit("_", 1)[-1]
        try:
            resolved = _resolve_data_path(literal)
        except RuntimeError as exc:
            outputs.append(OutputCheck(literal, False, False, str(exc)))
            continue
        parent = Path(resolved).parent
        parent_exists = parent.is_dir()
        writable = parent_exists and os.access(parent, os.W_OK)

        coverage_note = ""
        start_m = re.search(rf"_output_file_{idx}_start\s*=\s*cftime\.datetime\(([^)]*)\)", text)
        stop_m = re.search(rf"_output_file_{idx}_stop\s*=\s*cftime\.datetime\(([^)]*)\)", text)
        if start_m and stop_m:
            win_start = _parse_cftime_args(start_m.group(1))
            win_stop = _parse_cftime_args(stop_m.group(1))
            if win_start and win_stop:
                coverage_note = f"own window {win_start.date()}..{win_stop.date()}"
                if not (win_start < req_stop and req_start < win_stop):
                    coverage_note += f" -- does not overlap requested {req_start.date()}..{req_stop.date()}"
        outputs.append(OutputCheck(resolved, parent_exists, writable, coverage_note))

    # --- runtime-computed: meteo, rivers, initial-condition hydrography ---
    # Each `_list_*` helper resolves ${VAR} data-path references itself
    # (same _resolve_data_path as the static-scan section above) -- a
    # missing env var is a real, reportable pre-flight failure, not a
    # crash, so it's caught here the same way the static scan already
    # catches it per-literal. _expand_year_glob's own ValueError
    # (2+ files matching one year's wildcard -- ambiguous, refuses to
    # guess) is exactly as real a pre-flight failure: the real driver
    # script's own set_meteo_data would hit the identical crash at
    # runtime (a real, reproduced case: a stray duplicate-looking file
    # left over on disk, e.g. tas_bc_bilinear__disagg_2010.nc alongside
    # the correctly-named tas_bc_bilinear_historical_disagg_2010.nc) --
    # a --dry-run's whole point is to surface that now, in a report
    # covering everything else too, not die on the first one.
    try:
        meteo_files = _list_meteo_files(config, start, stop)
    except (RuntimeError, ValueError) as exc:
        inputs.append(InputCheck("meteo", "meteo.folder", "(unresolved)", False, str(exc)))
        meteo_files = []
    for desc, path_or_pattern, found, var in meteo_files:
        if not found:
            inputs.append(InputCheck("meteo", desc, path_or_pattern, False, "not found"))
            continue
        ok, detail = _check_file(path_or_pattern, var=var, expect_time=True)
        inputs.append(InputCheck("meteo", desc, path_or_pattern, ok, detail))

    try:
        river_files = _list_river_files(config, start, stop)
    except RuntimeError as exc:
        inputs.append(InputCheck("river", "river_discharge.folder", "(unresolved)", False, str(exc)))
        river_files = []

    # _list_river_files can return more than one file (source == "CMIP6"
    # spliced with the real EMORID historical record -- see
    # driver/scripts/rivers.py's own set_river_data) -- what needs to
    # cover [req_start, req_stop] is their COMBINED span, not each file's
    # own span independently (the historical file alone never reaches
    # 2099; the projection file alone never reaches back before 2015 --
    # neither is a failure on its own once the other is present).
    req_start_t, req_stop_t = _as_tuple(req_start), _as_tuple(req_stop)
    river_reports = []
    river_bounds = []
    for desc, path, found, var in river_files:
        if not found:
            river_reports.append((desc, path, False, "not found"))
            continue
        ok, detail = _check_file(path, var=var, expect_time=True)
        if ok:
            bounds = _read_time_bounds(path)
            if bounds:
                cov_start, cov_stop = bounds
                river_bounds.append((_as_tuple(cov_start), _as_tuple(cov_stop)))
                detail = f"covers {cov_start}..{cov_stop}"
        river_reports.append((desc, path, ok, detail))

    # Real coverage, not just the outer envelope -- min(starts)..max(stops)
    # alone would call this OK even with a genuine gap in the middle (e.g.
    # the historical file truncated short of 2015, scenario file starting
    # exactly at 2015 as expected): sort by start and require each file to
    # pick up no later than the previous one's own stop, same as the outer
    # bracket check, not just the two endpoints.
    river_bounds.sort(key=lambda b: b[0])
    union_ok = bool(river_bounds) and river_bounds[0][0] <= req_start_t and req_stop_t <= river_bounds[-1][1]
    if union_ok:
        for (_, prev_stop), (cur_start, _) in zip(river_bounds, river_bounds[1:]):
            if cur_start > prev_stop:
                union_ok = False
                break
    for desc, path, file_ok, detail in river_reports:
        ok = file_ok and (union_ok if river_bounds else True)
        if file_ok and not ok:
            detail += f" -- combined coverage does not fully cover requested {req_start}..{req_stop}"
        inputs.append(InputCheck("river", desc, path, ok, detail))

    if load_restart is None:
        try:
            ic_files = _list_ic_files(config)
        except RuntimeError as exc:
            inputs.append(InputCheck("initial condition", "hydrography.folder", "(unresolved)", False, str(exc)))
            ic_files = []
        for desc, path, found, var in ic_files:
            if not found:
                inputs.append(InputCheck("initial condition", desc, path, False, "not found"))
                continue
            ok, detail = _check_file(path, var=var, expect_time=True)
            inputs.append(InputCheck("initial condition", desc, path, ok, detail))

    if fabm:
        try:
            resolved = _resolve_data_path(fabm)
        except RuntimeError as exc:
            inputs.append(InputCheck("fabm", "fabm.yaml", fabm, False, str(exc)))
        else:
            ok, detail = _check_file(resolved)
            inputs.append(InputCheck("fabm", "fabm.yaml", resolved, ok, detail))

    return CheckReport(inputs=inputs, outputs=outputs)


def format_report(report: CheckReport) -> str:
    lines = ["=== extended dry-run: input files ==="]
    for c in report.inputs:
        mark = "OK  " if c.ok else "FAIL"
        lines.append(f"[{mark}] {c.category:18s} {c.description}")
        lines.append(f"        {c.path}")
        if c.detail:
            lines.append(f"        {c.detail}")
    lines.append("")
    lines.append("=== extended dry-run: output files ===")
    for o in report.outputs:
        mark = "OK  " if (o.parent_exists and o.writable) else "WARN"
        lines.append(f"[{mark}] {o.path}")
        lines.append(f"        parent exists={o.parent_exists} writable={o.writable}")
        if o.coverage_note:
            lines.append(f"        {o.coverage_note}")
    lines.append("")
    lines.append("ALL CHECKS PASSED" if report.ok else "SOME CHECKS FAILED")
    return "\n".join(lines)
