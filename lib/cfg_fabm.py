"""Configuring FABM

Fullfil dependencies, add initial conditions, boundary conditions,
and river loadings.

FABM output is configured in lib/cfg_output

"""

import sys
from pathlib import Path
import xarray as xr

import pygetm


# A routine by Gennadi to make data compatible with the input manager
def _add_coord(nc):  # Nicolas - make more generic later
    global _fabm_folder
    fmesh = xr.open_dataset(_fabm_folder / "mesh_mask.nc")
    nc = nc.drop_vars(("lon", "lat"))
    nc = nc.rename_dims(x="longitude", y="latitude")
    lat_AMM = fmesh.nav_lat.data
    lon_AMM = fmesh.nav_lon.data
    nc = nc.rename_dims({"t": "time"})
    nc = nc.rename_vars({"t": "time"})
    nc = nc.assign_coords(
        latitude=("latitude", lat_AMM[:, 0]), longitude=("longitude", lon_AMM[0, :])
    )
    nc.coords["latitude"] = nc.latitude.assign_attrs(
        long_name="latitude", units="degrees_north"
    )
    nc.coords["longitude"] = nc.longitude.assign_attrs(
        long_name="longitude", units="degrees_east"
    )
    nc.coords["time"] = nc.time.assign_attrs(long_name="time")
    nc = nc.fillna(0.0)
    return nc


def configure(sim, cfg, imonth):
    """The FABM instance is already configured via a FABM yaml file

    Support for different FABM setups can be configured using the
    cfg.fabm.config key in the YAML-configuration file.

    There is no limit on which configurations can be done but it
    includes providing dependecies, initial conditions, boundary
    conditions and river loadings.

    """

    global _fabm_folder

    if not sim.fabm:
        return

    if cfg.fabm.config == "ersem":
        sim.logger.info(
            f"Providing FABM configuration for {cfg.fabm.config} (not provided by pyGETM)"
        )
        #  print("Available dependencies:",list(sim.fabm.model.dependencies._lookup.keys()))
        # sim.fabm.get_dependency("absorption_of_silt").set(0.07)
        #
        _fabm_folder = cfg.fabm.folder
        sim.fabm.get_dependency("gelbstoff_absorption_satellite").set(
            pygetm.input.from_nc(
                _fabm_folder / "ADY_gle.nc", "gelbstoff_absorption_satellite"
            ),
            on_grid=False,
            climatology=True,
        )

        # sim.fabm.get_dependency("sediment_porosity").set(pygetm.input.from_nc(os.path.join(setup_dir,"porosity_map.nc"),'Porosity'),on_grid=False)
        # sim.fabm.get_dependency("sediment_grain_size").set(pygetm.input.from_nc(os.path.join(setup_dir,"amm7_d50.nc"),"TotalD50"),on_grid=False)
        sim.fabm.get_dependency("mole_fraction_of_carbon_dioxide_in_air").set(400.0)
        if sim.runtype == pygetm.BAROTROPIC_3D:
            sim.fabm.get_dependency("temperature").set(5.0)
            sim.fabm.get_dependency("practical_salinity").set(35.0)
            sim.fabm.get_dependency("density").set(1025.0)

        _EMEP_path = _fabm_folder / "Ndep/AMM7-EMEP-NDeposition_y2020.nc"
        # EMEP_path = Path(fabm_dir) / f"Ndep/AMM7-EMEP-NDeposition_y{year}.nc"
        #    for year in range(__YEAR__ - 1, __YEAR__ + 2)
        # ]
        sim.fabm.get_dependency("N3_flux/flux").set(
            pygetm.input.from_nc(_EMEP_path, "N3_flux", preprocess=_add_coord)
        )
        sim.fabm.get_dependency("N4_flux/flux").set(
            pygetm.input.from_nc(_EMEP_path, "N4_flux", preprocess=_add_coord)
        )

        _woa_folder = cfg.hydrography.folder
        sim["N3_n"].set(
            pygetm.input.from_nc(_woa_folder / "woa_n.nc", "n_an").isel(time=imonth)
        )
        sim["N1_p"].set(
            pygetm.input.from_nc(_woa_folder / "woa_p.nc", "p_an").isel(time=imonth)
        )
        sim["N5_s"].set(
            pygetm.input.from_nc(_woa_folder / "woa_i.nc", "i_an").isel(time=imonth)
        )
        sim["O2_o"].set(
            pygetm.input.from_nc(_woa_folder / "woa_o.nc", "o_an").isel(time=imonth)
        )

        sim["N3_n"].open_boundaries.type = pygetm.SPONGE
        sim["N3_n"].open_boundaries.values.set(
            pygetm.input.from_nc(_woa_folder / "woa_n.nc", "n_an"),
            on_grid=False,
            climatology=True,
        )
        sim["N1_p"].open_boundaries.type = pygetm.SPONGE
        sim["N1_p"].open_boundaries.values.set(
            pygetm.input.from_nc(_woa_folder / "woa_p.nc", "p_an"),
            on_grid=False,
            climatology=True,
        )
        sim["N5_s"].open_boundaries.type = pygetm.SPONGE
        sim["N5_s"].open_boundaries.values.set(
            pygetm.input.from_nc(_woa_folder / "woa_i.nc", "i_an"),
            on_grid=False,
            climatology=True,
        )
        sim["O2_o"].open_boundaries.type = pygetm.SPONGE
        sim["O2_o"].open_boundaries.values.set(
            pygetm.input.from_nc(_woa_folder / "woa_o.nc", "o_an"),
            on_grid=False,
            climatology=True,
        )
    else:
        sim.logger.critical(
            f"No FABM configuration for {cfg.fabm.config} has been found - add in lib/fabm.py"
        )
        sys.exit(1)
