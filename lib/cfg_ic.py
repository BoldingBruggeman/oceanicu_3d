"""Initial conditions for the physical model

When staring a model from scratch initial conditions are required.
Here data are read from WOA - will be extended with CMEMS data

"""

#

import pygetm


def create(sim, cfg, imonth):
    if cfg.simulation.runtype == pygetm.RunType.BAROCLINIC:
        sim.logger.info(
            f"Getting initial salinity from {cfg.initial_conditions.source}"
        )
        if cfg.initial_conditions.source == "constant":
            sim.temp.set(cfg.initial_conditions.temp)
            sim.salt.set(cfg.initial_conditions.salt)

        if cfg.initial_conditions.source == "WOA":
            sim.salt.set(
                pygetm.input.from_nc(
                    cfg.initial_conditions.folder / "woa_s.nc",
                    "s_an",
                ).isel(time=imonth),
                on_grid=False,
            )
            # sim.salt[...] = np.flip(sim.salt[...], axis=0)
            sim.logger.info("Getting initial temperature from WOA")
            sim.temp.set(
                pygetm.input.from_nc(
                    cfg.initial_conditions.folder / "woa_t.nc",
                    "t_an",
                ).isel(time=imonth),
                on_grid=False,
            )
            sim.density.convert_ts(sim.salt, sim.temp)
        # sim.temp[...] = np.flip(sim.temp[...], axis=0)
        sim.temp[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE
        sim.salt[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE
