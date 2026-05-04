"""Configuring the open boundaries

The configuration comes in two steps:
1) Configure the positions of the open boundaries
2) Attach boundary data to the simulation

Actual configuration is determined by the dict - cfg - passed as
argument.

A separation between 2D and 3D boundary data is made. 2D boundary data input
are from TPXO. 3D are not used yet.

"""

import cftime
import pygetm

bdy_type = pygetm.constants.FLATHER_ELEV
bdy_type = pygetm.constants.CLAMPED
bdy_type = pygetm.constants.FLATHER_TRANSPORT


def create(domain, cfg):
    if cfg.domain.boundaries:
        # selct boundary specification based on the setup name
        if cfg.setup == "ns":
            domain.open_boundaries.add_by_index(
                pygetm.Side.WEST,
                0,
                1,
                16,
                type_2d=bdy_type,
                type_3d=0,
            )
            domain.open_boundaries.add_by_index(
                pygetm.Side.WEST,
                0,
                103,
                116,
                type_2d=bdy_type,
                type_3d=0,
            )
            domain.open_boundaries.add_by_index(
                pygetm.Side.NORTH,
                124,
                27,
                60,
                type_2d=bdy_type,
                type_3d=0,
            )

        if cfg.setup == "ena4":
            domain.open_boundaries.add_by_index(
                pygetm.Side.WEST, 0, 0, 94 + 1, type_2d=bdy_type, type_3d=0
            )
            domain.open_boundaries.add_by_index(
                pygetm.Side.WEST,
                0,
                105,
                120 + 1,
                type_2d=bdy_type,
                type_3d=0,
            )
            domain.open_boundaries.add_by_index(
                pygetm.Side.NORTH,
                120,
                1,
                77 + 1,
                type_2d=bdy_type,
                type_3d=0,
            )
            domain.open_boundaries.add_by_index(
                pygetm.Side.SOUTH,
                0,
                1,
                25 + 1,
                type_2d=bdy_type,
                type_3d=0,
            )

        if cfg.setup == "ena8":
            domain.open_boundaries.add_by_index(
                pygetm.Side.WEST,
                0,
                0,
                188 + 1,
                type_2d=bdy_type,
                type_3d=0,
            )
            domain.open_boundaries.add_by_index(
                pygetm.Side.WEST,
                0,
                209,
                240 + 1,
                type_2d=bdy_type,
                type_3d=0,
            )
            domain.open_boundaries.add_by_index(
                pygetm.Side.NORTH,
                240,
                1,
                # 161 + 1,
                153 + 1,
                type_2d=bdy_type,
                type_3d=0,
            )

        if cfg.setup == "amm7":
            domain.open_boundaries.add_by_index(
                pygetm.Side.WEST,
                1,
                1,
                353,
                type_2d=bdy_type,
                type_3d=0,
            )
            domain.open_boundaries.add_by_index(
                pygetm.Side.NORTH,
                373,
                55,
                284,
                type_2d=bdy_type,
                type_3d=0,
            )
            domain.open_boundaries.add_by_index(
                pygetm.Side.SOUTH,
                1,
                2,
                100,
                type_2d=bdy_type,
                type_3d=0,
            )


def data_2d(sim, cfg):
    # if domain.open_boundaries:
    if cfg.domain.boundaries:
        if cfg.boundaries.barotropic.source == "TPXO":
            from pygetm.input import tpxo

            # Allow using TPXO for both standard and no_leap calendar
            pygetm.otps2.reference_time = cftime.datetime(
                1858, 11, 17, calendar=cfg.simulation.calendar
            )

            sim.logger.info("Getting 2D boundary data from TPXO")
            bdy_lon = sim.open_boundaries.lon
            bdy_lat = sim.open_boundaries.lat
            # Use the TPXO class to get elevations and velocities/transports
            sim.open_boundaries.z.set(
                tpxo.get(bdy_lon, bdy_lat, root=cfg.tides.folder),
                on_grid=True,
            )
            sim.open_boundaries.u.set(
                tpxo.get(bdy_lon, bdy_lat, variable="u", root=cfg.tides.folder),
                on_grid=True,
            )
            sim.open_boundaries.v.set(
                tpxo.get(bdy_lon, bdy_lat, variable="v", root=cfg.tides.folder),
                on_grid=True,
            )

        if cfg.boundaries.barotropic.source == "CMEMS":

            sim.logger.info("Getting 2D boundary data from CMEMS")
            fn = cfg.boundaries.barotropic.folder / cfg.boundaries.barotropic.filename 
            bdy_lon = sim.open_boundaries.lon
            bdy_lat = sim.open_boundaries.lat
            sim.open_boundaries.z.set(
                pygetm.input.from_nc(fn, "zos"),
                on_grid=True,
            )
            sim.open_boundaries.u.set(
                pygetm.input.from_nc(fn, "uo"),
                on_grid=True,
            )
            sim.open_boundaries.v.set(
                pygetm.input.from_nc(fn, "vo"),
                on_grid=True,
            )


def data_3d(sim, cfg):
    # Here 3D boundary data can be attached from e.g. WOA or CMEMS
    if cfg.domain.boundaries:
        if cfg.boundaries.baroclinic.source == "WOA":
            sim.logger.info("setting up 3D WOA boundary conditions")
            sim["temp"].open_boundaries.type = pygetm.SPONGE
            sim["temp"].open_boundaries.values.set(
                pygetm.input.from_nc(cfg.hydrography.folder / "woa_t.nc", "t_an"),
                on_grid=False,
                climatology=True,
            )
            sim["salt"].open_boundaries.type = pygetm.SPONGE
            sim["salt"].open_boundaries.values.set(
                pygetm.input.from_nc(cfg.hydrography.folder / "woa_s.nc", "s_an"),
                on_grid=False,
                climatology=True,
            )
        if cfg.boundaries.baroclinic.source == "CMEMS":
            sim.logger.info("setting up 3D CMEMS boundary conditions")
            fn = cfg.boundaries.baroclinic.folder / cfg.boundaries.baroclinic.filename 
            sim["temp"].open_boundaries.type = pygetm.SPONGE
            sim["temp"].open_boundaries.values.set(
                pygetm.input.from_nc(fn, "thetao"),
                on_grid=True,
                climatology=False,
            )
            sim["salt"].open_boundaries.type = pygetm.SPONGE
            sim["salt"].open_boundaries.values.set(
                pygetm.input.from_nc(fn, "so"),
                on_grid=True,
                climatology=False,
            )
