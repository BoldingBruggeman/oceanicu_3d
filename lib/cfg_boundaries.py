"""Configuring the open boundaries

The configuration comes in two steps:
1) Configure the positions of the open boundaries
2) Attach boundary data to the simulation

Actual configuration is determined by the dict - cfg - passed as
argument.

A separation between 2D and 3D boundary data is made. 2D boundary data input
are from TPXO. 3D are not used yet.

"""

from pathlib import Path

import cftime
import pygetm

from .cfg_utils import resolve_path as _resolve_path_base

bdy_type = pygetm.constants.FLATHER_ELEV
bdy_type = pygetm.constants.CLAMPED
bdy_type = pygetm.constants.FLATHER_TRANSPORT


def _resolve_path(src_cfg, setup: str) -> Path:
    return _resolve_path_base(src_cfg, setup=setup)


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
            domain.open_boundaries.add_by_index(
                pygetm.Side.EAST,
                111,
                62,
                70,
                type_2d=bdy_type,
                type_3d=0,
            )

        if cfg.setup == "ena4":
            type_2d =
            type_3d = 0
            domain.open_boundaries.add_by_index(
                pygetm.Side.WEST, 0, 0, 191 + 1, type_2d=bdy_type, type_3d=0
            )
            domain.open_boundaries.add_by_index(
                pygetm.Side.WEST,
                0,
                0,
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
            domain.open_boundaries.add_by_index(
                pygetm.Side.SOUTH,
                247,
                273,
                276,
                type_2d=bdy_type,
                type_3d=0,
            )
            domain.open_boundaries.add_by_index(
                pygetm.Side.SOUTH,
                247,
                277,
                296,
                type_2d=bdy_type,
                type_3d=0,
            )


def data_2d(sim, cfg):
    # if domain.open_boundaries:
    if cfg.domain.boundaries:
        _source = cfg.boundaries.barotropic.source
        if _source == "TPXO":
            from pygetm.input import tpxo

            # Allow using TPXO for both standard and no_leap calendar
            pygetm.otps2.reference_time = cftime.datetime(
                1858, 11, 17, calendar=cfg.simulation.calendar
            )

            sim.logger.info("Getting 2D boundary data from TPXO")
            bdy_lon = sim.open_boundaries.lon
            bdy_lat = sim.open_boundaries.lat
            # Use the TPXO class to get elevations and velocities/transports
            tpxo_folder = cfg.boundaries.barotropic.TPXO.tpxo_folder
            sim.open_boundaries.z.set(
                tpxo.get(bdy_lon, bdy_lat, root=tpxo_folder),
                on_grid=True,
            )
            sim.open_boundaries.u.set(
                tpxo.get(bdy_lon, bdy_lat, variable="u", root=tpxo_folder),
                on_grid=True,
            )
            sim.open_boundaries.v.set(
                tpxo.get(bdy_lon, bdy_lat, variable="v", root=tpxo_folder),
                on_grid=True,
            )

        else:
            sim.logger.info(f"Getting 2D boundary data from {_source}")
            _cfg = getattr(cfg.boundaries.barotropic, _source)
            fn = _resolve_path(_cfg, cfg.setup.upper())

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
            #_woa_folder = cfg.boundaries.baroclinic.WOA.folder
            _woa_folder = cfg.hydrography.WOA.folder
            sim["temp"].open_boundaries.type = pygetm.SPONGE
            sim["temp"].open_boundaries.values.set(
                pygetm.input.from_nc(_woa_folder / "woa_t.nc", "t_an"),
                on_grid=False,
                climatology=True,
            )
            sim["salt"].open_boundaries.type = pygetm.SPONGE
            sim["salt"].open_boundaries.values.set(
                pygetm.input.from_nc(_woa_folder / "woa_s.nc", "s_an"),
                on_grid=False,
                climatology=True,
            )

        if cfg.boundaries.baroclinic.source == "CMEMS":
            sim.logger.info("setting up 3D CMEMS boundary conditions")
            _cfg = cfg.boundaries.baroclinic.CMEMS
            fn = _resolve_path(_cfg, cfg.setup.upper())
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
