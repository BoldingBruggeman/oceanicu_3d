"""Hydrography initial-condition attachment (a domain config's
hydrography.<source>.data_script -- see oceanicu_providers.py). Mirrors
cfg_ic.py's own create() for the WOA/CMEMS branches specifically
("constant" hydrography is plain data_assignments, no Python needed).

Loaded via pygetm_config.providers.load_dotted_target ("path/to/file.py:name"),
never imported directly -- see oceanicu_driver.py's own module docstring for
how that's wired (hydrography.<source>.data_script's default, pointing here).
"""

from __future__ import annotations

from pathlib import Path

from pygetm_config.loader import resolve_data_path


def set_hydrography_ic(sim, domain, config: dict) -> None:
    """Mirrors cfg_ic.py's own create() -- WOA/CMEMS branches specifically
    ("constant" hydrography is plain data_assignments, no Python needed).
    Real Python needed for: (1) `.isel(time=imonth)` -- a monthly
    CLIMATOLOGY index PICK for the initial, one-time value (imonth derived
    from config['runtime']['time'], matching run_model.py's own
    `simstart.month - 1`) -- NOT pygetm-config's own `climatology: True`
    data_assignments flag, which means something different (keep cycling
    the whole 12-month pattern for the entire run, wrong for an initial
    condition). (2) sim.density.convert_ts(sim.salt, sim.temp) -- pyGETM's
    internal state is conservative temperature/absolute salinity, WOA/CMEMS
    provide in-situ/practical values -- called for BOTH WOA and CMEMS,
    matching cfg_ic.py's own code exactly (present in both real-data
    branches, absent from "constant").

    Masks out land points afterward (sim.temp/sim.salt set to
    pygetm.constants.FILL_VALUE where sim.T.mask == 0), matching cfg_ic.py's
    own code -- both WOA/CMEMS climatology files are GLOBAL, so horizontal
    interpolation can leave real (non-fill) values sitting at domain points
    outside the real ocean mask.

    Registered via hydrography.data_script (see pygetm_config.loader.
    run_hydrography_data_script) -- only called when NOT loading from a
    restart (checked there, not here). Checks runtype == BAROCLINIC itself
    (matching cfg_ic.py's own identical gate) since the core loader
    function doesn't special-case runtype for any of its three data_script
    hooks.

    `folder` may be a "${VAR}"/"$VAR" reference (pygetm-config's own lazy
    data-path resolution mechanism) -- resolve_data_path expands it here,
    at actual use time. See scripts/rivers.py's own docstring for why this
    function has to opt into that explicitly (a project-specific script
    hook, not core pygetm-config code going through loader._coerce_value's
    generic "path" TypeKind handling).

    Also home to the ONE-TIME FABM tracer initial condition (2026-10-01,
    moved here from scripts/fabm.py's own configure_fabm) -- either the
    ERSEM restart-style seed (fabm.ERSEM.restart_file) or, failing that,
    the WOA/CMEMS climatology pick, mirroring the T/S IC above but driven
    by boundaries.fabm's OWN, independently-configured source, not
    hydrography's -- see that block's own comment further down for the
    full reasoning (including the real restart_file-vs-climatology
    perpetual-spin-up state machine this supports).
    """
    import datetime

    import pygetm
    import pygetm.constants
    import pygetm.input

    if sim.runtype != pygetm.RunType.BAROCLINIC:
        return

    hydro = config["hydrography"]
    source = hydro.get("source")
    # NOT a bare `if source not in (...): return` any more (2026-09-30, per
    # user) -- that used to skip the ERSEM restart block below outright for
    # "constant" hydrography, even though the two are independent (T/S IC
    # source has no bearing on whether a FABM restart IC should apply).
    # Scoped to just the WOA/CMEMS T/S block instead, so "constant"
    # hydrography still falls through to the restart block further down.
    if source in ("WOA", "CMEMS"):
        # runtime.time is deliberately never in the static config (start/stop
        # are per-invocation, not per-setup -- see nse_from_oceanicu.yaml's own
        # header comment) -- both oceanicu_driver.py's live path and codegen's
        # generated scripts fill it in from --start before this hook ever runs
        # (`config.setdefault('runtime', {})['time'] = args.start`), but only if
        # --start was actually given somewhere (at generation time, baked into
        # the script's own --start default, or at the script's own invocation).
        # A bare KeyError here (real, reproduced case: a --dry-run invocation
        # with no --start anywhere) gave no hint why; this hook's own monthly-
        # climatology index pick genuinely can't proceed without a real date.
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
        #sim.density.convert_ts(sim.salt, sim.temp)

        sim.temp[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE
        sim.salt[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE

    # --- ERSEM state restart-style initial condition ---
    # Lives HERE, not in scripts/fabm.py's own configure_fabm, for a real
    # reason: configure_fabm runs on EVERY chunk unconditionally (its
    # dependency setup -- atmospheric CO2/N2O, N-deposition, fish pressure
    # -- is real, time-varying forcing that every chunk needs, restart or
    # not), whereas this hook (hydrography.data_script) is only ever
    # called for a genuine fresh start -- the call site itself skips it
    # entirely under `if not args.load_restart:` (see this function's own
    # docstring). Putting the restart-IC load there would have silently
    # re-applied it on every continuation chunk too. Piggybacking on this
    # existing fresh-start-only gate avoids needing a new, separately-
    # gated hook (and the pygetm-config codegen changes that would need).
    #
    # fabm.ERSEM.restart_file points at a real pygetm restart-format
    # NetCDF (add_restart()/load_restart() shape) -- e.g. /data/OceanICU/
    # oceanicu_3d/data/NSe/FABM/init/restart_ersem.nc, which per user
    # (2026-09-30) ALSO carries temperature/salinity/other physical
    # fields (confirmed directly: 210 data_vars, most physical --
    # hnt/zt/U/V/pk/qk/... -- alongside 74 real ERSEM-shaped ones like
    # N1_p/N3_n/R1_c).
    #
    # NOT read via sim.load_restart() (an earlier version of this hook
    # did) -- that uses on_grid=OnGrid.ALL internally (see pygetm.
    # simulation.Simulation.load_restart's own field.set(..., on_grid=
    # pygetm.input.OnGrid.ALL) call), which explicitly SKIPS vertical
    # interpolation, assuming the file's own layer k IS the current
    # grid's own layer k. That's true for a genuine continuation restart
    # (same run, same grid, by construction) but false here: restart_
    # ersem.nc is a "perpetual ERSEM" snapshot from a DIFFERENT run,
    # saved with WHATEVER layer heights that run had at save time --
    # this run's own fresh-start layer heights (computed from ITS OWN
    # water depth/vertical-coordinate state, see pygetm.vertical_
    # coordinates) can differ, for GVC by however much the free-surface
    # elevation differs, and for Adaptive coordinates potentially much
    # more (stratification-driven, not depth-driven -- confirmed via a
    # real comparison plot, 2026-09-30, per user: a same-total-depth but
    # differently-SHAPED target grid gave present-method values badly
    # offset from the true profile at every depth, while a conservative
    # remap recovered it exactly). Doing a blind index-to-index copy in
    # that case silently misplaces every tracer's real vertical
    # structure -- worse than not seeding at all, since it looks
    # plausible without being physically meaningful.
    #
    # Fixed here via a real per-column conservative vertical remap
    # instead: reconstruct the FILE's own saved layer interfaces from
    # its hnt (layer thickness)/zt (surface elevation), then for each
    # tracer and each wet column, interpolate the CUMULATIVE (depth-
    # integrated) profile at THIS run's own fresh-start interfaces
    # (sim.T.zf) and difference -- exact for a piecewise-constant
    # per-layer-mean source, and reduces to ordinary linear
    # interpolation (of the cumulative function, not the raw values) so
    # it needs no bespoke overlap-integration code. Vectorized across
    # every tracer within a column (they share the same source/target
    # interfaces, only the values differ) -- the remaining Python loop
    # over wet (j, i) columns is the only part that can't be vectorized
    # away (every column has its own source AND target interfaces), but
    # each iteration's own numpy work is cheap enough that this is a
    # negligible one-time cost at sim start, not per-timestep.
    #
    # `if sim.fabm:` guards this explicitly (redundant with configure_fabm's
    # own internal check on the SAME condition, but this hook has no such
    # check of its own otherwise -- per user, 2026-09-30).
    #
    # Both helpers below are nested INSIDE this function, not module-level
    # siblings -- pygetm_config.codegen's --dump-python only inlines a
    # data_script hook's OWN source (inspect.getsource on set_hydrography_ic
    # alone), never any sibling module-level helper it calls (see this
    # project's own hit, 2026-09-15, on rivers.py's _apply_calendar_suffix --
    # NameError in the generated standalone script only, invisible when
    # running live via oceanicu_driver.py which imports the real module).
    # Hit again here 2026-09-30 the same way -- fixed by nesting instead of
    # factoring out, so inspect.getsource captures everything needed in one
    # shot.
    def _seed_fabm_state_from_restart(sim, restart_path: str) -> None:
        """Conservative per-column vertical remap of every FABM state
        variable found in *restart_path* onto sim's own fresh-start vertical
        grid -- see set_hydrography_ic's own docstring for the full
        reasoning (why not sim.load_restart(), why conservative, why
        per-column). A tracer present in sim.fabm.state_variables but absent
        from the file is left untouched (keeps its own fabm.yaml
        initial_value) with a warning, not a hard error -- the file is a
        real ERSEM state snapshot, not guaranteed to be perfectly exhaustive
        against every possible fabm.yaml variant.
        """
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

            Conservative remap of a per-layer-mean profile reduces to
            interpolating the CUMULATIVE (depth-integrated) profile at the
            target interfaces, then differencing and dividing by target layer
            thickness -- exact for a piecewise-constant source, and needs no
            bespoke overlap-integration code. A target layer extending beyond
            the source's own depth range is clamped (constant extrapolation,
            matching numpy.interp's own default) -- e.g. a shallower target
            grid simply doesn't reach the source's deepest layers at all, which
            is the physically correct outcome, not an error. nz_src and nz_tgt
            need not match -- this interpolates on real depth, never on layer
            index, so it works just as well for a genuine resolution change as
            for a same-resolution depth/shape mismatch (per user, 2026-09-30).
            """
            src_thick = (source_ifaces[:-1] - source_ifaces[1:])[:, None]
            cum_src = np.concatenate(
                [np.zeros((1, source_vals.shape[1])), np.cumsum(source_vals * src_thick, axis=0)],
                axis=0,
            )
            x_src = -source_ifaces  # increasing, required by searchsorted below
            x_tgt = -target_ifaces
            idx = np.clip(np.searchsorted(x_src, x_tgt, side="right") - 1, 0, len(x_src) - 2)
            x0, x1 = x_src[idx], x_src[idx + 1]
            with np.errstate(invalid="ignore", divide="ignore"):
                frac = np.where(x1 > x0, (x_tgt - x0) / (x1 - x0), 0.0)
            cum_at_target = cum_src[idx] + frac[:, None] * (cum_src[idx + 1] - cum_src[idx])
            cum_at_target[x_tgt <= x_src[0]] = cum_src[0]
            cum_at_target[x_tgt >= x_src[-1]] = cum_src[-1]
            tgt_thick = (target_ifaces[:-1] - target_ifaces[1:])[:, None]
            tgt_content = cum_at_target[1:] - cum_at_target[:-1]
            with np.errstate(invalid="ignore", divide="ignore"):
                return tgt_content / tgt_thick

        source_vals = None
        benthic_vals = {}
        n_benthic_nan = 0
        # restart_ersem.nc holds this run's FULL global (yt, xt) domain --
        # under real MPI spatial decomposition, THIS rank's own sim.T only
        # covers one local tile of it, so the file must be sliced down to
        # match before comparing/using its arrays. sim.T.tiling (pygetm.
        # parallel.Tiling -- confirmed via pygetm/domain.py's own scatter/
        # gather code as the mechanism pygetm.input.HorizontalInterpolation
        # relies on for its own per-rank reads of WOA/CMEMS-style forcing).
        #
        # subdomain2rawslices() alone is NOT enough: pygetm's real load-
        # balanced decomposition (Tiling.autodetect, max_protrude=0.5 by
        # default) allows a tile to protrude PAST the true coastline/global
        # edge by design (to keep tile sizes uniform while skipping
        # all-land tiles for load balancing) -- hit for real on bb-server1,
        # 2026-09-30, NP=183: one edge rank's raw x-slice landed entirely
        # beyond the file's own 257-wide global array, giving a silent
        # zero-width slice (55, 0) instead of that rank's real local width
        # (55, 52). subdomain2slices() is the version that actually
        # accounts for this: it returns the CLIPPED (always in-bounds)
        # global slice to read, the matching slice into this rank's own
        # FULL local (ny_sub, nx_sub) tile shape to place it at, and that
        # local shape itself -- exactly the local/global pair pygetm's own
        # gather/scatter code uses for the same reason.
        # `.values` (no halo), NOT `.all_values` -- restart_ersem.nc's own
        # hnt/zt were written from the halo-free interior (its real domain
        # shape), matching this run's own `.values`; `.all_values` is
        # bigger by 2*halo in each direction (confirmed on bb-server1,
        # 2026-09-30: file (251, 257) vs `.all_values` (255, 261) -- exactly
        # halo=2 on each side). See pygetm.core.Array's own `.mask`/`.values`
        # properties (halo-excluded) vs `.all_mask`/`.all_values` (halo-
        # included). Computed up front (independent of the restart file
        # itself) since both the pelagic and benthic fallback logic below
        # need it.
        mask = sim.T.mask.values != 0
        ny, nx = mask.shape

        tiling = getattr(sim.T, "tiling", None)
        ny_sub = nx_sub = None
        if tiling is not None:
            local_slice, global_slice, local_shape, _ = tiling.subdomain2slices()
            _, yslice_l, xslice_l = local_slice
            _, yslice_g, xslice_g = global_slice
            ny_sub, nx_sub = local_shape
            # subdomain2slices() does NOT guard against a tile with ZERO real
            # overlap with the global domain -- it silently returns a
            # nonsensical negative-width local slice instead of raising
            # (confirmed directly, 2026-09-30, forcing a deliberately fully-
            # protruding tile). A tile that protrudes THAT much shouldn't
            # occur under Tiling.autodetect's own max_protrude cap in
            # practice, but check explicitly rather than trust it blindly.
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
            # A real ERSEM restart carries BOTH pelagic (z-resolved) and
            # BENTHIC (sediment, no z dimension at all -- e.g. Q1_c/Q6_*/
            # K1_p/H1_c/Y2_c/...) state variables (confirmed directly
            # against restart_ersem.nc, 2026-09-30: Q1_c etc. are (ny, nx),
            # not (nz, ny, nx)). Only pelagic tracers have any vertical
            # structure to remap -- split on each variable's own shape
            # rather than assuming sim.fabm.state_variables is pelagic-only
            # (an earlier version of this code did, and np.stack failed
            # outright mixing (nz, ny, nx) and (ny, nx) arrays together).
            # Compared against hnt_glob's own (clipped, not-yet-embedded)
            # shape -- every real variable in the file shares that same
            # clipped extent.
            pelagic = [
                n for n in available
                if ds[n].isel(time=0, yt=yslice_g, xt=xslice_g).shape == hnt_glob.shape
            ]
            benthic = [n for n in available if n not in pelagic]
            if pelagic:
                # Embed EACH tracer individually (last two dims still (y, x)
                # at that point) before stacking a new tracer axis onto the
                # end -- embedding the already-stacked array instead would
                # embed against the wrong last-two-dims (n_tracers, not x).
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

                # No vertical structure at all -- a straight horizontal copy,
                # same convention as the horizontal-grid check just below
                # (per-column remapping assumes file and run line up 1:1).
                # A handful of genuinely NaN restart points are a known,
                # long-standing artifact at some coastline/channel-edge
                # cells (confirmed directly, 2026-09-30: restart_ersem.nc's
                # own zt/hnt are ALSO NaN at those exact points, so it's a
                # gap in the file itself, not specific to any one tracer).
                #
                # A WET (active) cell with bad source data must NOT fall
                # back to pygetm.constants.FILL_VALUE -- that's a sentinel
                # for cells the model never touches (masked/dry); writing
                # it into a genuinely active cell instead means FABM's own
                # biogeochemistry integrates that physically-absurd
                # concentration forward like any other -- confirmed
                # directly, 2026-09-30: a real run passed IC-time check_
                # finite fine (FILL_VALUE is merely a large finite number)
                # but failed at istep=40 with several pelagic AND benthic
                # tracers newly non-finite, traced back to exactly this.
                #
                # Filled instead from the NEAREST valid (wet, finite) cell
                # of the SAME tracer (per user, 2026-09-30) -- preserves
                # local spatial structure (a coastal cell gets a value
                # close to its real neighbors), unlike a flat domain-wide
                # default. Standard nearest-fill recipe: distance_
                # transform_edt on the invalid mask, with return_indices,
                # gives each invalid cell's nearest valid cell's own index.
                # Only genuinely dry cells (mask False) get FILL_VALUE,
                # matching this hook's own WOA/CMEMS temp/salt masking.
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
            # NaN, not np.empty's uninitialized garbage nor pygetm.constants.
            # FILL_VALUE -- a dry (masked-out) or NaN-skipped column is never
            # written into `out` in the loop below on its own, so it needs a
            # defined placeholder here. NaN specifically (not FILL_VALUE):
            # FILL_VALUE is a large but FINITE sentinel meant only for cells
            # the model never touches (masked/dry) -- writing it into a
            # genuinely ACTIVE wet column instead means FABM's own
            # biogeochemistry integrates that physically-absurd concentration
            # forward like any other value, and reliably diverges to NaN
            # within a few timesteps (real bug hit 2026-09-30: a run passed
            # IC-time check_finite fine but failed at istep=40 with several
            # tracers newly non-finite, traced back to exactly this). NaN
            # placeholders here get properly resolved below instead --
            # nearest-valid-neighbor fill for wet columns, real FILL_VALUE
            # only for genuinely dry ones.
            out = np.full((target_zf.shape[0] - 1, ny, nx, n_tracers), np.nan, dtype=np.float64)

            # Source interfaces: cumulative sum of hnt DOWNWARD from zt
            # (surface first, increasingly negative going down) -- same
            # construction and convention validated directly against this
            # exact file, 2026-09-30 (see the standalone test_conservative_
            # remap.py this was prototyped in: identity-remap and
            # conservation checks both passed to floating-point precision
            # on a real column).
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
                        # A handful of genuinely NaN restart points are a known,
                        # long-standing artifact at some coastline/channel-edge
                        # cells (confirmed directly, 2026-09-30: restart_ersem.
                        # nc's own zt/hnt are ALSO NaN at those exact points).
                        # Resolved below via nearest-valid-neighbor fill, not
                        # here -- this loop only tracks which columns COULDN'T
                        # be remapped directly.
                        n_skipped_nan += 1
                        continue
                    tgt_ifaces = target_zf[:, j, i]
                    out[:, j, i, :] = _conservative_remap_column(src_vals, src_ifaces, tgt_ifaces)
                    valid_col[j, i] = True
                    n_seeded += 1

            # Wet columns with bad source data: fall back to the NEAREST
            # valid wet column's own remapped profile (per user, 2026-09-30)
            # -- preserves local spatial structure (a coastal cell gets a
            # value close to its real neighbors), unlike a flat domain-wide
            # default. Standard nearest-fill recipe: distance_transform_edt
            # on the invalid mask, with return_indices, gives each invalid
            # cell's nearest valid cell's own index.
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
            # --- WOA/CMEMS-sourced FABM tracer initial conditions ---
            # Moved here from scripts/fabm.py's own configure_fabm
            # (2026-10-01, real bug hit directly) -- that function runs on
            # EVERY chunk unconditionally (see this function's own
            # docstring just above, re: why the restart-style IC lives here
            # and not there), so a one-time climatology-pick IC left there
            # would silently re-apply on every restart-continuation chunk
            # too, overwriting whatever real, evolved state a genuine
            # continuation just loaded. This hook is only ever called on a
            # genuine fresh start (gated at the call site, `if not args.
            # load_restart:` -- see this function's own docstring), so it's
            # the correct home for ANY one-time FABM IC, restart-file-
            # sourced (above) or climatology-sourced (below) alike.
            #
            # Independent of hydrography's own T/S source above -- this is
            # boundaries.fabm's own, separately-configured source (per
            # user, 2026-10-01: a WOA-sourced setup needs this exact same
            # restart-vs-climatology choice for a perpetual spin-up too,
            # same as CMEMS; the two roles are independently configured on
            # purpose, see oceanicu_providers.py's own boundary_fabm role
            # comment). So this does NOT reuse the `imonth`/`time` computed
            # in the `source in ("WOA", "CMEMS")` block above (that's
            # hydrography's OWN source, e.g. "constant", which could differ
            # and might not even have run) -- computed independently here,
            # exactly mirroring what configure_fabm used to do.
            #
            # restart_file vs this climatology branch is a real, intended
            # perpetual-spin-up state machine, not an either/or default
            # (per user, 2026-10-01): the FIRST spin-up cycle has no
            # restart_ersem.nc yet (climatology IC, this branch), and every
            # SUBSEQUENT cycle re-seeds from the previous cycle's own
            # ending state instead (restart_file, set above) -- both
            # fabm.ERSEM.restart_file and boundaries.fabm.<source>.ic_folder/
            # tracers are read LIVE from the companion generated_*_config.
            # yaml at runtime (confirmed directly), so switching between
            # cycles only needs editing that file, not regenerating.
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

                # WOA: spec["file"]/spec["variable"] themselves (already
                # grid-shaped, same file the boundary uses) under this
                # choice's own `folder`. CMEMS: NOT spec["file"]/
                # spec["variable"] (the real nbdyp-shaped boundary-forcing
                # product/variable name) -- every CMEMS/CMIP6 bio product
                # in this project turned out to be boundary-point-shaped,
                # confirmed directly on bb-server1 (ncdump -h), including
                # bio_monthly_climatology.nc, which an earlier version of
                # this code wrongly assumed was grid-shaped. There is no
                # genuinely grid-shaped CMEMS/CMIP6 biogeochemistry product
                # anywhere in this project's data -- WOA's own global
                # climatology is the only one, so each tracer's own
                # OPTIONAL ic_file/ic_variable (under this choice's own
                # ic_folder, e.g. ${BOUNDARY_FOLDER_FABM_WOA}) is expected
                # to point at THAT (e.g. woa_n.nc/n_an), same real files/
                # variable names boundaries.fabm.WOA's own `tracers`
                # already uses -- see ic_folder's own ParameterSpec comment
                # in oceanicu_providers.py. Neither available -> leave that
                # tracer alone, same as any tracer not listed in `tracers`
                # at all: it keeps its own fabm.yaml-declared initial_value.
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
                    else:
                        ic_filename = spec["file"]
                        ic_variable = spec["variable"]
                    if not ic_filename or not ic_variable:
                        continue
                    sim[tracer].set(
                        pygetm.input.from_nc(ic_folder / ic_filename, ic_variable).isel(time=imonth)
                    )
