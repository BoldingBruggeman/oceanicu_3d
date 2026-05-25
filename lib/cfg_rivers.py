"""Configuring rivers

To simplify the run script river specifications are done here.

Rivers can come from different sources - presently EMORID.
The EMORID dataset provides river discharge and nutrient loads. The original file has been processed to include concentrations  instead of loads in mmol/m3, 
    and to include global meadian for each variable across all rivers for each time step. 
    The original file is saved as EMORID_1990_2024.nc, and the processed file with concentrations is saved as EMORID_1990_2024_concentrations.nc

The configuration falls in two steps:
1) Set name and  position of rivers in the Domain object.
2) Attach river data to the Simulation object.

"""

import xarray as xr

import pygetm

"""
A dictionary to provide a common interface to different river 
sources.
"""
river_config = {
    # EMORID default naming (used in most of the original files)
    "emorid": {
        "index": "site",  # integer index
        "name": "site_name",  # name
        "lat": "lat",  # latitude column / coordinate name
        "lon": "lon",  # longitude column / coordinate name
        "Q": "Q",  # discharge (flow) variable
        "Qmean": "Q_mean",  # discharge (flow) variable
        "N3_n": "NO3", # nitrate load in N/day
        "N4_n": "NH4", # ammonium load in N/day
        "N1_p": "PO4", # phosphate load in P/day
        "N5_s": "Si", # silicate load in Si/day
        "O3_TA": "TALK", # total alkalinity load in mol/day?
        "O3_c": "DIC", # dissolved inorganic carbon load in mol/day?
    },
    # TODO: fix the names for the JRC rivers
    # Example configuration “adolf” – uses slightly different names
    "jrc": {
        "lat": "Latitude",  # e.g. column called “Latitude”
        "lon": "Longitude",  # e.g. column called “Longitude”
        "Q": "discharge",  # discharge variable named “discharge”
        "Qmean": "QQQQan",  # discharge (flow) variable
    },
    # Add more configurations as needed …
    # "myconfig": {"lat": "...", "lon": "...", "discharge": "..."},
}


MOLAR_MASS = {
    "emorid": {"NO3": 14.0067,   # reported as N g/mol
    "NH4": 14.0067,   # reported as N g/mol
    "PO4": 30.9738,   # reported as P g/mol
    "Si": 28.0855,  # reported as Si    g/mol
    "TALK": 1.0, # reported as tmol/day? - assuming 1 mol of alkalinity corresponds to 1 mol of charge, so 1 g/mol
    "DIC": 1.0, # reported as tmol/day? - assuming 1 mol of DIC corresponds to 1 mol of carbon, so 1 g/mol
}, 
"jrc": {"NO3": 14.0067,   # reported as N g/mol
    "NH4": 14.0067,   # reported as N g/mol
    "PO4": 30.9738,   # reported as P g/mol
    "Si": 28.0855,  # reported as Si g/mol
}
}
vars2process = ["N3_n", "N4_n", "N1_p", "N5_s"] # , "O3_TA", "O3_c"]

def load_to_concentration(
    config_name: str, load_t_day,
    discharge_m3s,
    tracer="NO3"
):
    """
    Convert river nutrient load [t/day]
    to concentration [mmol/m3] .

    Parameters
    ----------
    config_name: str
        Name of the river data source, e.g. "emorid"
    load_t_day : scalar, ndarray, DataArray
        Nutrient load [t/day]
    discharge_m3s : scalar, ndarray, DataArray
        River discharge [m3/s]
    tracer : str
        One of: 'NO3', 'NH4', 'PO4', 'Si/SiO4', 'TALK', 'DIC'

    Returns
    -------
    concentration : same type as input
        Concentration [mmol/m3]
    """

    M = MOLAR_MASS[config_name][tracer]
    tonn2gram = 1e6
    day2second = 86400
    mol2mmol = 1e3
    return (
        load_t_day * tonn2gram * mol2mmol
        / (M * discharge_m3s * day2second)
    )

def _get_river_cfg(config_name: str):
    """
    Return names to access specific variables - to support different
    river sources
    """
    river_cfg = river_config[config_name]  # will raise KeyError if not found
    return (
        river_cfg["index"],
        river_cfg["name"],
        river_cfg["lat"],
        river_cfg["lon"],
        river_cfg["Q"],
        river_cfg["Qmean"],
    )


def create(domain: pygetm.domain.Domain, cfg):
    if cfg.domain.rivers:
        index, name, lat_name, lon_name, Q_name, Qmean_name = _get_river_cfg(
            cfg.rivers.source
        )

        time_coder = xr.coders.CFDatetimeCoder(use_cftime=True)
        ds = xr.open_dataset(
            cfg.rivers.folder / cfg.rivers.file,
            engine="netcdf4",
            decode_times=time_coder,
        )

        # Limit available rivers by threshold
        valid_river = ds[Qmean_name] > cfg.rivers.threshold

        # Limit available rivers by domain area
        for n in ds[index].values:
            valid_river[n] = valid_river[n] & domain.contains(
                ds[lon_name].values[n], ds[lat_name].values[n]
            )
        print(f"Number of valid rivers: {valid_river.sum().values} out of {len(valid_river)}")
        river_list = []
        # for n in ds["HydroID"]:
        # Loop over all valid rivers and add them
        for n in ds[index].values:
            if valid_river[n]:
                river_list.append(
                    domain.rivers.add_by_location(
                        f"{ds[name].values[n]}",
                        # river_name,
                        ds[lon_name].values[n],
                        ds[lat_name].values[n],
                        coordinate_type=pygetm.CoordinateType.LONLAT,
                    )
                )


def data(sim, cfg):
    if cfg.domain.rivers and cfg.rivers.source:
        sim.logger.info(f"Attaching river data from source {cfg.rivers.source}")
        index, name, lat_name, lon_name, Q_name, Qmean_name = _get_river_cfg(
            cfg.rivers.source
        )
        time_coder = xr.coders.CFDatetimeCoder(use_cftime=True)
        ds = xr.open_dataset(
            cfg.rivers.folder / cfg.rivers.file,
            engine="netcdf4",
            decode_times=time_coder,
        )
        # TODO: - use non-hardcoded names for index and name
        # Loop over included files and attached river data
        _source = river_config[cfg.rivers.source]

        for river in sim.rivers.values():
            sim.logger.info(f"Processing river {river.name}")
            # for n in ds[_source[name]].values:
            for n in ds[_source["index"]].values:
                if river.name == ds[_source["name"]][n].values:
                    sim.logger.info(f"Added Q river data to for river {river.name}")

                    river.flow.set(ds[_source[Q_name]][n])
                    river["salt"].set(0.0)
                    if sim.fabm:
                        for varname in vars2process:
                            if varname in _source and _source[varname] in ds.data_vars:
                                    river[varname].set(ds[_source[varname]][n])
                        sim.logger.info(f"Added FABM BGC river data to {varname} for river {river.name}")

                    break
