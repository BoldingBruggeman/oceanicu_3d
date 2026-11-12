"""Initial conditions that need Python (hydrography.<source>.data_script, see
oceanicu_providers.py): the T/S monthly pick for WOA/CMEMS and the one-time
FABM tracer IC. Loaded by "path/to/file.py:name", never imported."""

from __future__ import annotations

from pathlib import Path

from pygetm_config.loader import resolve_data_path


def set_hydrography_ic(sim, domain, config: dict) -> None:
    """As cfg_ic.py's create(), for a fresh start only (the loader skips it
    when loading a restart) and BAROCLINIC runs.

    T/S (WOA/CMEMS): the month of runtime.time is picked from the monthly
    climatology (`.isel(time=imonth)` -- not data_assignments' climatology
    flag, which would cycle all year). Optional convert_ts converts to CT/SA.
    Land points are set to FILL_VALUE (the source files are global).

    FABM tracers: from fabm.ERSEM.restart_file (conservative vertical remap),
    else from boundaries.fabm's WOA/CMEMS files. `folder` values may be
    ${VAR} references, expanded with resolve_data_path.
    """
    import datetime

    import pygetm
    import pygetm.constants
    import pygetm.input

    if sim.runtype != pygetm.RunType.BAROCLINIC:
        return

    hydro = config["hydrography"]
    source = hydro.get("source")
    # "constant" hydrography skips only the T/S block; the FABM IC below
    # still applies.
    if source in ("WOA", "CMEMS"):
        # runtime.time comes from --start (driver or generated script); the
        # monthly pick can't proceed without it.
        time = config.get("runtime", {}).get("time")
        if time is None:
            raise RuntimeError(
                "hydrography's monthly-climatology initial condition needs a real start time, but "
                "runtime.time isn't set anywhere -- pass --start explicitly (either when generating "
                "this script, or when running it)."
            )
        if isinstance(time, str):
            time = datetime.datetime.fromisoformat(time)
        imonth = time.month - 1

        folder = Path(resolve_data_path(hydro["folder"]))
        if source == "WOA":
            salt_file, salt_var = folder / "woa_s.nc", "s_an"
            temp_file, temp_var = folder / "woa_t.nc", "t_an"
        else:  # CMEMS
            salt_file, salt_var = folder / "so_2025_monthly_ic.nc", "so_ff"
            temp_file, temp_var = folder / "thetao_2025_monthly_ic.nc", "thetao_ff"

        sim.salt.set(pygetm.input.from_nc(salt_file, salt_var).isel(time=imonth), on_grid=False)
        sim.temp.set(pygetm.input.from_nc(temp_file, temp_var).isel(time=imonth), on_grid=False)
        if hydro.get("convert_ts"):
            # WOA's t_an is in-situ temperature; CMEMS thetao is potential.
            sim.density.convert_ts(sim.salt, sim.temp, in_situ=(source == "WOA"))

        sim.temp[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE
        sim.salt[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE

    # --- FABM initial condition ---
    # Here, not in fabm.py's configure_fabm: this hook runs on a fresh start
    # only (skipped when loading a restart), configure_fabm on every chunk.
    #
    # fabm.ERSEM.restart_file: a pygetm restart-format file from ANOTHER run
    # (a "perpetual ERSEM" state; it may also hold physical fields, which are
    # ignored). Not read with sim.load_restart(): that copies layer k to layer
    # k, but this run's fresh-start layers differ from the saved run's (free
    # surface, adaptive coordinates). Instead every pelagic tracer is remapped
    # conservatively per wet column from the file's interfaces (zt, hnt) to
    # sim.T.zf. Only with FABM on.
    #
    # The helpers are nested so that codegen, which embeds this one
    # function's source, takes them along.
    def _seed_fabm_state_from_restart(sim, restart_path: str) -> None:
        """Seed every FABM state variable in *restart_path* onto this run's
        fresh-start grid: pelagic ones by conservative per-column vertical
        remap, benthic ones by horizontal copy. Variables missing from the
        file keep their fabm.yaml initial_value (with a warning)."""
        import numpy as np
        import xarray as xr

        def _conservative_remap_column(source_vals, source_ifaces, target_ifaces):
            """Per-column conservative vertical remap, vectorized across
            tracers (they share the same source/target interfaces within one
            column -- only the values differ).

            source_vals: (nz_src, n_tracers) per-layer means.
            source_ifaces: (nz_src+1,) DECREASING (surface first).
            target_ifaces: (nz_tgt+1,) DECREASING (surface first).
            Returns (nz_tgt, n_tracers).

            Interpolates the cumulative (depth-integrated) source profile at
            the target interfaces and divides each layer's content by the part
            of it the source column covers: exact for a piecewise-constant
            source. Outside the source's depth range the nearest source layer
            is extended (constant extrapolation), so a target column reaching
            higher or deeper than the source (e.g. a restart saved at another
            tidal elevation) keeps the surface/bottom values instead of
            getting zeros. Works on real depth, not layer index, so nz_src and
            nz_tgt need not match.
            """
            src_thick = (source_ifaces[:-1] - source_ifaces[1:])[:, None]
            cum_src = np.concatenate(
                [np.zeros((1, source_vals.shape[1])), np.cumsum(source_vals * src_thick, axis=0)],
                axis=0,
            )
            x_src = -source_ifaces  # increasing, required by searchsorted below
            x_tgt = np.clip(-target_ifaces, x_src[0], x_src[-1])
            idx = np.clip(np.searchsorted(x_src, x_tgt, side="right") - 1, 0, len(x_src) - 2)
            x0, x1 = x_src[idx], x_src[idx + 1]
            with np.errstate(invalid="ignore", divide="ignore"):
                frac = np.where(x1 > x0, (x_tgt - x0) / (x1 - x0), 0.0)
            cum_at_target = cum_src[idx] + frac[:, None] * (cum_src[idx + 1] - cum_src[idx])
            covered = (x_tgt[1:] - x_tgt[:-1])[:, None]
            with np.errstate(invalid="ignore", divide="ignore"):
                out = (cum_at_target[1:] - cum_at_target[:-1]) / covered
            # Layers entirely outside the source range: nearest source layer.
            above = (-target_ifaces[1:] <= x_src[0])
            below = (-target_ifaces[:-1] >= x_src[-1])
            out[above] = source_vals[0]
            out[below] = source_vals[-1]
            return out

        source_vals = None
        benthic_vals = {}
        n_benthic_nan = 0
        # The file holds the full global (yt, xt) domain; under MPI this rank
        # reads its own tile. subdomain2slices() (not subdomain2rawslices) gives
        # the clipped global slice plus where it goes in the local tile, since
        # load-balanced tiles may protrude past the domain edge. `.values` (no
        # halo) matches the file's interior shape.
        mask = sim.T.mask.values != 0
        ny, nx = mask.shape

        tiling = getattr(sim.T, "tiling", None)
        ny_sub = nx_sub = None
        if tiling is not None:
            local_slice, global_slice, local_shape, _ = tiling.subdomain2slices()
            _, yslice_l, xslice_l = local_slice
            _, yslice_g, xslice_g = global_slice
            ny_sub, nx_sub = local_shape
            # A tile with no overlap at all gets a negative-width slice
            # rather than an error, so check.
            if yslice_g.stop <= yslice_g.start or xslice_g.stop <= xslice_g.start:
                sim.logger.warning(
                    f"_seed_fabm_state_from_restart: this rank's own tile has no real "
                    f"overlap with {restart_path}'s global domain -- nothing seeded "
                    f"here, FABM state keeps its own fabm.yaml initial_value for this "
                    f"rank."
                )
                return
        else:
            yslice_l = xslice_l = yslice_g = xslice_g = slice(None)

        def _embed_local(arr_glob: "np.ndarray") -> "np.ndarray":
            """Place a globally-clipped read (last two dims = the overlap
            with this rank's own tile) into a NaN-filled array shaped like
            this rank's own FULL local tile. The protruding remainder (this
            tile's own footprint that reaches past the true global domain)
            is left NaN -- always land/masked in practice (that's the
            whole point of max_protrude's load-balancing), and the wet-
            column loop below already treats non-finite source data as
            "skip this column" regardless of why it's non-finite.
            """
            if tiling is None:
                return arr_glob
            assert ny_sub is not None and nx_sub is not None
            out = np.full(arr_glob.shape[:-2] + (ny_sub, nx_sub), np.nan)
            out[..., yslice_l, xslice_l] = arr_glob
            return out

        with xr.open_dataset(restart_path) as ds:
            hnt_glob = ds["hnt"].isel(time=0, yt=yslice_g, xt=xslice_g).values  # (nz_src, ny_g, nx_g), surface-first
            zt_glob = ds["zt"].isel(time=0, yt=yslice_g, xt=xslice_g).values  # (ny_g, nx_g)
            hnt = _embed_local(hnt_glob)
            zt = _embed_local(zt_glob)
            state_names = [var.name for var in sim.fabm.state_variables]
            available = [n for n in state_names if n in ds]
            missing = [n for n in state_names if n not in ds]
            if missing:
                sim.logger.warning(
                    f"_seed_fabm_state_from_restart: {restart_path} has no {missing} -- "
                    f"left at their own fabm.yaml initial_value."
                )
            if not available:
                sim.logger.warning(
                    f"_seed_fabm_state_from_restart: none of this FABM model's state "
                    f"variables were found in {restart_path} -- nothing seeded."
                )
                return
            # Pelagic tracers have the (nz, y, x) shape of hnt and get remapped;
            # benthic ones are (y, x) and are copied horizontally.
            pelagic = [
                n for n in available
                if ds[n].isel(time=0, yt=yslice_g, xt=xslice_g).shape == hnt_glob.shape
            ]
            benthic = [n for n in available if n not in pelagic]
            if pelagic:
                # Embed each tracer before stacking (embedding uses the last two dims).
                source_vals = np.stack(
                    [
                        _embed_local(ds[n].isel(time=0, yt=yslice_g, xt=xslice_g).values)
                        for n in pelagic
                    ],
                    axis=-1,
                )  # (nz_src, ny, nx, n_tracers)
            if benthic:
                import pygetm.constants
                from scipy import ndimage

                # Straight horizontal copy. The file has NaN at a few coastal/
                # channel-edge points (its zt/hnt too). A wet cell must not get
                # FILL_VALUE (FABM would integrate it and diverge), so it takes
                # the nearest valid wet cell's value; only dry cells get
                # FILL_VALUE, as temp/salt above.
                for n in benthic:
                    arr = _embed_local(ds[n].isel(time=0, yt=yslice_g, xt=xslice_g).values)
                    bad = mask & ~np.isfinite(arr)
                    n_benthic_nan += int(bad.sum())
                    valid = mask & np.isfinite(arr)
                    if bad.any() and valid.any():
                        jj, ii = ndimage.distance_transform_edt(
                            ~valid, return_distances=False, return_indices=True
                        )
                        arr[bad] = arr[jj[bad], ii[bad]]
                    arr[~mask] = pygetm.constants.FILL_VALUE
                    benthic_vals[n] = arr

        if hnt.shape[1:] != (ny, nx):
            raise RuntimeError(
                f"_seed_fabm_state_from_restart: {restart_path}'s own horizontal grid "
                f"{hnt.shape[1:]} (this rank's own slice) does not match this run's own "
                f"({ny}, {nx}) -- refusing to seed FABM state from a restart file on a "
                f"different horizontal grid (per-column vertical remapping assumes the "
                f"two line up 1:1)."
            )

        n_seeded = 0
        n_skipped_nan = 0
        if pelagic:
            import pygetm.constants
            from scipy import ndimage

            target_zf = sim.T.zf.values  # (nz_tgt+1, ny, nx), surface-first, real meters -- see mask's own .values comment above (no halo, matching the restart file)
            nz_src = hnt.shape[0]
            n_tracers = source_vals.shape[-1]
            # NaN placeholder (not FILL_VALUE, which must never reach a wet
            # cell): resolved below by nearest-neighbour fill, or FILL_VALUE
            # for dry cells.
            out = np.full((target_zf.shape[0] - 1, ny, nx, n_tracers), np.nan, dtype=np.float64)

            # Source interfaces: zt, then downwards by the cumulative hnt.
            source_ifaces_all = np.empty((nz_src + 1, ny, nx), dtype=np.float64)
            source_ifaces_all[0] = zt
            source_ifaces_all[1:] = zt[np.newaxis] - np.cumsum(hnt, axis=0)

            valid_col = np.zeros((ny, nx), dtype=bool)
            for j in range(ny):
                for i in range(nx):
                    if not mask[j, i]:
                        continue
                    src_ifaces = source_ifaces_all[:, j, i]
                    src_vals = source_vals[:, j, i, :]  # (nz_src, n_tracers)
                    if not np.all(np.isfinite(src_vals)) or not np.all(np.isfinite(src_ifaces)):
                        # NaN source column (see benthic above): filled below.
                        n_skipped_nan += 1
                        continue
                    tgt_ifaces = target_zf[:, j, i]
                    out[:, j, i, :] = _conservative_remap_column(src_vals, src_ifaces, tgt_ifaces)
                    valid_col[j, i] = True
                    n_seeded += 1

            # Wet columns without valid source data take the nearest valid
            # column's remapped profile.
            fill_wet = mask & ~valid_col
            if fill_wet.any() and valid_col.any():
                jj, ii = ndimage.distance_transform_edt(
                    ~valid_col, return_distances=False, return_indices=True
                )
                out[:, fill_wet, :] = out[:, jj[fill_wet], ii[fill_wet], :]
            out[:, ~mask, :] = pygetm.constants.FILL_VALUE  # dry cells only

            for k, name in enumerate(pelagic):
                sim[name][...] = out[..., k]

        for name in benthic:
            sim[name][...] = benthic_vals[name]

        msg = (
            f"_seed_fabm_state_from_restart: seeded pelagic {pelagic} ({n_seeded} wet columns"
            + (f", {n_skipped_nan} filled from nearest valid neighbor" if n_skipped_nan else "")
            + f") and benthic {benthic} (direct horizontal copy"
            + (f", {n_benthic_nan} filled from nearest valid neighbor" if n_benthic_nan else "")
            + f") from {restart_path}"
        )
        sim.logger.info(msg)

    if sim.fabm:
        fabm_cfg = config.get("fabm") or {}
        restart_file = fabm_cfg.get("restart_file")
        if restart_file:
            _seed_fabm_state_from_restart(sim, resolve_data_path(restart_file))
        else:
            # --- FABM tracer IC from boundaries.fabm's source (not
            # hydrography's), when there is no restart_file. A spin-up can
            # start from this climatology and later cycles from restart_file:
            # both are read from the companion *_config.yaml at runtime, so
            # switching needs no regeneration.
            boundaries_fabm_cfg = config.get("boundaries", {}).get("fabm") or {}
            if boundaries_fabm_cfg.get("source") in ("WOA", "CMEMS"):
                time = config.get("runtime", {}).get("time")
                if time is None:
                    raise RuntimeError(
                        "the FABM tracer initial condition needs a real start time, but "
                        "runtime.time isn't set anywhere -- pass --start explicitly (either "
                        "when generating this script, or when running it)."
                    )
                if isinstance(time, str):
                    time = datetime.datetime.fromisoformat(time)
                imonth = time.month - 1

                # WOA: the boundary file/variable is grid-shaped, so it is the
                # IC too. CMEMS: its products are boundary-point shaped, so each
                # tracer's optional ic_file/ic_variable under ic_folder (usually
                # WOA's files). No IC file: the fabm.yaml initial_value stays.
                is_cmems = boundaries_fabm_cfg.get("source") == "CMEMS"
                own_folder = Path(resolve_data_path(boundaries_fabm_cfg["folder"]))
                ic_folder = (
                    Path(resolve_data_path(boundaries_fabm_cfg["ic_folder"]))
                    if is_cmems and boundaries_fabm_cfg.get("ic_folder")
                    else own_folder
                )
                for tracer, spec in (boundaries_fabm_cfg.get("tracers") or {}).items():
                    if is_cmems:
                        ic_filename = spec.get("ic_file")
                        ic_variable = spec.get("ic_variable")
                    else:  # constant / ZERO_GRADIENT tracers have no file
                        ic_filename = spec.get("file")
                        ic_variable = spec.get("variable")
                    if not ic_filename or not ic_variable:
                        continue
                    sim[tracer].set(
                        pygetm.input.from_nc(ic_folder / ic_filename, ic_variable).isel(time=imonth)
                    )
