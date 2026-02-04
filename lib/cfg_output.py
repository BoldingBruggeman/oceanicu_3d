"""Configuring output

To simplify the run script output specification is done here.

A number of lists of variables is defined and used in calls to
output.request.
Various FABM output specifications can be defined using the
cfg.fabm.config key - e.g. seach for "ersem"

The output specifications are attached to the simulation object and no
further actions are required in terms of output.

"""

from pathlib import Path
import datetime
import numpy as np

import pygetm

# Definitions of various output lists
airsea_standard_output = (
    "u10",
    "v10",
    "sp",
    "t2m",
    "tcc",
    "tp",
)
airsea_fluxes_output = (
    "swr",
    "shf",
    "tausxu",
    "tausyv",
)
barotropic_2d_output = (
    "Ht",
    "zt",
    "Dt",
    "u1",
    "v1",
)
barotropic_2d_debug_output = (
    "maskt",
    "masku",
    "maskv",
    "dxt",
    "dyt",
    "Hu",
    "Hv",
    "U",
    "V",
    "Du",
    "Dv",
    "dpdx",
    "dpdy",
    "z0bu",
    "z0bv",
    "z0bt",
    "ru",
    "rv",
)
barotropic_3d_output = (
    "uk",
    "vk",
    "ww",
    "SS",
    "num",
)
barotropic_3d_debug_output = (
    # "fpk",
    # "fqk",
    "rru",
    "rrv",
    "advpk",
    "advqk",
    "diffpk",
    "diffqk",
)
baroclinic_3d_output = (
    "temp",
    "salt",
    "rho",
    "nuh",
)
baroclinic_3d_debug_output = (
    "rad",
    "sst",
    "hnt",
    "idpdx",
    "idpdy",
)
fabm_ersem_output = (
    "P1_c",
    "P2_c",
    "P3_c",
    "P4_c",
    "O2_o",
    "N3_n",
    "N4_n",
    "N1_p",
    "N5_s",
    "Q6_c",
    "Y2_c",
    "Y3_c",
    "O3_c",
    "O3_fair",
    "Q7_c",
    "Q6_pen_depth_c",
    "ben_col_D1m",
    "ben_col_D2m",
    "N5_s_bdy",
    "N5_s_sms",
    "N5_s_sfl",
    "N5_s_bfl",
    "N5_s_sfl_tot_calculator_result",
    "N5_s_bfl_tot_calculator_result",
    "N5_s_sms_tot_calculator_result",
)


def create(
    # sim: pygetm.simulation.MySimulation,
    # sim: MySimulation,
    sim,
    cfg,
    save_initial: bool = False,
):
    """Configure and create NetCDF output files.

    For each file a number of settings can be changed - like saving
    frequency.

    For the output requests the time_average variable can be changed.

    """
    sim.logger.info("Setting up output")

    _path = Path(cfg.runtime.output_folder) / (cfg.setup + "_meteo.nc")
    output = sim.output_manager.add_netcdf_file(
        str(_path),
        interval=datetime.timedelta(hours=1),
        sync_interval=None,
        default_dtype=np.float32,
        save_initial=save_initial,
    )
    if isinstance(sim.airsea, pygetm.airsea.FluxesFromMeteo):
        output.request(airsea_standard_output)
    output.request(airsea_fluxes_output)

    _path = Path(cfg.runtime.output_folder) / (cfg.setup + "_2d.nc")
    output = sim.output_manager.add_netcdf_file(
        str(_path),
        interval=datetime.timedelta(hours=1),
        sync_interval=24,
        default_dtype=np.float32,
        save_initial=save_initial,
    )
    output.request(barotropic_2d_output)
    if cfg.runtime.debug_output:
        output.request(barotropic_2d_debug_output)

    if sim.runtype > pygetm.RunType.BAROTROPIC_2D:
        _path = Path(cfg.runtime.output_folder) / (cfg.setup + "_3d.nc")
        output = sim.output_manager.add_netcdf_file(
            str(_path),
            interval_units=pygetm.TimeUnit.HOURS,
            interval=6,
            #interval_units=pygetm.TimeUnit.TIMESTEPS,
            #interval=30,
            # time_average=True,
            sync_interval=None,
            default_dtype=np.float32,
            save_initial=save_initial,
        )
        output.request(barotropic_3d_output, time_average=False)
        if cfg.runtime.debug_output:
            output.request(barotropic_3d_debug_output, time_average=False)

        if sim.runtype == pygetm.RunType.BAROCLINIC:
            output.request(baroclinic_3d_output, time_average=False)
            # if cfg.vertical_coordinates.type == 3: # KB: should be fixed
            if cfg.vertical_coordinates.type == 3:  # KB: should be fixed
                output.request("hnt", "nug", "ga")
            if sim.fabm:
                if cfg.fabm.config == "ersem":
                    output.request(fabm_ersem_output, time_average=False)
                else:
                    output.request(*sim.fabm.default_outputs)
            if cfg.runtime.debug_output:
                output.request(baroclinic_3d_debug_output, time_average=False)
