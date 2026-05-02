"""Configuring the air/sea exchange

The configuration comes in two steps:
1) Create an airsea instance
2) Attach meteorological data to the airsea object

Actual configuration is determined by the dict - cfg - passed as
argument. Configuration supports ERA5 and CMIP6 data sources.

Data are attached using the pyGETM input manager and specifically reading
data from pre-downloaded NetCDF files.

Whether to use ERA5 or CMIP6 is controlled by the cfg.airsea.source
configuration key.

"""

from pathlib import Path

import pygetm


def create(cfg) -> pygetm.airsea:
    # If a meteo source is not set use the simple Fluxes meethod
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

    # create and use file names for the different meteo variables
    if isinstance(sim.airsea, pygetm.airsea.FluxesFromMeteo):
        if cfg.meteo.source == "ERA5":
            _file = Path(cfg.meteo.folder) / "era5_u10_????.nc"
            sim.airsea.u10.set(pygetm.input.from_nc(str(_file), "u10"))
            _file = Path(cfg.meteo.folder) / "era5_v10_????.nc"
            sim.airsea.v10.set(pygetm.input.from_nc(str(_file), "v10"))
            _file = Path(cfg.meteo.folder) / "era5_t2m_????.nc"
            sim.airsea.t2m.set(pygetm.input.from_nc(str(_file), "t2m") - 273.15)
            _file = Path(cfg.meteo.folder) / "era5_d2m_????.nc"
            sim.airsea.d2m.set(pygetm.input.from_nc(str(_file), "d2m") - 273.15)
            _file = Path(cfg.meteo.folder) / "era5_sp_????.nc"
            sim.airsea.sp.set(pygetm.input.from_nc(str(_file), "sp"))
            _file = Path(cfg.meteo.folder) / "era5_tp_????.nc"
            sim.airsea.tp.set(pygetm.input.from_nc(str(_file), "tp") / 3600.0)
            _file = Path(cfg.meteo.folder) / "era5_tcc_????.nc"
            sim.airsea.tcc.set(pygetm.input.from_nc(str(_file), "tcc"))
            # if (
            #     not sim.airsea.shortwave_method == pygetm.NET_FLUX
            #     or not sim.airsea.shortwave_method == pygetm.DOWNWARD_FLUX
            #     or not sim.airsea.longwave_method == pygetm.NET_FLUX
            #     or not sim.airsea.longwave_method == pygetm.DOWNWARD_FLUX
            # ):
            #     meteo_path = Path(meteo_dir) / "era5_tcc_????.nc"
            #     sim.airsea.tcc.set(pygetm.input.from_nc(str(meteo_path), "tcc"))
            # else:
            #     sim.airsea.tcc.set(0.5)

            if sim.airsea.shortwave_method == pygetm.NET_FLUX:
                _file = Path(cfg.meteo.folder) / "era5_ssr_????.nc"
                sim.airsea.swr.set(pygetm.input.from_nc(str(_file), "ssr") / 3600.0)
            if sim.airsea.shortwave_method == pygetm.DOWNWARD_FLUX:
                _file = Path(cfg.meteo.folder) / "era5_ssrd_????.nc"
                sim.airsea.swr_downwards.set(
                    pygetm.input.from_nc(str(_file), "ssrd") / 3600.0
                )

            if sim.airsea.longwave_method == pygetm.NET_FLUX:
                _file = Path(cfg.meteo.folder) / "era5_str_????.nc"
                sim.airsea.ql.set(pygetm.input.from_nc(str(_file), "str") / 3600.0)
            if sim.airsea.longwave_method == pygetm.DOWNWARD_FLUX:
                _file = Path(cfg.meteo.folder) / "era5_strd_????.nc"
                sim.airsea.ql_downwards.set(
                    pygetm.input.from_nc(str(_file), "strd") / 3600.0
                )

        if cfg.meteo.source == "CMIP6":
            _file = Path(cfg.meteo.folder) / "uas.nc"
            sim.airsea.u10.set(pygetm.input.from_nc(str(_file), "uas"))
            _file = Path(cfg.meteo.folder) / "vas.nc"
            sim.airsea.v10.set(pygetm.input.from_nc(str(_file), "vas"))
            _file = Path(cfg.meteo.folder) / "tas.nc"
            sim.airsea.t2m.set(pygetm.input.from_nc(str(_file), "tas") - 273.15)
            _file = Path(cfg.meteo.folder) / "huss.nc"
            sim.airsea.qa.set(pygetm.input.from_nc(str(_file), "huss"))
            _file = Path(cfg.meteo.folder) / "ps.nc"
            sim.airsea.sp.set(pygetm.input.from_nc(str(_file), "ps"))
            _file = Path(cfg.meteo.folder) / "pr.nc"
            sim.airsea.tp.set(pygetm.input.from_nc(str(meteo_path), "pr") / 1000.0)

            rsds_path = Path(cfg.meteo.folder) / "rsds.nc"
            rsus_path = Path(cfg.meteo.folder) / "rsus.nc"
            sim.airsea.swr.set(
                pygetm.input.from_nc(str(rsds_path), "rsds")
                - pygetm.input.from_nc(str(rsus_path), "rsus")
            )
            rlds_path = Path(cfg.meteo.folder) / "rlds.nc"
            rlus_path = Path(cfg.meteo.folder) / "rlus.nc"
            sim.airsea.ql.set(
                pygetm.input.from_nc(str(rlds_path), "rlds")
                - pygetm.input.from_nc(str(rlus_path), "rlus")
            )
            # required even if not used
            sim.airsea.tcc.set(0.5)

        # if not a baroclinic run use the t2m temperatures as proxy for SST
        if sim.runtype < pygetm.RunType.BAROCLINIC:
            sim.sst = sim.airsea.t2m
