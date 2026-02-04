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
    Return the latitude, longitude, and discharge variable names for a
    given configuration. Raises KeyError if the configuration is unknown.
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

        # print(domain.contains(10, 55))
        # print(domain.contains.get_boundary())
        # quit()

        # Limit the available rivers to those inside the model domain
        # Temporary solution
        min_lon = -180.0
        max_lon = 180.0
        min_lat = -90.0
        max_lat = 90.0
        if cfg.setup == "ns":
            min_lon = -5.125
            max_lon = 13.375
            min_lat = 48.39167
            max_lat = 60.79167
        if cfg.setup == "ena4":
            min_lon = -20
            max_lon = 15.5
            min_lat = 40
            max_lat = 70
        if cfg.setup == "ena8":
            min_lon = -20
            max_lon = 15.5
            min_lat = 40
            max_lat = 70

        lon_da = ds[lon_name]
        lat_da = ds[lat_name]
        valid_lon = (lon_da >= min_lon) & (lon_da <= max_lon)
        valid_lat = (lat_da >= min_lat) & (lat_da <= max_lat)
        valid_combined = valid_lon & valid_lat

        # Limit available rivers by threshold
        if cfg.rivers.threshold > 0.0:
            mean_q = ds[Qmean_name]
            valid_mean = mean_q > cfg.rivers.threshold
            valid_combined = valid_combined & valid_mean

        river_list = []
        # for n in ds["HydroID"]:
        # Loop over all rivers in the river source file
        for n in ds[index].values:
            # filter out rivers
            if valid_combined[n]:
                river_name = f"{ds[name].values[n]}"
                river_list.append(
                    domain.rivers.add_by_location(
                        river_name,
                        lon_da.values[n],
                        lat_da.values[n],
                        coordinate_type=pygetm.CoordinateType.LONLAT,
                    )
                )


def data(sim, cfg):
    if cfg.rivers.source:
        time_coder = xr.coders.CFDatetimeCoder(use_cftime=True)
        ds = xr.open_dataset(
            cfg.rivers.folder / cfg.rivers.file,
            engine="netcdf4",
            decode_times=time_coder,
        )
        # Loop over included files and attached river data
        _source = river_config[cfg.rivers.source]
        for river in sim.rivers.values():
            # get the global index - should be replaced by adding an index attribute
            for n in ds[_source["index"]].values:
                if river.name == ds[_source["name"]][n].values:
                    river.flow.set(ds[_source["Q"]][n])
                    break
