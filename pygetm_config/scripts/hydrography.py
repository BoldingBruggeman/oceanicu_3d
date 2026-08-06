"""Hydrography initial-condition attachment (nse_from_oceanicu.yaml's
hydrography.<source>.data_script -- see oceanicu_providers.py). Mirrors
cfg_ic.py's own real create() for the WOA/CMEMS branches specifically
("constant" hydrography is plain data_assignments, no Python needed).

Loaded via pygetm_config.providers.load_dotted_target ("path/to/file.py:name"),
never imported directly -- see nse_driver.py's own module docstring for how
that's wired (hydrography.<source>.data_script's default, pointing here).
"""

from __future__ import annotations

from pathlib import Path

from pygetm_config.loader import resolve_data_path


def set_hydrography_ic(sim, domain, config: dict) -> None:
    """Mirrors cfg_ic.py's own create() -- WOA/CMEMS branches specifically
    ("constant" hydrography is plain data_assignments, no Python needed).
    Real Python needed for: (1) `.isel(time=imonth)` -- a monthly
    CLIMATOLOGY index PICK for the initial, one-time value (imonth derived
    from config['runtime']['time'], matching run_model.py's own real
    `simstart.month - 1`) -- NOT pygetm-config's own `climatology: True`
    data_assignments flag, which means something different (keep cycling
    the whole 12-month pattern for the entire run, wrong for an initial
    condition). (2) sim.density.convert_ts(sim.salt, sim.temp) -- pyGETM's
    internal state is conservative temperature/absolute salinity, WOA/CMEMS
    provide in-situ/practical values (user request, TODO item 21) --
    called for BOTH WOA and CMEMS, matching cfg_ic.py's own real code
    exactly (present in both real-data branches, absent from "constant").

    Masks out land points afterward (sim.temp/sim.salt set to
    pygetm.constants.FILL_VALUE where sim.T.mask == 0), matching cfg_ic.py's
    own real code -- both WOA/CMEMS climatology files are GLOBAL, so
    horizontal interpolation can leave real (non-fill) values sitting at
    domain points outside the real ocean mask.

    Registered via hydrography.data_script (see pygetm_config.loader.
    run_hydrography_data_script) -- only called when NOT loading from a
    restart (checked there, not here). Checks runtype == BAROCLINIC itself
    (matching cfg_ic.py's own identical gate) since the core loader
    function doesn't special-case runtype for any of its three data_script
    hooks.

    `folder` may be a "${VAR}"/"$VAR" reference (pygetm-config's own TODO
    item 15 lazy-resolution mechanism) -- resolve_data_path expands it here,
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

    time = config["runtime"]["time"]
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
    sim.density.convert_ts(sim.salt, sim.temp)

    sim.temp[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE
    sim.salt[..., sim.T.mask == 0] = pygetm.constants.FILL_VALUE
