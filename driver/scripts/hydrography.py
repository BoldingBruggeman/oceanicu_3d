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
    if source not in ("WOA", "CMEMS"):
        return

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
    # NetCDF (add_restart()/load_restart() shape) -- e.g. bb-server1:/tmp/
    # restart_ersem.nc, which per user (2026-09-30) ALSO carries
    # temperature/salinity/other physical fields (confirmed directly: 210
    # data_vars, most physical -- hnt/zt/U/V/pk/qk/... -- alongside 74
    # real ERSEM-shaped ones like N1_p/N3_n/R1_c). sim.load_restart()
    # loads every field present in sim.output_manager.fields flagged
    # _part_of_state and RAISES if one is missing from the file -- so
    # narrowing that dict to just the FABM state variable names first
    # means only those are looked for/loaded; the file's own temp/salt
    # (already set above, from WOA/CMEMS) are excluded from the narrowed
    # dict and never touched, regardless of being present in the file.
    # `if sim.fabm:` guards this explicitly (redundant with configure_fabm's
    # own internal check on the SAME condition, but this hook has no such
    # check of its own otherwise -- per user, 2026-09-30).
    if sim.fabm:
        fabm_cfg = config.get("fabm") or {}
        restart_file = fabm_cfg.get("restart_file")
        if restart_file:
            restart_path = resolve_data_path(restart_file)
            fabm_state_names = {var.name for var in sim.fabm.state_variables}
            all_fields = sim.output_manager.fields
            sim.output_manager.fields = {
                name: field for name, field in all_fields.items() if name in fabm_state_names
            }
            try:
                sim.load_restart(restart_path)
            finally:
                sim.output_manager.fields = all_fields
            sim.logger.info(f"set_hydrography_ic: seeded FABM state from {restart_path}")
