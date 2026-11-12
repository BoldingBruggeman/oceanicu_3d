"""FABM setup that needs Python rather than data_assignments: the ERSEM
dependencies (fabm.<source>.data_script, see oceanicu_providers.py). Loaded by
"path/to/file.py:name", never imported."""

from __future__ import annotations

# Module level on purpose: codegen embeds only the hook's function body, and
# the generated _utils.py supplies its own pygetm_config-free
# resolve_data_path/expand_year_glob. An import inside the function would be
# copied into the generated script and fail where pygetm_config isn't installed.
from pathlib import Path

from pygetm_config.loader import expand_year_glob, resolve_data_path


def configure_fabm(sim, domain, config: dict) -> None:
    """As cfg_fabm.py's configure(): FABM dependencies (sim.fabm.get_dependency
    -- gelbstoff absorption, atmospheric CO2/N2O, N-deposition, fishing
    pressure), which data_assignments can't reach.

    Runs only with FABM on, for source ERSEM, and for BAROCLINIC runs (no
    barotropic support by design). Tracer boundaries come from
    oceanicu_providers.derive_data_assignments; tracer ICs from
    hydrography.py's set_hydrography_ic. Runs on every chunk.
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

    # Validated config: the active choice's fields are on fabm_cfg itself
    # (fabm_cfg["folder"], not fabm_cfg["ERSEM"]["folder"]).
    fabm_folder = Path(resolve_data_path(fabm_cfg["folder"]))

    # --- ERSEM dependencies (as cfg_fabm.py's configure()) ---
    sim.fabm.get_dependency("gelbstoff_absorption_satellite").set(
        pygetm.input.from_nc(fabm_folder / "ADY_gle.nc", "gelbstoff_absorption_satellite"),
        on_grid=False,
        climatology=True,
    )
    # Atmospheric CO2/N2O: input4MIPs GHG concentrations as 15-degree latitude
    # bands (gn-15x360deg, subset to AMM7's bands, broadcast over 2 lon points),
    # in ${GHG_CONCENTRATION_FOLDER}/{co2,n2o}_{historical,<scenario>}_15deg.nc
    # (download_ghg_15deg.py, saved with the data). Historical (1990-2014) is
    # always read; with a scenario the scenario file (from 2015-01) is spliced
    # after it. The older {co2,n2o}_{scenario}.nc files there are unused.
    fabm_cfg_boundaries = config.get("boundaries", {}).get("fabm") or {}
    # Scenario: boundaries.fabm's own (a CMIP6 run keeps atmosphere and
    # boundaries on one scenario), else fabm.ERSEM.ghg_scenario (CMEMS/WOA).
    co2_scenario = fabm_cfg_boundaries.get("scenario") or fabm_cfg.get("ghg_scenario")
    # Without a scenario the historical files end 2014-12; fail at setup, not
    # when TemporalInterpolation runs out of data mid-run.
    runtime_cfg = config.get("runtime") or {}
    _ghg_stop = runtime_cfg.get("stop")
    if not co2_scenario and _ghg_stop and datetime.datetime.fromisoformat(_ghg_stop) > datetime.datetime(2015, 1, 1):
        raise ValueError(
            f"configure_fabm: no GHG scenario available (boundaries.fabm.CMIP6.scenario unset "
            f"and fabm.ERSEM.ghg_scenario unset) but this run's own stop date ({_ghg_stop}) is "
            "past the historical CO2/N2O/N-deposition files' real 2014-12 end -- set "
            "fabm.ERSEM.ghg_scenario (e.g. 'ssp245') or this will crash mid-run once the "
            "historical-only series runs out of data."
        )
    # A noleap simulation (raw CMIP6) needs the '_noleap' copies (inlined,
    # not shared: hooks are embedded one function at a time).
    _calendar_suffix = (
        "_noleap" if config.get("runtime", {}).get("calendar") == "noleap" else ""
    )
    # has_dependency: the carbonate module may be off or absent in fabm.yaml.
    if sim.fabm.has_dependency("mole_fraction_of_carbon_dioxide_in_air"):
        ghg_folder = Path(resolve_data_path("${GHG_CONCENTRATION_FOLDER}"))
        co2_hist_path = ghg_folder / f"co2_historical_15deg{_calendar_suffix}.nc"
        if co2_scenario:
            co2_scen_path = ghg_folder / f"co2_{co2_scenario}_15deg{_calendar_suffix}.nc"
            sim.fabm.get_dependency("mole_fraction_of_carbon_dioxide_in_air").set(
                pygetm.input.from_nc(
                    [str(co2_hist_path), str(co2_scen_path)],
                    "mole_fraction_of_carbon_dioxide_in_air",
                ),
                on_grid=False,
            )
            sim.logger.info(
                f"configure_fabm: providing mole_fraction_of_carbon_dioxide_in_air "
                f"from {co2_hist_path} + {co2_scen_path}"
            )
        else:
            sim.fabm.get_dependency("mole_fraction_of_carbon_dioxide_in_air").set(
                pygetm.input.from_nc(str(co2_hist_path), "mole_fraction_of_carbon_dioxide_in_air"),
                on_grid=False,
            )
            sim.logger.info(f"configure_fabm: providing mole_fraction_of_carbon_dioxide_in_air from {co2_hist_path}")

    # Atmospheric N2O for ERSEM's nitrous_oxide module (iswN2O), same files
    # and splice as CO2. The file is in ppb; ERSEM wants natm -- numerically
    # equal at ~1 atm, so no scaling (as FABM's own test environment).
    if sim.fabm.has_dependency("partial_pressure_of_n2o"):
        ghg_folder = Path(resolve_data_path("${GHG_CONCENTRATION_FOLDER}"))
        n2o_hist_path = ghg_folder / f"n2o_historical_15deg{_calendar_suffix}.nc"
        if co2_scenario:
            n2o_scen_path = ghg_folder / f"n2o_{co2_scenario}_15deg{_calendar_suffix}.nc"
            sim.fabm.get_dependency("partial_pressure_of_n2o").set(
                pygetm.input.from_nc(
                    [str(n2o_hist_path), str(n2o_scen_path)],
                    "mole_fraction_of_nitrous_oxide_in_air",
                ),
                on_grid=False,
            )
            sim.logger.info(
                f"configure_fabm: providing partial_pressure_of_n2o from "
                f"{n2o_hist_path} + {n2o_scen_path}"
            )
        else:
            sim.fabm.get_dependency("partial_pressure_of_n2o").set(
                pygetm.input.from_nc(str(n2o_hist_path), "mole_fraction_of_nitrous_oxide_in_air"),
                on_grid=False,
            )
            sim.logger.info(f"configure_fabm: providing partial_pressure_of_n2o from {n2o_hist_path}")

    # N-deposition: AMM7_Ndep_BC-EMEP_{historical,<scenario>}_y????.nc
    # (historical 1993-2014, scenarios 2015-2100) with a scenario, else the
    # old climatology AMM7-EMEP-NDeposition_y????.nc. Restricted to this
    # run's years with expand_year_glob: a bare ???? would also match the
    # other scenarios' files in the same folder.
    NDEP_HIST_CUTOFF_YEAR = 2014  # this dataset's own historical/scenario split -- matches meteo.py's CMIP6 HIST_CUTOFF_YEAR

    _start = runtime_cfg.get("time")
    _stop = runtime_cfg.get("stop")

    # A chunk restarting exactly at 2015-01-01 still needs the historical
    # file, to bracket its first request against the scenario's first record.
    _start_is_new_year_right_after_cutoff = bool(_start) and datetime.datetime.fromisoformat(_start) == datetime.datetime(
        NDEP_HIST_CUTOFF_YEAR + 1, 1, 1
    )

    def _ndep_scenario_paths(scenario: str) -> list:
        if not _start or not _stop:
            pattern = str(fabm_folder / f"Ndep/AMM7_Ndep_BC-EMEP_{scenario}_y????.nc")
            return expand_year_glob(pattern, _start, _stop)

        start_year = datetime.datetime.fromisoformat(_start).year
        stop_year = datetime.datetime.fromisoformat(_stop).year

        paths = []
        if start_year <= NDEP_HIST_CUTOFF_YEAR:
            hist_stop = _stop if stop_year <= NDEP_HIST_CUTOFF_YEAR else f"{NDEP_HIST_CUTOFF_YEAR}-12-31"
            pattern = str(fabm_folder / "Ndep/AMM7_Ndep_BC-EMEP_historical_y????.nc")
            paths += expand_year_glob(pattern, _start, hist_stop)
        elif _start_is_new_year_right_after_cutoff:
            pattern = str(fabm_folder / "Ndep/AMM7_Ndep_BC-EMEP_historical_y????.nc")
            paths += expand_year_glob(pattern, f"{NDEP_HIST_CUTOFF_YEAR}-01-01", f"{NDEP_HIST_CUTOFF_YEAR}-12-31")
        if stop_year > NDEP_HIST_CUTOFF_YEAR:
            scen_start = _start if start_year > NDEP_HIST_CUTOFF_YEAR else f"{NDEP_HIST_CUTOFF_YEAR + 1}-01-01"
            pattern = str(fabm_folder / f"Ndep/AMM7_Ndep_BC-EMEP_{scenario}_y????.nc")
            paths += expand_year_glob(pattern, scen_start, _stop)
        return paths

    # N3_flux_ff/N4_flux_ff: the products are land-masked NaN where NSe has
    # wet coastal cells, so the files carry these nearest-neighbour-filled
    # variables next to the original N3_flux/N4_flux.
    if co2_scenario:
        ndep_paths = _ndep_scenario_paths(co2_scenario)
        if sim.fabm.has_dependency("N3_flux/flux"):
            sim.fabm.get_dependency("N3_flux/flux").set(
                pygetm.input.from_nc(ndep_paths, "N3_flux_ff")
            )
        if sim.fabm.has_dependency("N4_flux/flux"):
            sim.fabm.get_dependency("N4_flux/flux").set(
                pygetm.input.from_nc(ndep_paths, "N4_flux_ff")
            )
    else:
        emep_pattern = str(fabm_folder / "Ndep/AMM7-EMEP-NDeposition_y????.nc")
        emep_paths = expand_year_glob(emep_pattern, _start, _stop)
        if sim.fabm.has_dependency("N3_flux/flux"):
            sim.fabm.get_dependency("N3_flux/flux").set(
                pygetm.input.from_nc(emep_paths, "N3_flux_ff")
            )
        if sim.fabm.has_dependency("N4_flux/flux"):
            sim.fabm.get_dependency("N4_flux/flux").set(
                pygetm.input.from_nc(emep_paths, "N4_flux_ff")
            )

    # Fishing pressure, only for a fabm.yaml with the mizer model (fabm_mizer.yaml).
    if sim.fabm.has_dependency("fish/fishing_pressure"):
        fish_path = fabm_folder / "fish/fishing_effort_????_AMM7.nc"
        sim.fabm.get_dependency("fish/fishing_pressure").set(
            pygetm.input.from_nc(str(fish_path), "tot_EffActiveHours")
        )
        sim.logger.info(f"configure_fabm: providing fish/fishing_pressure from {fish_path}")

    # FABM initial conditions (climatology or restart_file) are set in
    # hydrography.py's set_hydrography_ic, which runs on a fresh start only;
    # this hook runs on every chunk.
