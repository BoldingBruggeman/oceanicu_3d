"""EMORID river positioning + discharge data attachment (a domain config's
river_discharge.emorid.script/data_script -- see oceanicu_providers.py).
Mirrors cfg_rivers.py's own two-step split ("1) Set name and position of
rivers... 2) Attach river data to the Simulation object").

Loaded via pygetm_config.providers.load_dotted_target ("path/to/file.py:name"),
never imported directly -- see oceanicu_driver.py's own module docstring for
how that's wired (river_discharge.emorid.script/data_script defaults, both
pointing here) and pygetm_config's docs/yaml_vs_python.md for why this stays
real Python rather than a static YAML list at all (the *set* of rivers is
threshold-filtered and domain-footprint-dependent at run time, not fixed).
"""

from __future__ import annotations

from pathlib import Path

from pygetm_config.loader import resolve_data_path


def add_rivers(domain, config: dict):
    """Mirrors cfg_rivers.py's create() -- dynamic, threshold-filtered, read from
    an EMORID/JRC discharge file at run time. The *set* of rivers depends on the
    threshold and the domain's exact footprint, so (per pygetm-config's
    docs/yaml_vs_python.md) this cannot become a static YAML list without losing
    that behavior; it has to stay a loop over real data, same as in OceanICU
    today.

    Reads the validated `river_discharge:` section (a `nested_by_label`
    ChoiceSpec -- see oceanicu_providers.py), not a free-form dict: whichever
    source is active ("emorid" or "CMIP6") has its fields flattened onto
    `config["river_discharge"]` directly by validate_config, regardless of
    whether the YAML wrote them nested under `emorid:`/`CMIP6:` or flat. Both
    sources use this SAME function unchanged -- CMIP6's delta-change river
    projection (stats/cli/river_projection.py's river_flows_future_
    {scenario}.nc) is keyed by the same EMORID stations, just with a Q_mean
    variable added so the threshold lookup below finds a match; only
    folder/folder_template/file (see `file`'s own {model}/{scenario}
    templating below) differ between the two sources.

    `folder` may be a "${VAR}"/"$VAR" reference (pygetm-config's own lazy
    data-path resolution mechanism) -- resolve_data_path expands it here,
    at actual use time, exactly like pygetm-config's own generic kind="path"/
    kind="file" handling does for core schema fields. This function isn't
    core pygetm-config code (it's a project-specific script hook, loaded via
    load_dotted_target), so it has to opt into that resolution explicitly --
    unlike domain.path/tpxo_folder/data_assignments file, which get it for
    free via loader._coerce_value's own "path" TypeKind branch.
    """
    import xarray as xr
    import pygetm

    rcfg = config["river_discharge"]
    # folder_template (e.g. "CMIP6/{model}/{scenario}/rivers") must be
    # applied here explicitly, same as every other CMIP6 provider does
    # (oceanicu_providers.py's boundaries.baroclinic/barotropic/fabm
    # branches all do `folder / folder_template.format(model=..., scenario=...)`)
    # -- river_discharge has no core-schema equivalent doing this for it
    # automatically (unlike open_boundaries' file: kind, which gets
    # ${VAR} resolution for free but NOT folder_template composition).
    # Missing this meant `folder` alone (e.g. scylla's bare
    # RIVER_FOLDER_CMIP6=/work/shared/oceanICU/BiasCorrected, deliberately
    # NOT including CMIP6/<model>/<scenario>/rivers -- see
    # driver/scylla_data_roots.yaml) resolved to a file that never
    # existed (FileNotFoundError, hit on the HPC 2026-09-15).
    folder = Path(resolve_data_path(rcfg["folder"]))
    if rcfg.get("folder_template"):
        folder = folder / rcfg["folder_template"].format(
            model=rcfg.get("model", ""), scenario=rcfg.get("scenario", "")
        )
    # .format() is a no-op for "emorid"'s literal filename (no {} in it) --
    # only source=CMIP6 actually has {model}/{scenario} placeholders here,
    # same substitution folder_template already does elsewhere (meteo.py's
    # own folder_template.format(model=..., scenario=...)) -- avoids the
    # filename and `scenario:` field ever disagreeing with each other.
    filename = rcfg["file"].format(model=rcfg.get("model", ""), scenario=rcfg.get("scenario", ""))
    # river_discharge.CMIP6.daily (2026-09-24): switches to the
    # day-of-year-disaggregated sibling file, whose own real coverage
    # starts exactly 2015-01-01 -- see set_river_data's docstring below for
    # why the plain monthly file can't cover that date. "emorid" has no
    # `daily` field at all (rcfg.get returns None/falsy), so this is a
    # no-op for that source, same as `folder_template` above.
    if rcfg.get("daily"):
        filename = filename.removesuffix(".nc") + "_daily.nc"
    # Inlined rather than a shared helper: pygetm_config.codegen's
    # _emit_script_hook embeds ONE function's source per script/data_script
    # reference (inspect.getsource on just that function, no dependency
    # scan of sibling module-level helpers -- see its own docstring on
    # local imports for the same limitation) -- a separate
    # _apply_calendar_suffix() here left the generated standalone script
    # calling an undefined name (NameError, hit on the HPC 2026-09-15).
    # Same mechanism as oceanicu_providers.derive_data_assignments' own
    # `_calendar_suffix` for boundaries; applied here directly since
    # river_discharge's `file:` is read by THIS script hook, not resolved
    # inside derive_data_assignments (per user, 2026-09-15: "do it the way
    # it was done for the boundaries"). Applies to EVERY river source
    # equally, not just CMIP6 -- "emorid"'s own real-observation file needs
    # the same treatment for a noleap-calendar run. Comes AFTER the `daily`
    # suffix above -- matches the real on-disk naming (river_flows_future_
    # {scenario}_daily_noleap.nc, confirmed directly against GFDL-ESM4's
    # noleap files).
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
    """Mirrors cfg_rivers.py's own data() -- the second half of
    river_discharge's job: add_rivers (above) only sets POSITION; this
    attaches the REAL, time-varying discharge to each river
    actually present in this subdomain (sim.rivers -- a pygetm.rivers.
    LocalRiverCollection, keyed by name, only rivers that fall within THIS
    subdomain -- not necessarily every one add_rivers positioned on the
    global domain). Needs a live sim.rivers (only exists once the
    Simulation object is built), so this runs via
    river_discharge.data_script (see pygetm_config.loader.
    run_river_discharge_data_script), a separate hook from
    river_discharge.script's own add_rivers (which runs before sim exists).

    Salt is set to 0.0 (matching cfg_rivers.py's own
    river["salt"].set(0.0) -- river water is fresh).

    FABM biogeochemistry (2026-10-01): reads NO3/NH4/PO4/Si/TALK/DIC
    directly as CONCENTRATIONS (mmol/m3) from Ricardo's
    EMORID_1993_2024_conc_nemo_TALK_DIC.nc -- no unit conversion needed,
    unlike the older EMORID_1990_2024.nc this replaces (which stored raw
    LOADS in t/day, confirmed directly against its own units attrs, and
    needed a load->concentration conversion at read time using each
    river's own Q). TALK/DIC are genuine, documented concentrations here
    (source: NEMO NOWMAPS, 1993-2018 real, 2019-2024 filled with the 2018
    annual mean, per the file's own long_name attrs) -- so O3_TA/O3_c
    (alkalinity/DIC) are now set too, unlike before: the OLD file's units
    convention for those two was an unconfirmed guess (molar_mass=1.0,
    "assuming...", in the pre-repo reference implementation this descends
    from), which is why they were excluded previously.

    No real future-projected nutrient product exists: the CMIP6 river-
    projection file (river_flows_future_{scenario}[_daily].nc) has ONLY
    Q/Q_mean/regulated_flag (confirmed directly), no nutrient data at all
    -- and per user, 2026-10-01, this is deliberate: nutrients are NEVER
    projected into the future, only flow is. So nutrients are ALWAYS read
    from this same real historical record regardless of
    river_discharge.source, and simply never updated past that record's
    own real coverage -- pygetm's TemporalInterpolation holds the last
    value beyond it, the same "best we can do" precedent already used for
    CMIP6 biogeochemical boundaries.

    Flow splice (river_discharge.source == "CMIP6" only): real EMORID flow
    up to its own real LAST DAY, then the CMIP6-projected daily file
    (river_discharge.CMIP6.daily=true) from the day after -- same
    historical+future splice PATTERN meteo.py/the open-boundary scripts
    already use, but at a DIFFERENT cutover: EMORID's own real Q coverage
    now runs to 2024-12-31 (confirmed directly, 100% finite throughout,
    including its own final year) -- a decade past the generic CMIP6
    2014/2015 historical/scenario protocol boundary those other scripts
    splice at. The cutover is discovered from the historical file's own
    real last time value, not hardcoded (an earlier version of this
    function used a fixed HIST_CUTOFF_YEAR=2014, which predates this file
    and is wrong by a decade for it -- it also only ever opened the
    historical file for a chunk STARTING in/before the cutoff year, a
    purely historical optimization that's gone now that the historical
    file is always needed anyway, for nutrients). `river_flows_future_
    {scenario}_daily.nc`'s own real coverage starts 2015-01-01 -- a REAL
    ~10-year overlap with EMORID's now-2024 coverage, unlike the clean
    2014/2015 handoff meteo/boundaries have; the future file's own
    pre-cutover years are explicitly sliced away below rather than relying
    on pygetm.input.Concatenate to do it (it's a dumb, index-based
    concatenation with no date-awareness -- confirmed directly against its
    own __getitem__ -- so feeding it two time-OVERLAPPING sources would
    silently break TemporalInterpolation's monotonic-time assumption at
    the seam).

    Known gap, not yet addressed: no "_noleap" sibling of
    EMORID_1993_2024_conc_nemo_TALK_DIC.nc exists yet (unlike the older
    EMORID_1990_2024_noleap.nc) -- a run with runtime.calendar: noleap
    (only ever GFDL-ESM4 CMIP6-raw today) will hit a loud FileNotFoundError
    here rather than a silent wrong answer, which is the right failure mode
    until that sibling exists, but it does mean this doesn't yet work for
    that one combination.

    ${RIVER_FOLDER}/RIVER_FILE are the SEPARATE machine-configured env
    var/name for the real historical file (see machines.yaml) --
    independent of river_discharge's own folder/folder_template/file
    (which for source=CMIP6 locate the PROJECTED future file only, below);
    RIVER_FILE differs by archive vintage (e.g. scylla's real file isn't
    necessarily bb-server1/orca's), so it's read from the environment
    directly rather than hardcoded, defaulting to
    'EMORID_1993_2024_conc_nemo_TALK_DIC.nc' only if genuinely unset.
    Verified directly (2026-09-19) that the historical file and the CMIP6
    projection file share the identical 446-station site_name roster/
    order, so a per-file by-name lookup (same as below) lines up
    correctly -- still done independently per file rather than assumed,
    same caution as the "site_name" vs "name" fallback already applies.
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

    # The real EMORID record (flow AND concentrations together now) is
    # the canonical source regardless of river_discharge.source: "emorid"
    # reads it directly for flow; "CMIP6" splices it with the projected
    # future file below. Nutrients always come from here -- see docstring.
    hist_folder = Path(resolve_data_path("${RIVER_FOLDER}"))
    hist_filename = os.environ.get("RIVER_FILE", "EMORID_1993_2024_conc_nemo_TALK_DIC.nc")
    if config.get("runtime", {}).get("calendar") == "noleap":
        hist_filename = hist_filename.removesuffix(".nc") + "_noleap.nc"
    hist_path = hist_folder / hist_filename

    use_cmip6 = rcfg.get("source") == "CMIP6"
    future_path = None
    if use_cmip6:
        # folder_template composition -- see add_rivers' own comment above
        # for why. Only resolved for CMIP6 -- the historical file above is
        # the "emorid" choice's own real source too now, so its schema
        # folder/file fields are unused here (still used by add_rivers'
        # own positioning step, a separate, earlier concern).
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

        # Real EMORID coverage's own last day -- see docstring for why
        # this replaces a hardcoded HIST_CUTOFF_YEAR.
        hist_cutoff = hist_ds["time"].values[-1]

        # Same "site_name" vs "name" fallback as add_rivers above -- both
        # read the same file(s), so if one needs it the other might too.
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
                # Real EMORID flow up to (and including) its own real last
                # day, then the CMIP6-projected daily file from the day
                # after -- see docstring for why the future file's own
                # pre-cutover years must be sliced away explicitly here.
                hist_da = datasets[0]["Q"].isel(site=indices[0]).sel(time=slice(None, hist_cutoff))
                fut_da = datasets[1]["Q"].isel(site=indices[1]).sel(
                    time=slice(hist_cutoff + datetime.timedelta(days=1), None)
                )
                flow = xr.concat([hist_da, fut_da], dim="time")
            river.flow.set(flow)
            river["salt"].set(0.0)
            n_set += 1

        if getattr(sim, "fabm", None):
            # tracer -> real variable name in the historical file --
            # already concentrations, see docstring.
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
                for tracer, file_var in _emorid_nutrient_vars.items():
                    if file_var not in hist_ds.data_vars:
                        continue
                    river[tracer].set(hist_ds[file_var].isel(site=idx))
                    set_here.append(tracer)
                    n_fabm_set += 1
                if set_here:
                    sim.logger.info(f"Added FABM BGC river data {set_here} for river {river.name}")
            sim.logger.info(f"Set FABM river nutrients for {n_fabm_set} river/tracer pairs")

    return n_set
