"""Configuring internal pressure

To simplify the run script the pressure gradient is created here

"""

import enum
import pygetm


class InternalPressure(enum.IntEnum):
    BlumbergMellor = 1
    ShchepetkinMcwilliams = 2


def create(
    cfg,
) -> pygetm.internal_pressure:
    internal_pressure = None
    if cfg.simulation.runtype > pygetm.RunType.BAROTROPIC_3D:
        if cfg.internal_pressure.type == InternalPressure.BlumbergMellor:
            internal_pressure = pygetm.internal_pressure.BlumbergMellor()
        if cfg.internal_pressure.type == InternalPressure.ShchepetkinMcwilliams:
            internal_pressure = pygetm.internal_pressure.ShchepetkinMcwilliams()

    return internal_pressure
