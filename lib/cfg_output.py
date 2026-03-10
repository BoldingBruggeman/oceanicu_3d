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
import yaml
import datetime
import numpy as np

import pygetm
# from xarray.coding.times import _time_units_to_timedelta

# 3D ERSEM variables - remember a , after the last item
surface_variables_fabm_ersem_from_3d = ()
bottom_variables_fabm_ersem_from_3d = ()

# keep empty - will be filled further down based on cfg.fabm.config
surface_variables_fabm_from_3d = ()
bottom_variables_fabm_from_3d = ()


def _load_yaml(path: str | Path) -> dict:
    """Load a YAML file and return its top‑level dictionary."""
    with Path(path).open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    # Our YAML wraps everything under the key “varlists”
    return data.get("varlists", {})


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

    # _vars = _load_yaml(cfg.output.config_file)
    _vars = _load_yaml("output.yaml")

    if cfg.output.meteo:
        sim.logger.info("Adding meteo output")
        _path = Path(cfg.output.folder) / (cfg.setup + "_meteo.nc")
        output = sim.output_manager.add_netcdf_file(
            str(_path),
            interval=datetime.timedelta(hours=1),
            sync_interval=None,
            default_dtype=np.float32,
            save_initial=save_initial,
        )
        if isinstance(sim.airsea, pygetm.airsea.FluxesFromMeteo):
            output.request(tuple(_vars["airsea"]["standard"]))
        output.request(tuple(_vars["airsea"]["fluxes"]))

    if cfg.output.tides:
        sim.logger.info("Adding tidal analysis output")
        _path = Path(cfg.output.folder) / (cfg.setup + "_tides.nc")
        output = sim.output_manager.add_netcdf_file(
            str(_path),
            interval=datetime.timedelta(hours=1),
            sync_interval=24,
            default_dtype=np.float32,
            save_initial=save_initial,
        )
        output.request(tuple(_vars["barotropic"]["2d"]["tides"]))

    # predefined temporal frequencies
    if sim.runtype == pygetm.RunType.BAROCLINIC:
        # surface variables at daily frequency
        if cfg.output.baroclinic.daily.surface:
            sim.logger.info("Adding daily surface output")
            time_average = False
            _path = Path(cfg.output.folder) / (cfg.setup + "_surface_daily.nc")
            output = sim.output_manager.add_netcdf_file(
                str(_path),
                interval_units=pygetm.TimeUnit.DAYS,
                interval=1,
                sync_interval=None,
                default_dtype=np.float32,
                save_initial=save_initial,
            )
            for n in _vars["baroclinic"]["2d"]["surface"]:
                output.request(
                    sim[n].isel(z=-1), output_name=n, time_average=time_average
                )
            # for n in surface_variables_fabm_from_3d:
            # output.request(
            # sim[n].isel(z=-1), output_name=n, time_average=time_average
            # )
            # if surface_variables_from_2d:
            # output.request(surface_variables_from_2d, time_average=time_average)

        # bottom variables at daily frequency
        if cfg.output.baroclinic.daily.bottom:
            sim.logger.info("Adding daily bottom output")
            time_average = False
            _path = Path(cfg.output.folder) / (cfg.setup + "_bottom_daily.nc")
            output = sim.output_manager.add_netcdf_file(
                str(_path),
                interval_units=pygetm.TimeUnit.DAYS,
                interval=1,
                sync_interval=None,
                default_dtype=np.float32,
                save_initial=save_initial,
            )
            for n in _vars["baroclinic"]["2d"]["bottom"]:
                output.request(
                    sim[n].isel(z=0), output_name=n, time_average=time_average
                )
            # for n in bottom_variables_fabm_from_3d:
            # output.request(
            # sim[n].isel(z=0), output_name=n, time_average=time_average
            # )
            # if bottom_variables_from_2d:
            # output.request(bottom_variables_from_2d, time_average=time_average)

        # variables at monthly frequency
        if cfg.output.baroclinic.monthly:
            sim.logger.info("Adding monthly output")
            _path = Path(cfg.output.folder) / (cfg.setup + "_monthly.nc")
            output = sim.output_manager.add_netcdf_file(
                str(_path),
                interval_units=pygetm.TimeUnit.MONTHS,
                interval=1,
                sync_interval=None,
                default_dtype=np.float32,
                save_initial=save_initial,
            )
            time_average = True
            output.request(
                tuple(_vars["barotropic"]["3d"]["standard"]),
                time_average=time_average,
            )
            output.request(
                tuple(_vars["baroclinic"]["3d"]["standard"]),
                time_average=time_average,
            )

            # if cfg.vertical_coordinates.type == 3: # KB: should be fixed
            if cfg.vertical_coordinates.type == 3:  # KB: should be fixed
                output.request("hnt", "nug", "ga")

            if sim.fabm:
                if cfg.fabm.config == "ersem":
                    output.reques(
                        tuple(_vars["baroclinic"]["3d"]["fabm"]),
                        time_average=time_average,
                    )
                # KBsurface_variables_fabm_from_3d = (
                # KBsurface_variables_fabm_ersem_from_3d
                # KB)
                # KBbottom_variables_fabm_from_3d = bottom_variables_fabm_ersem_from_3d

                # KBoutput.request(fabm_ersem_output, time_average=time_average)
                else:
                    output.request(*sim.fabm.default_outputs)
            if cfg.runtime.debug_output:
                output.request(
                    tuple(_vars["barotropic"]["3d"]["debug"]),
                    time_average=time_average,
                )
                output.request(
                    tuple(_vars["baroclinic"]["3d"]["debug"]),
                    time_average=time_average,
                )

    # default 2D and 3D output
    if cfg.output.barotropic.default_2d:
        sim.logger.info("Adding default 2D output")
        _path = Path(cfg.output.folder) / (cfg.setup + "_2d.nc")
        output = sim.output_manager.add_netcdf_file(
            str(_path),
            interval=datetime.timedelta(hours=1),
            sync_interval=24,
            default_dtype=np.float32,
            save_initial=save_initial,
        )
        output.request(tuple(_vars["barotropic"]["2d"]["standard"]))
        if cfg.runtime.debug_output:
            output.request(tuple(_vars["barotropic"]["2d"]["debug"]))

    if sim.runtype > pygetm.RunType.BAROTROPIC_2D:
        # 3D variables
        if cfg.output.barotropic.default_3d:
            sim.logger.info("Adding default 3D barotropic output")
            _path = Path(cfg.output.folder) / (cfg.setup + "_3d.nc")
            output = sim.output_manager.add_netcdf_file(
                str(_path),
                interval_units=pygetm.TimeUnit.MONTHS,
                interval=1,
                sync_interval=None,
                default_dtype=np.float32,
                save_initial=save_initial,
            )
            time_average = True
            output.request(
                tuple(_vars["barotropic"]["3d"]["standard"]),
                time_average=time_average,
            )
            if cfg.runtime.debug_output:
                output.request(
                    tuple(_vars["barotropic"]["3d"]["debug"]),
                    time_average=time_average,
                )

        if cfg.output.baroclinic.default:
            if sim.runtype == pygetm.RunType.BAROCLINIC:
                sim.logger.info("Adding default 3D baroclinic output")
                output.request(
                    tuple(_vars["baroclinic"]["3d"]["standard"]),
                    time_average=time_average,
                )
                # if cfg.vertical_coordinates.type == 3: # KB: should be fixed
                if cfg.vertical_coordinates.type == 3:  # KB: should be fixed
                    output.request("hnt", "nug", "ga")
                    if sim.fabm:
                        if cfg.fabm.config == "ersem":
                            # surface_variables_fabm_from_3d = (
                            # output.request(_vars["barotropic_3d_debug_output"], time_average=False)
                            # surface_variables_fabm_ersem_from_3d
                            # )
                            # bottom_variables_fabm_from_3d = (
                            # bottom_variables_fabm_ersem_from_3d
                            # )

                            output.request(
                                tuple(_vars["baroclinic"]["3d"]["fabm"]),
                                time_average=time_average,
                            )
                    else:
                        output.request(*sim.fabm.default_outputs)
                if cfg.runtime.debug_output:
                    output.request(
                        tuple(_vars["baroclinic"]["3d"]["debug"]),
                        time_average=time_average,
                    )
