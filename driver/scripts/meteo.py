"""Meteo forcing pieces that genuinely need real Python, not a static
data_assignments entry (a domain config's meteo.<source>.data_script and the
post_data_script hook -- see oceanicu_providers.py). Mirrors cfg_airsea.py's
own data() -- see each function's own docstring.

Loaded via pygetm_config.providers.load_dotted_target ("path/to/file.py:name"),
never imported directly -- see oceanicu_driver.py's own module docstring for
how that's wired (meteo.<source>.data_script/post_data_script defaults, both
pointing here).
"""

from __future__ import annotations

from pathlib import Path

from pygetm_config.loader import expand_year_glob, resolve_data_path


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

    import netCDF4
    import pygetm
    import xarray as xr

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
        """Open with a small (~1MB) per-variable HDF5 chunk cache, not
        HDF5's own default (16-64MB observed) -- that default was sized
        for the files' old near-contiguous storage; now that they're
        rechunked to [1, nlat, nlon] (~4.8KB/chunk, see the 2026-09-17
        rechunk of /data/CMIP6/GFDL-ESM4/{historical,ssp370}), a 16-64MB
        cache holds thousands of chunks nobody needs, and pygetm's
        per-MPI-rank opens each pay for that memory independently.
        Must be set via a raw netCDF4.Dataset BEFORE any data is read --
        xr.open_dataset(path) has no kwarg for this. Handing pygetm.
        input.from_nc() an already-open xarray NetCDF4DataStore (instead
        of the usual bare path string) works because from_nc()/_open()
        just forward whatever `paths` elements they're given straight to
        xr.open_dataset() -- a DataStore is accepted there exactly like
        a path. This ONLY affects reading these CMIP6-raw meteo files;
        GETM's own history/restart writer is a separate code path this
        never touches.
        """
        varname = _RAW_STORED_VARNAME.get(var, var)
        stores = []
        for path in _raw_paths(var):
            nc = netCDF4.Dataset(path, "r")
            nc.variables[varname].set_var_chunk_cache(1_000_000, 521, 0.75)
            stores.append(xr.backends.NetCDF4DataStore(nc))
        return pygetm.input.from_nc(stores, varname)

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


def set_sst_proxy(sim, domain, config: dict) -> None:
    """Barotropic runtypes (BAROTROPIC_2D/BAROTROPIC_3D) have no computed sea
    surface temperature to give pygetm's FluxesFromMeteo airsea
    implementation (which requires sst to be set regardless of runtype), so
    they use t2m (2m air temperature) as a stand-in. BAROCLINIC runs have a
    real, model-calculated SST and don't need this substitution at all.
    Mirrors cfg_airsea.py's own data() function -- its comment there: "if
    not a baroclinic run use the t2m temperatures as proxy for SST"
    (`sim.sst = sim.airsea.t2m`). Without this, sim.start()
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
