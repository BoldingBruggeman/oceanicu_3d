"""River positions and discharge data (river_discharge.<source>.script and
data_script, see oceanicu_providers.py), as cfg_rivers.py's two steps. Python,
not a YAML list: the set of rivers depends on the threshold and the domain
footprint. Loaded by "path/to/file.py:name", never imported."""

from __future__ import annotations

from pathlib import Path

from pygetm_config.loader import resolve_data_path


def add_rivers(domain, config: dict):
    """As cfg_rivers.py's create(): add every river whose mean discharge
    exceeds `threshold` and that lies in the domain. Both sources ("emorid",
    "CMIP6") use the same EMORID stations; the CMIP6 projection file carries
    a Q_mean for the threshold. Reads the validated, choice-flattened
    river_discharge section; ${VAR} folders are expanded here."""
    import xarray as xr
    import pygetm

    rcfg = config["river_discharge"]
    # river_discharge has no core-schema folder_template handling, so compose
    # it here (e.g. RIVER_FOLDER_CMIP6 + "CMIP6/{model}/{scenario}/rivers").
    folder = Path(resolve_data_path(rcfg["folder"]))
    if rcfg.get("folder_template"):
        folder = folder / rcfg["folder_template"].format(
            model=rcfg.get("model", ""), scenario=rcfg.get("scenario", "")
        )
    # {model}/{scenario} in `file` (CMIP6 only) keep it in step with `scenario`.
    filename = rcfg["file"].format(model=rcfg.get("model", ""), scenario=rcfg.get("scenario", ""))
    # CMIP6 `daily`: the daily-disaggregated sibling (see set_river_data).
    if rcfg.get("daily"):
        filename = filename.removesuffix(".nc") + "_daily.nc"
    # noleap runs read the '_noleap' copy, for every source; after the
    # `daily` suffix, as on disk (..._daily_noleap.nc). Inlined, not a shared
    # helper: codegen embeds one function at a time.
    if config.get("runtime", {}).get("calendar") == "noleap":
        filename = filename.removesuffix(".nc") + "_noleap.nc"
    path = folder / filename
    threshold = rcfg.get("threshold", 0)

    with xr.open_dataset(path) as ds:
        # "qmean" heuristic normalizes away underscores/case -- the real EMORID
        # file (verified against /data/EMORID/EMORID_1990_2024.nc) names this
        # "Q_mean", not "qmean".
        qmean_name = next(v for v in ds.data_vars if "qmean" in v.lower().replace("_", ""))
        valid = ds[qmean_name] > threshold
        lons = ds["lon"].values[valid.values]
        lats = ds["lat"].values[valid.values]
        # Real file uses "site_name", not "name" -- check both rather than
        # silently falling back to anonymous numeric indices.
        name_var = next((v for v in ("site_name", "name") if v in ds), None)
        names = ds[name_var].values[valid.values] if name_var else range(len(lons))
        n_added = 0
        for name, lon, lat in zip(names, lons, lats):
            if not domain.contains(lon, lat):
                continue
            domain.rivers.add_by_location(str(name), lon, lat, coordinate_type=pygetm.CoordinateType.LONLAT)
            n_added += 1
    return n_added


def set_river_data(sim, domain, config: dict) -> int:
    """As cfg_rivers.py's data(): discharge, salt (0) and FABM nutrients for
    the rivers in this subdomain (sim.rivers). Runs after data_assignments,
    via river_discharge.data_script.

    The EMORID record ${RIVER_FOLDER}/$RIVER_FILE (default
    EMORID_1993_2024_conc_nemo_TALK_DIC.nc, by archive vintage per machine)
    holds flow and NO3/NH4/PO4/Si/TALK/DIC as concentrations (mmol/m3; TALK/
    DIC from NEMO NOWMAPS, 2019-2024 filled with the 2018 mean).

    Nutrients are never projected. "emorid": after the record ends pygetm
    holds the last value. "CMIP6": they follow a monthly climatology of the
    record (EMORID_nutrient_climatology_monthly_{5yr,10yr,full}.nc, site x 12
    months), each future day taking its calendar month's value.

    Flow for "CMIP6": the record up to its last day (2024-12-31, read from
    the file), then the projection (river_flows_future_{scenario}[_daily].nc)
    from the day after. The projection starts 2015, so its overlapping years
    are sliced away explicitly: pygetm.input.Concatenate is index-based and
    would break the monotonic time axis.

    The _noleap record drops every real Feb 29 (rather than relabelling), so
    a noleap run splices without gaps. Stations are looked up by name in each
    file separately.
    """
    import contextlib
    import datetime
    import os
    import xarray as xr

    rcfg = config["river_discharge"]
    # CFDatetimeCoder(use_cftime=True), matching cfg_rivers.py's own real
    # data() exactly -- needed for Q's time dimension, unlike add_rivers
    # above (which never reads a time-varying variable at all).
    time_coder = xr.coders.CFDatetimeCoder(use_cftime=True)

    # The EMORID record is always read: flow for "emorid" (and CMIP6 before
    # the splice), and the nutrients for both.
    hist_folder = Path(resolve_data_path("${RIVER_FOLDER}"))
    hist_filename = os.environ.get("RIVER_FILE", "EMORID_1993_2024_conc_nemo_TALK_DIC.nc")
    if config.get("runtime", {}).get("calendar") == "noleap":
        hist_filename = hist_filename.removesuffix(".nc") + "_noleap.nc"
    hist_path = hist_folder / hist_filename

    use_cmip6 = rcfg.get("source") == "CMIP6"
    future_path = None
    if use_cmip6:
        # The projected file (folder_template as in add_rivers).
        folder = Path(resolve_data_path(rcfg["folder"]))
        if rcfg.get("folder_template"):
            folder = folder / rcfg["folder_template"].format(
                model=rcfg.get("model", ""), scenario=rcfg.get("scenario", "")
            )
        filename = rcfg["file"].format(model=rcfg.get("model", ""), scenario=rcfg.get("scenario", ""))
        # river_discharge.CMIP6.daily -- see add_rivers' own comment above for why.
        if rcfg.get("daily"):
            filename = filename.removesuffix(".nc") + "_daily.nc"
        if config.get("runtime", {}).get("calendar") == "noleap":
            filename = filename.removesuffix(".nc") + "_noleap.nc"
        future_path = folder / filename

    n_set = 0
    with contextlib.ExitStack() as stack:
        hist_ds = stack.enter_context(
            xr.open_dataset(hist_path, engine="netcdf4", decode_times=time_coder)
        )
        datasets = [hist_ds]
        if use_cmip6:
            datasets.append(stack.enter_context(
                xr.open_dataset(future_path, engine="netcdf4", decode_times=time_coder)
            ))

        # Splice at the record's own last day.
        hist_cutoff = hist_ds["time"].values[-1]

        # Nutrient climatology (CMIP6 only): in ${RIVER_FOLDER} next to the
        # record it is derived from; its month axis needs no noleap copy.
        clim_ds = None
        clim_name_to_index: dict = {}
        fut_time = None
        fut_months = None
        if use_cmip6:
            clim_period = rcfg.get("nutrient_climatology", "5yr")
            clim_path = hist_folder / f"EMORID_nutrient_climatology_monthly_{clim_period}.nc"
            clim_ds = stack.enter_context(
                xr.open_dataset(clim_path, engine="netcdf4")
            )
            clim_name_var = next((v for v in ("site_name", "name") if v in clim_ds), None)
            clim_site_names = (
                clim_ds[clim_name_var].values if clim_name_var else range(clim_ds.sizes["site"])
            )
            clim_name_to_index = {str(n): i for i, n in enumerate(clim_site_names)}
            # The future flow's own time axis, so nutrients follow its dates.
            fut_time = datasets[1]["time"].sel(
                time=slice(hist_cutoff + datetime.timedelta(days=1), None)
            )
            fut_months = fut_time.dt.month.values

        # "site_name" vs "name", as in add_rivers.
        name_to_index_per_ds = []
        for ds in datasets:
            name_var = next((v for v in ("site_name", "name") if v in ds), None)
            site_names = ds[name_var].values if name_var else range(ds.sizes["site"])
            name_to_index_per_ds.append({str(n): i for i, n in enumerate(site_names)})

        for name, river in sim.rivers.items():
            indices = [nti.get(name) for nti in name_to_index_per_ds]
            if any(i is None for i in indices):
                continue
            if not use_cmip6:
                flow = datasets[0]["Q"].isel(site=indices[0])
            else:
                # EMORID up to its last day, then the projection from the day
                # after; the projection's overlapping years are sliced away.
                hist_da = datasets[0]["Q"].isel(site=indices[0]).sel(time=slice(None, hist_cutoff))
                fut_da = datasets[1]["Q"].isel(site=indices[1]).sel(
                    time=slice(hist_cutoff + datetime.timedelta(days=1), None)
                )
                flow = xr.concat([hist_da, fut_da], dim="time")
            river.flow.set(flow)
            river["salt"].set(0.0)
            n_set += 1

        if getattr(sim, "fabm", None):
            # tracer -> variable in the record (already concentrations).
            _emorid_nutrient_vars = {
                "N3_n": "NO3",
                "N4_n": "NH4",
                "N1_p": "PO4",
                "N5_s": "Si",
                "O3_TA": "TALK",
                "O3_c": "DIC",
            }
            name_to_index = name_to_index_per_ds[0]
            n_fabm_set = 0
            for name, river in sim.rivers.items():
                idx = name_to_index.get(name)
                if idx is None:
                    continue
                set_here = []
                clim_idx = clim_name_to_index.get(name) if use_cmip6 else None
                for tracer, file_var in _emorid_nutrient_vars.items():
                    if file_var not in hist_ds.data_vars:
                        continue
                    hist_nutrient_da = hist_ds[file_var].isel(site=idx).sel(
                        time=slice(None, hist_cutoff)
                    )
                    if clim_idx is not None and file_var in clim_ds.data_vars:
                        # Future: each day's calendar month from the climatology.
                        clim_vals = clim_ds[file_var].isel(site=clim_idx).values
                        fut_vals = clim_vals[fut_months - 1]
                        fut_nutrient_da = xr.DataArray(
                            fut_vals, coords={"time": fut_time}, dims="time"
                        )
                        nutrient_da = xr.concat(
                            [hist_nutrient_da, fut_nutrient_da], dim="time"
                        )
                    else:
                        nutrient_da = hist_nutrient_da
                    river[tracer].set(nutrient_da)
                    set_here.append(tracer)
                    n_fabm_set += 1
                if set_here:
                    sim.logger.info(f"Added FABM BGC river data {set_here} for river {river.name}")
            sim.logger.info(f"Set FABM river nutrients for {n_fabm_set} river/tracer pairs")

    return n_set
