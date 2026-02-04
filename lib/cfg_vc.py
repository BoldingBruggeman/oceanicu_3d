"""Configuring vertical coordinates

To simplify the run script the pressure gradient is created here.

SIGMA and GVC are fully configured using the cfg dictionary. AVC is
configured hard coded values in this file.

A vertical_coordinate object is return and used in the configuration
of the simulation object.

"""

import sys
import enum

import pygetm


class VerticalCoordinates(enum.IntEnum):
    SIGMA = 1
    GVC = 2
    ADAPTIVE = 3


def create(
    cfg,
) -> pygetm.vertical_coordinates:
    vertical_coordinates = None
    if cfg.simulation.runtype > pygetm.RunType.BAROTROPIC_2D:
        nz = cfg.vertical_coordinates.nz
        ddu = cfg.vertical_coordinates.ddu
        ddl = cfg.vertical_coordinates.ddl
        Dgamma = cfg.vertical_coordinates.Dgamma
        gamma_surf = cfg.vertical_coordinates.gamma_surf

        if cfg.vertical_coordinates.type == VerticalCoordinates.SIGMA:
            vertical_coordinates = pygetm.vertical_coordinates.Sigma(
                nz, ddl=ddl, ddu=ddu
            )
        if cfg.vertical_coordinates.type == VerticalCoordinates.GVC:
            vertical_coordinates = pygetm.vertical_coordinates.GVC(
                nz, ddl=ddl, ddu=ddu, Dgamma=Dgamma, gamma_surf=gamma_surf
            )
        if cfg.vertical_coordinates.type == VerticalCoordinates.ADAPTIVE:
            try:
                vertical_coordinates = pygetm.vertical_coordinates.Adaptive(
                    nz,
                    cfg.runtime.timestep,
                    cnpar=1.0,
                    ddu=ddu,
                    ddl=ddl,
                    gamma_surf=gamma_surf,
                    Dgamma=Dgamma,
                    csigma=0.001,
                    cgvc=-0.001,
                    hpow=3,
                    chsurf=0.05,
                    hsurf=0.5,
                    chmidd=-0.1,
                    hmidd=0.5,
                    chbott=0.01,
                    hbott=0.5,
                    cneigh=0.001,
                    rneigh=0.25,
                    decay=2.0 / 3.0,
                    cNN=0.05,
                    drho=0.5,
                    cSS=-1.0,
                    dvel=0.1,
                    chmin=0.1,
                    hmin=0.5,
                    nvfilter=1,
                    vfilter=0.1,
                    nhfilter=1,
                    hfilter=0.2,
                    split=1,
                    timescale=3.0 * 3600.0,
                )
            except:
                print("Error: can not initialize Adaptive-coordinates")
                sys.exit(1)

    return vertical_coordinates
