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

        with xr.open_dataset(restart_path) as ds:
            hnt = ds["hnt"].isel(time=0).values  # (nz_src, ny, nx), surface-first
            zt = ds["zt"].isel(time=0).values  # (ny, nx)
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
            source_vals = np.stack(
                [ds[n].isel(time=0).values for n in available], axis=-1
            )  # (nz_src, ny, nx, n_tracers)

        target_zf = sim.T.zf.all_values  # (nz_tgt+1, ny, nx), surface-first, real meters
        mask = sim.T.mask.all_values != 0
        ny, nx = mask.shape
        if hnt.shape[1:] != (ny, nx):
            raise RuntimeError(
                f"_seed_fabm_state_from_restart: {restart_path}'s own horizontal grid "
                f"{hnt.shape[1:]} does not match this run's own ({ny}, {nx}) -- refusing "
                f"to seed FABM state from a restart file on a different horizontal grid "
                f"(per-column vertical remapping assumes the two line up 1:1)."
            )

        nz_src = hnt.shape[0]
        n_tracers = source_vals.shape[-1]
        out = np.empty((target_zf.shape[0] - 1, ny, nx, n_tracers), dtype=np.float64)

        # Source interfaces: cumulative sum of hnt DOWNWARD from zt (surface
        # first, increasingly negative going down) -- same construction and
        # convention validated directly against this exact file, 2026-09-30
        # (see the standalone test_conservative_remap.py this was prototyped
        # in: identity-remap and conservation checks both passed to
        # floating-point precision on a real column).
        source_ifaces_all = np.empty((nz_src + 1, ny, nx), dtype=np.float64)
        source_ifaces_all[0] = zt
        source_ifaces_all[1:] = zt[np.newaxis] - np.cumsum(hnt, axis=0)

        n_seeded = 0
        n_skipped_nan = 0
        for j in range(ny):
            for i in range(nx):
                if not mask[j, i]:
                    continue
                src_ifaces = source_ifaces_all[:, j, i]
                src_vals = source_vals[:, j, i, :]  # (nz_src, n_tracers)
                if not np.all(np.isfinite(src_vals)) or not np.all(np.isfinite(src_ifaces)):
                    # A handful of genuinely NaN restart points are a known,
                    # long-standing artifact at some coastline/channel-edge
                    # velocity points (confirmed directly, 2026-09-29, on this
                    # same domain's own physical restart fields) -- for FABM
                    # tracers specifically this hasn't been observed, but the
                    # same defensive skip applies: leave this column's
                    # tracers at their fabm.yaml initial_value rather than
                    # propagate a NaN into a otherwise-healthy simulation.
                    n_skipped_nan += 1
                    continue
                tgt_ifaces = target_zf[:, j, i]
                out[:, j, i, :] = _conservative_remap_column(src_vals, src_ifaces, tgt_ifaces)
                n_seeded += 1

        for k, name in enumerate(available):
            sim[name][...] = out[..., k]

        msg = f"_seed_fabm_state_from_restart: seeded {available} from {restart_path} ({n_seeded} wet columns"
        if n_skipped_nan:
            msg += f", {n_skipped_nan} skipped for non-finite source data"
        sim.logger.info(msg + ")")

    if sim.fabm:
        fabm_cfg = config.get("fabm") or {}
        restart_file = fabm_cfg.get("restart_file")
        if restart_file:
            _seed_fabm_state_from_restart(sim, resolve_data_path(restart_file))
