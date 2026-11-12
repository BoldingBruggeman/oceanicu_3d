"""Meteo forcing that needs Python rather than data_assignments
(meteo.<source>.data_script and runtime.post_data_script, see
oceanicu_providers.py). Loaded by "path/to/file.py:name", never imported."""

from __future__ import annotations

from pathlib import Path

from pygetm_config.loader import expand_year_glob, resolve_data_path


def set_meteo_data(sim, domain, config: dict) -> None:
    """As cfg_airsea.py's data(), for the CMIP6 sources (ERA5 is plain
    data_assignments): "CMIP6" reads the bias-corrected archive (ocean-prep
    bc-correct), "CMIP6-raw" the raw archive (CMIP6_RAW_FOLDER, ocean-data's
    DataLoader). Sets t2m/qa/u10/v10/sp/tp and the radiation. Runs after
    apply_data_assignments.

    meteo.<source>.radiation_source:
    - "net" (bias-corrected only): net_sw/net_lw, each bias-corrected as one
      composite -> swr/ql.
    - "components": rsds-rsus and rlds-rlus -> swr/ql.
    - "pseudo_tcc" (default): a cloud fraction from daily-mean rsds alone,
      for models without reliable net/upward fluxes. kt = rsds / TOA, with
      TOA the analytic daily-mean insolation (Duffie & Beckman eq. 1.10.3);
      tcc = quadratic fit of kt, calibrated against ERA5 daily-mean
      ssrd/tcc over NW Europe 2024-2025 (R^2=0.636; the calibration scripts
      are not in this repo -- recalibrate formula and fit together). tcc
      drives both the ROSATI_MIYAKODA shortwave and CLARK longwave formulas.
      Reads the daily-mean rsds (not _disagg_): the fit is on daily means.

    simulation.airsea.shortwave_method/longwave_method must match: NET_FLUX
    (-1) for "net"/"components", a bulk formula for "pseudo_tcc"; otherwise
    the forcing is silently ignored. A mismatch gives a warning.

    `{method}` in the bias-corrected filenames is a glob: bc-correct
    currently writes 'bilinear'/'' for every variable, and the glob matches
    both that and the correct names.

    Historical/scenario splice: CMIP6 `historical` ends 2014, so a run
    reaching before 2015 reads historical files for those years and the
    meteo.<source>.scenario files after, as one series -- derived from the
    run's start (runtime.time) and stop (runtime.stop), not configured.

    Self-contained (helpers nested): codegen embeds only this function.
    """
    meteo = config.get("meteo") or {}
    source = meteo.get("source")
    if source not in ("CMIP6", "CMIP6-raw"):
        return

    # Only FluxesFromMeteo fields are written here; any other airsea type
    # gets a clear error instead of an AttributeError or no forcing at all.
    _airsea_type = config.get("simulation", {}).get("airsea", {}).get("type")
    if _airsea_type not in (None, "FluxesFromMeteo"):
        raise ValueError(
            f"meteo.source={source!r} (meteo.data_script) writes FluxesFromMeteo's own fields "
            f"(t2m/qa/u10/v10/sp/tp/swr/ql/tcc), but simulation.airsea.type={_airsea_type!r} -- those "
            "targets don't exist on that airsea class. See oceanicu_providers.derive_data_assignments' "
            "own ERA5-branch comment for the same gap/options."
        )

    import datetime

    import netCDF4
    import pygetm
    import xarray as xr

    # Validated config: the active choice's fields are on `meteo` itself.
    folder_root = Path(resolve_data_path(meteo["folder"]))
    folder_template = meteo.get("folder_template")
    model = meteo.get("model", "")
    scenario = meteo.get("scenario", "")

    HIST_CUTOFF_YEAR = 2014  # CMIP6 'historical' experiment always ends here

    runtime_cfg = config.get("runtime") or {}
    _start = runtime_cfg.get("time")
    _stop = runtime_cfg.get("stop")

    # runtime.time is this chunk's start, not the experiment's. A chunk
    # starting exactly 2015-01-01T00 still needs historical's last record:
    # the scenario files start at 12:00 (daily-mean convention).
    _start_is_new_year_right_after_cutoff = bool(_start) and datetime.datetime.fromisoformat(_start) == datetime.datetime(
        HIST_CUTOFF_YEAR + 1, 1, 1
    )

    def _experiment_folder(experiment: str) -> Path:
        if folder_template:
            return folder_root / folder_template.format(model=model, scenario=experiment)
        return folder_root

    def _spliced_paths(filename_template: str) -> list:
        """Year files for a `????` filename ("{exp}" filled with the
        experiment where the name embeds it), spliced for this run."""
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
        elif _start_is_new_year_right_after_cutoff:
            # Start at the seam: only the last historical year's file.
            pattern = str(_experiment_folder("historical") / filename_template.format(exp="historical"))
            paths += expand_year_glob(pattern, f"{HIST_CUTOFF_YEAR}-01-01", f"{HIST_CUTOFF_YEAR}-12-31")
        if stop_year > HIST_CUTOFF_YEAR:
            scen_start = _start if start_year > HIST_CUTOFF_YEAR else f"{HIST_CUTOFF_YEAR + 1}-01-01"
            pattern = str(_experiment_folder(scenario) / filename_template.format(exp=scenario))
            paths += expand_year_glob(pattern, scen_start, _stop)
        return paths

    # CMIP6-raw: one whole-period file per variable and experiment, so at
    # most two files. Defined unconditionally (a def inside one branch would
    # be unbound when called from another).

    def _raw_paths(var: str) -> list:
        """At most two whole-period files (historical, scenario) for this run.
        `*` matches the fetch-method tag ("_onfly"/"_nostream")."""
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
        if start_year <= HIST_CUTOFF_YEAR or _start_is_new_year_right_after_cutoff:
            paths += _one("historical")
        if stop_year > HIST_CUTOFF_YEAR:
            paths += _one(scenario)
        return paths

    def _open_with_small_cache(path: str):
        """Open *path* with a ~1 MB HDF5 chunk cache for all its variables
        instead of the 16-64 MB default (the files are chunked [1, nlat,
        nlon]; every MPI rank pays for the cache). netCDF4's chunk cache is a
        process-wide default captured at open time, so: shrink, open,
        restore (try/finally) -- later files, including GETM's own output,
        keep the default. Returns a NetCDF4DataStore, which from_nc accepts
        like a path."""
        default_cache = netCDF4.get_chunk_cache()
        netCDF4.set_chunk_cache(1_000_000, 521, 0.75)
        try:
            nc = netCDF4.Dataset(path, "r")
        finally:
            netCDF4.set_chunk_cache(*default_cache)
        return xr.backends.NetCDF4DataStore(nc)

    def _raw_var(var: str):
        stores = [_open_with_small_cache(path) for path in _raw_paths(var)]
        return pygetm.input.from_nc(stores, var)

    if source == "CMIP6-raw":
        # Only 1990-2014 was fetched for the raw historical experiment.
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

        # Native CMIP6 units (bias correction changes values, not units):
        # tas K -> degC; pr kg m-2 s-1 is mm s-1, tp wants m s-1 (/1000).
        sim.airsea.t2m.set(_raw_var("tas") - 273.15)
        sim.airsea.qa.set(_raw_var("huss"))
        sim.airsea.u10.set(_raw_var("uas"))
        sim.airsea.v10.set(_raw_var("vas"))
        # Surface pressure ps (the bias-corrected archive only has psl).
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
    # shortwave/longwave_method from simulation.airsea (choice-flattened).
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
        """Set sim.airsea.tcc from *rsds* (see set_meteo_data's docstring)."""
        import numpy as np

        solar_constant = 1361.0
        fit_a, fit_b, fit_c = -2.234072161944959, 0.6895789115452573, 0.8957591501340265

        # Raw CMIP6 has lat/lon, the bias-corrected archive latitude/longitude.
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


def set_sst_proxy(sim, domain, config: dict) -> None:
    """Barotropic runs have no model SST, but FluxesFromMeteo needs one: use
    t2m (as cfg_airsea.py; otherwise sim.start() fails with "sst is
    masked"). Runs after data_assignments, via runtime.post_data_script."""
    import pygetm

    if sim.runtype < pygetm.RunType.BAROCLINIC:
        sim.sst = sim.airsea.t2m
