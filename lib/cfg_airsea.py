"""Configuring the air/sea exchange

The configuration comes in two steps:
1) Create an airsea instance
2) Attach meteorological data to the airsea object

Actual configuration is determined by the dict - cfg - passed as
argument. Configuration supports ERA5 and CMIP6 data sources.

Data are attached using the pyGETM input manager and specifically reading
data from pre-downloaded NetCDF files.

Whether to use ERA5 or CMIP6 is controlled by the cfg.meteo.source
configuration key. The active source sub-section (cfg.meteo.ERA5 or
cfg.meteo.CMIP6) provides the folder path.

"""

from pathlib import Path

import pygetm


def create(cfg) -> pygetm.airsea:
    # If a meteo source is not set use the simple Fluxes method
    if not cfg.meteo.source:
        airsea = pygetm.airsea.Fluxes()
        calculate_evaporation = False
    else:
        # Dew point variable differs between the two sources
        if cfg.meteo.source == "ERA5":
            humidity_measure = pygetm.HumidityMeasure.DEW_POINT_TEMPERATURE
        if cfg.meteo.source == "CMIP6":
            humidity_measure = pygetm.HumidityMeasure.SPECIFIC_HUMIDITY

        _shortwave_method = cfg.meteo.shortwave_method
        _longwave_method = cfg.meteo.longwave_method

        # This can go when fixed upstream
        if cfg.meteo.longwave_method == -1 or cfg.meteo.longwave_method == -2:
            _longwave_method = cfg.meteo.longwave_method
        else:
            n = cfg.meteo.longwave_method
            _longwave_method = pygetm.LongwaveMethod.CLARK

        airsea = pygetm.airsea.FluxesFromMeteo(
            shortwave_method=_shortwave_method,
            longwave_method=_longwave_method,
            humidity_measure=humidity_measure,
            calculate_evaporation=cfg.meteo.evaporation,
        )
    return airsea


def data(sim, cfg):
    if isinstance(sim.airsea, pygetm.airsea.Fluxes):
        sim.airsea.taux[...] = 0.0
        sim.airsea.tauy[...] = 0.0
        sim.airsea.swr[...] = 0.0
        sim.airsea.shf[...] = 0.0

    if isinstance(sim.airsea, pygetm.airsea.FluxesFromMeteo):
        _meteo_cfg = getattr(cfg.meteo, cfg.meteo.source)
        if cfg.meteo.source == "CMIP6":
            _folder = Path(_meteo_cfg.folder) / _meteo_cfg.model / _meteo_cfg.scenario
        else:
            _folder = Path(_meteo_cfg.folder)

        if cfg.meteo.source == "ERA5":
            sim.airsea.u10.set(pygetm.input.from_nc(str(_folder / "era5_u10_????.nc"), "u10"))
            sim.airsea.v10.set(pygetm.input.from_nc(str(_folder / "era5_v10_????.nc"), "v10"))
            sim.airsea.t2m.set(pygetm.input.from_nc(str(_folder / "era5_t2m_????.nc"), "t2m") - 273.15)
            sim.airsea.d2m.set(pygetm.input.from_nc(str(_folder / "era5_d2m_????.nc"), "d2m") - 273.15)
            sim.airsea.sp.set(pygetm.input.from_nc(str(_folder / "era5_sp_????.nc"), "sp"))
            sim.airsea.tp.set(pygetm.input.from_nc(str(_folder / "era5_tp_????.nc"), "tp") / 3600.0)
            sim.airsea.tcc.set(pygetm.input.from_nc(str(_folder / "era5_tcc_????.nc"), "tcc"))

            if sim.airsea.shortwave_method == pygetm.NET_FLUX:
                sim.airsea.swr.set(pygetm.input.from_nc(str(_folder / "era5_ssr_????.nc"), "ssr") / 3600.0)
            if sim.airsea.shortwave_method == pygetm.DOWNWARD_FLUX:
                sim.airsea.swr_downwards.set(
                    pygetm.input.from_nc(str(_folder / "era5_ssrd_????.nc"), "ssrd") / 3600.0
                )
            if sim.airsea.longwave_method == pygetm.NET_FLUX:
                sim.airsea.ql.set(pygetm.input.from_nc(str(_folder / "era5_str_????.nc"), "str") / 3600.0)
            if sim.airsea.longwave_method == pygetm.DOWNWARD_FLUX:
                sim.airsea.ql_downwards.set(
                    pygetm.input.from_nc(str(_folder / "era5_strd_????.nc"), "strd") / 3600.0
                )

        if cfg.meteo.source == "CMIP6":
            sim.airsea.u10.set(pygetm.input.from_nc(str(_folder / "uas.nc"), "uas"))
            sim.airsea.v10.set(pygetm.input.from_nc(str(_folder / "vas.nc"), "vas"))
            sim.airsea.t2m.set(pygetm.input.from_nc(str(_folder / "tas.nc"), "tas") - 273.15)
            sim.airsea.qa.set(pygetm.input.from_nc(str(_folder / "huss.nc"), "huss"))
            sim.airsea.sp.set(pygetm.input.from_nc(str(_folder / "ps.nc"), "ps"))
            sim.airsea.tp.set(pygetm.input.from_nc(str(_folder / "pr.nc"), "pr") / 1000.0)

            sim.airsea.swr.set(
                pygetm.input.from_nc(str(_folder / "rsds.nc"), "rsds")
                - pygetm.input.from_nc(str(_folder / "rsus.nc"), "rsus")
            )
            sim.airsea.ql.set(
                pygetm.input.from_nc(str(_folder / "rlds.nc"), "rlds")
                - pygetm.input.from_nc(str(_folder / "rlus.nc"), "rlus")
            )
            # required even if not used
            sim.airsea.tcc.set(0.5)

        # if not a baroclinic run use the t2m temperatures as proxy for SST
        if sim.runtype < pygetm.RunType.BAROCLINIC:
            sim.sst = sim.airsea.t2m
