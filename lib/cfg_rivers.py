"""Configuring rivers

To simplify the run script river specifications are done here.

Rivers can come from different sources - presently EMORID.

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
            # for n in ds[_source[name]].values:
            for n in ds[_source["index"]].values:
                if river.name == ds[_source["name"]][n].values:
                    river.flow.set(ds[_source[Q_name]][n])
                    river["salt"].set(0.0)
                    break
