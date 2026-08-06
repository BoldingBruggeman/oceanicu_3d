"""EMORID river positioning + discharge data attachment (nse_from_oceanicu.yaml's
river_discharge.emorid.script/data_script -- see oceanicu_providers.py). Mirrors
cfg_rivers.py's own real two-step split ("1) Set name and position of rivers...
2) Attach river data to the Simulation object"), verified against that source.

Loaded via pygetm_config.providers.load_dotted_target ("path/to/file.py:name"),
never imported directly -- see nse_driver.py's own module docstring for how
that's wired (river_discharge.emorid.script/data_script defaults, both pointing
here) and pygetm_config's docs/yaml_vs_python.md for why this stays real Python
rather than a static YAML list at all (the *set* of rivers is threshold-filtered
and domain-footprint-dependent at run time, not fixed).
"""

from __future__ import annotations

from pathlib import Path


def add_rivers(domain, config: dict):
    """Mirrors cfg_rivers.py's create() -- dynamic, threshold-filtered, read from
    an EMORID/JRC discharge file at run time. The *set* of rivers depends on the
    threshold and the domain's exact footprint, so (per pygetm-config's
    docs/yaml_vs_python.md) this cannot become a static YAML list without losing
    that behavior; it has to stay a loop over real data, same as in OceanICU
    today.

    Reads the validated `river_discharge:` section (a `nested_by_label`
    ChoiceSpec -- see oceanicu_providers.py), not a free-form dict: whichever
    source is active (only "emorid" is registered today) has its fields
    flattened onto `config["river_discharge"]` directly by validate_config,
    regardless of whether the YAML wrote them nested under `emorid:` or flat.
    """
    import xarray as xr
    import pygetm

    rcfg = config["river_discharge"]
    path = Path(rcfg["folder"]) / rcfg["file"]
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
    """Mirrors cfg_rivers.py's own data() -- the second half of
    river_discharge's job (user request): add_rivers (above) only sets
    POSITION; this attaches the REAL, time-varying discharge to each river
    actually present in this subdomain (sim.rivers -- a pygetm.rivers.
    LocalRiverCollection, keyed by name, only rivers that fall within THIS
    subdomain -- not necessarily every one add_rivers positioned on the
    global domain). Needs a live sim.rivers (only exists once the
    Simulation object is built), so this runs via
    river_discharge.data_script (see pygetm_config.loader.
    run_river_discharge_data_script), a separate hook from
    river_discharge.script's own add_rivers (which runs before sim exists).

    Salt is set to 0.0 (matching cfg_rivers.py's own
    river["salt"].set(0.0) -- river water is fresh). FABM biogeochemistry
    (NO3/NH4/PO4/Si/TALK/DIC, present in the real EMORID file) is NOT wired
    up here -- this repo has no FABM model configured yet; cfg_rivers.py's
    own data() only does this when sim.fabm is truthy, so it's a real,
    deliberate scope limit, not an oversight.
    """
    import xarray as xr

    rcfg = config["river_discharge"]
    path = Path(rcfg["folder"]) / rcfg["file"]
    # CFDatetimeCoder(use_cftime=True), matching cfg_rivers.py's own real
    # data() exactly -- needed for Q's time dimension, unlike add_rivers
    # above (which never reads a time-varying variable at all).
    time_coder = xr.coders.CFDatetimeCoder(use_cftime=True)
    n_set = 0
    with xr.open_dataset(path, engine="netcdf4", decode_times=time_coder) as ds:
        # Same "site_name" vs "name" fallback as add_rivers above -- both
        # read the same file, so if one needs it the other might too.
        name_var = next((v for v in ("site_name", "name") if v in ds), None)
        site_names = ds[name_var].values if name_var else range(ds.sizes["site"])
        name_to_index = {str(n): i for i, n in enumerate(site_names)}
        for name, river in sim.rivers.items():
            idx = name_to_index.get(name)
            if idx is None:
                continue
            river.flow.set(ds["Q"].isel(site=idx))
            river["salt"].set(0.0)
            n_set += 1
    return n_set
