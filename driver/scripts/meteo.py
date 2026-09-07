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
    if meteo.get("source") != "CMIP6":
        return

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

    sim.airsea.t2m.set(pygetm.input.from_nc(_spliced_paths("tas_bc_*_disagg_????.nc"), "tas") - 273.15)
    sim.airsea.qa.set(pygetm.input.from_nc(_spliced_paths("huss_bc_*_disagg_????.nc"), "huss"))
    sim.airsea.u10.set(pygetm.input.from_nc(_spliced_paths("uas_bc_*_disagg_????.nc"), "uas"))
    sim.airsea.v10.set(pygetm.input.from_nc(_spliced_paths("vas_bc_*_disagg_????.nc"), "vas"))
    sim.airsea.sp.set(pygetm.input.from_nc(_spliced_paths("psl_bc_*_disagg_????.nc"), "psl"))
    sim.airsea.tp.set(pygetm.input.from_nc(_spliced_paths("pr_bc_*_disagg_????.nc"), "pr") / 1000.0)

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
        import numpy as np

        rsds = pygetm.input.from_nc(_spliced_paths("rsds_bc_*_{exp}_????.nc"), "rsds")

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
