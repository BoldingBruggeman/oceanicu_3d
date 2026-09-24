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

    FABM biogeochemistry (2026-09-23): the real EMORID file has NO3/NH4/
    PO4/Si/TALK/DIC as raw LOADS (confirmed directly against its own units
    attrs -- tN/d, tP/d, tSi/d, t/d), not concentrations. An older
    reference implementation (~/cfg_rivers.py, predates this repo)
    expected an already-unit-converted "EMORID_1990_2024_concentrations.nc"
    to exist -- it doesn't on this machine, so conversion happens here
    instead, at read time, using each river's own historical Q (load ->
    g/s -> g/m3 via /Q -> mol/m3 via /molar_mass -> mmol/m3 via *1000).
    Only N3_n/N4_n/N1_p/N5_s (nitrate/ammonium/phosphate/silicate) are set
    -- mirrors that same reference's own vars2process list, which left
    O3_TA/O3_c (alkalinity/DIC) commented out: their real EMORID units
    convention was already flagged there as an unconfirmed guess
    (molar_mass=1.0, "assuming..."), and both get real boundary values
    from CMIP6 delta-change instead (see boundaries.fabm.CMIP6), so
    leaving river-sourced alkalinity/DIC unset is low-stakes.

    No real future-projected nutrient product exists: the CMIP6 river-
    projection file (river_flows_future_{scenario}.nc) has ONLY Q/Q_mean/
    regulated_flag (confirmed directly), no nutrient loads at all. So
    nutrients are ALWAYS read from the real historical EMORID record
    regardless of river_discharge.source, and simply never updated past
    that record's own real coverage -- pygetm's TemporalInterpolation
    holds the last value beyond it, the same "best we can do" precedent
    already used for CMIP6 biogeochemical boundaries this session (no
    reliable CMIP6-projected river nutrient forcing is achievable either).

    river_discharge.source == "CMIP6" specifically: spliced across the
    same 2014/2015 historical/scenario boundary meteo.py's set_meteo_data
    and the codegen-baked-in open-boundary literals already use --
    river_flows_future_{scenario}.nc (a delta-change projection off the
    2015-2100 ssp period) only starts 2015-01-31, so a run/chunk whose
    own period reaches back before that needs the real EMORID
    observational record for the historical portion too, instead of (as
    before) letting pygetm's TemporalInterpolation hard-fail with "time
    series starts only at 2015-01-31" (a real gap, found running
    running/check_inputs.py's --extended-dry-run against
    NSe/CMIP6_raw/run01, 2026-09-19). ${RIVER_FOLDER}/RIVER_FILE are the
    SEPARATE machine-configured env var/name for that file (see
    machines.yaml) -- independent of ${RIVER_FOLDER_CMIP6}; RIVER_FILE
    differs by archive vintage (e.g. scylla's real file isn't
    bb-server1/orca's 'EMORID_1990_2024.nc'), so it's read from the
    environment directly here rather than hardcoded, defaulting to
    'EMORID_1990_2024.nc' only if genuinely unset. Verified directly
    (2026-09-19) that the historical file and the CMIP6 projection file
    share the identical 446-station site_name roster/order, so a
    per-file by-name lookup (same as below) lines up correctly -- still
    done independently per file rather than assumed, same caution as
    the "site_name" vs "name" fallback already applies.

    That splice only helps chunks starting in/before 2014, though -- the
    HIST_CUTOFF_YEAR check below is on the CHUNK's own start year, so any
    chunk starting in 2015 (the whole scenario period's own first chunk,
    typically 2015-01-01) skips the historical splice entirely and reads
    ONLY the future file, whose first real record doesn't land until
    2015-01-31 -- a genuine ~30-day pre-first-record gap TemporalInterpolation
    can't extrapolate across (found running running/check_inputs.py's
    --extended-dry-run against all 12 NSe/CMIP6 model x scenario x fabm
    combos, 2026-09-24, on every single one regardless of model).
    river_discharge.CMIP6.daily=true (see oceanicu_providers.py) is the
    real fix: ocean-prep's river-projection --disaggregate now also writes
    a day-of-year-shaped daily sibling whose own first record is exactly
    2015-01-01, closing this gap at the source rather than patching the
    splice boundary. Leave `daily` unset/false for configs still reading
    the plain monthly file (its own first-record timestamp needs a one-off
    data fix instead -- see running/fix_monthly_river_time.py).
    """
    import contextlib
    import datetime
    import os
    import xarray as xr

    rcfg = config["river_discharge"]
    # folder_template composition -- see add_rivers' own comment above for why.
    folder = Path(resolve_data_path(rcfg["folder"]))
    if rcfg.get("folder_template"):
        folder = folder / rcfg["folder_template"].format(
            model=rcfg.get("model", ""), scenario=rcfg.get("scenario", "")
        )
    filename = rcfg["file"].format(model=rcfg.get("model", ""), scenario=rcfg.get("scenario", ""))
    # river_discharge.CMIP6.daily -- see add_rivers' own comment above for why.
    if rcfg.get("daily"):
        filename = filename.removesuffix(".nc") + "_daily.nc"
    # Inlined, not a shared helper -- see add_rivers' own comment above for why.
    if config.get("runtime", {}).get("calendar") == "noleap":
        filename = filename.removesuffix(".nc") + "_noleap.nc"
    future_path = folder / filename

    HIST_CUTOFF_YEAR = 2014
    hist_path = None
    if rcfg.get("source") == "CMIP6":
        time = config.get("runtime", {}).get("time")
        if isinstance(time, str):
            time = datetime.datetime.fromisoformat(time)
        if time is not None and time.year <= HIST_CUTOFF_YEAR:
            hist_folder = Path(resolve_data_path("${RIVER_FOLDER}"))
            hist_filename = os.environ.get("RIVER_FILE", "EMORID_1990_2024.nc")
            if config.get("runtime", {}).get("calendar") == "noleap":
                hist_filename = hist_filename.removesuffix(".nc") + "_noleap.nc"
            hist_path = hist_folder / hist_filename

    # CFDatetimeCoder(use_cftime=True), matching cfg_rivers.py's own real
    # data() exactly -- needed for Q's time dimension, unlike add_rivers
    # above (which never reads a time-varying variable at all).
    time_coder = xr.coders.CFDatetimeCoder(use_cftime=True)
    n_set = 0
    # ExitStack, not a plain "with a, b:" -- hist_path is only sometimes
    # present, and river.flow.set() below (matching this function's own
    # pre-existing single-file pattern) must be called while every file
    # it draws from is still open.
    with contextlib.ExitStack() as stack:
        datasets = []
        if hist_path is not None:
            datasets.append(stack.enter_context(
                xr.open_dataset(hist_path, engine="netcdf4", decode_times=time_coder)
            ))
        datasets.append(stack.enter_context(
            xr.open_dataset(future_path, engine="netcdf4", decode_times=time_coder)
        ))

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
            if len(datasets) == 1:
                flow = datasets[0]["Q"].isel(site=indices[0])
            else:
                # Historical sliced up to (not through) the boundary --
                # its own real coverage runs well past 2015 too, and
                # concatenating both files' full, overlapping ranges
                # would break TemporalInterpolation's monotonic-time
                # assumption.
                hist_da = datasets[0]["Q"].isel(site=indices[0]).sel(
                    time=slice(None, f"{HIST_CUTOFF_YEAR}-12-31")
                )
                fut_da = datasets[1]["Q"].isel(site=indices[1])
                flow = xr.concat([hist_da, fut_da], dim="time")
            river.flow.set(flow)
            river["salt"].set(0.0)
            n_set += 1

    if getattr(sim, "fabm", None):
        # Always the real historical file, regardless of river_discharge.
        # source -- see this function's own docstring for why (no real
        # future-projected nutrient product exists at all).
        nutrient_folder = Path(resolve_data_path("${RIVER_FOLDER}"))
        nutrient_filename = os.environ.get("RIVER_FILE", "EMORID_1990_2024.nc")
        if config.get("runtime", {}).get("calendar") == "noleap":
            nutrient_filename = nutrient_filename.removesuffix(".nc") + "_noleap.nc"
        nutrient_path = nutrient_folder / nutrient_filename
        # tracer -> (real EMORID load variable, molar mass in g/mol) --
        # see this function's docstring for why O3_TA/O3_c are excluded.
        _emorid_nutrient_vars = {
            "N3_n": ("NO3", 14.0067),
            "N4_n": ("NH4", 14.0067),
            "N1_p": ("PO4", 30.9738),
            "N5_s": ("Si", 28.0855),
        }
        n_fabm_set = 0
        with xr.open_dataset(nutrient_path, engine="netcdf4", decode_times=time_coder) as nds:
            name_var = next((v for v in ("site_name", "name") if v in nds), None)
            site_names = nds[name_var].values if name_var else range(nds.sizes["site"])
            name_to_index = {str(n): i for i, n in enumerate(site_names)}
            for name, river in sim.rivers.items():
                idx = name_to_index.get(name)
                if idx is None:
                    continue
                q = nds["Q"].isel(site=idx)
                # Floor away from 0 before dividing -- a real river can hit
                # genuinely near-zero flow at low-water; holding a load
                # constant while dividing by a vanishing Q would otherwise
                # spike the resulting concentration arbitrarily high.
                q_safe = q.where(q > 1e-6, 1e-6)
                for tracer, (file_var, molar_mass) in _emorid_nutrient_vars.items():
                    if file_var not in nds.data_vars:
                        continue
                    load_t_per_day = nds[file_var].isel(site=idx)
                    # t/day -> g/s -> g/m3 (via /Q) -> mol/m3 (via /molar_mass) -> mmol/m3
                    conc_mmol_m3 = load_t_per_day * 1e6 / 86400.0 / q_safe / molar_mass * 1000.0
                    river[tracer].set(conc_mmol_m3)
                    n_fabm_set += 1
        sim.logger.info(f"Set FABM river nutrients (N3_n/N4_n/N1_p/N5_s) for {n_fabm_set} river/tracer pairs")

    return n_set
