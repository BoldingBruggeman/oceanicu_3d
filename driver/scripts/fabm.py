"""FABM dependency/IC/boundary setup that genuinely needs real Python, not a
static data_assignments entry (a domain config's fabm.<source>.data_script --
see oceanicu_providers.py). Mirrors cfg_fabm.py's own configure(sim, cfg,
imonth) -- see configure_fabm's own docstring.

Loaded via pygetm_config.providers.load_dotted_target ("path/to/file.py:name"),
never imported directly -- see oceanicu_driver.py's own module docstring for
how that's wired (fabm.<source>.data_script's default points here).
"""

from __future__ import annotations

# Module level, NOT inside configure_fabm -- matches hydrography.py/meteo.py/
# rivers.py's own identical pattern, for a real reason (a bug this file used
# to have): codegen._emit_script_hook's inspect.getsource() only captures a
# hook's own FUNCTION BODY, not imports sitting above it at module level, and
# codegen's generated _utils.py already defines its OWN standalone
# resolve_data_path (no pygetm_config dependency -- the whole generated
# script needs it regardless of which hooks are used). So a module-level
# import here is invisible to the embedded copy, which then resolves
# `resolve_data_path` via the enclosing _utils.py module's own definition
# instead -- works both live (real pygetm_config.loader import) and embedded
# (pygetm_config-free). Putting this import INSIDE configure_fabm instead (as
# an earlier version of this file did, matching the "keep everything
# self-contained" convention used for genuinely custom helpers like
# _add_coord below) gets copied verbatim into the embedded code and executes
# on the target machine at call time -- a real, reproduced ModuleNotFoundError
# on a machine with pygetm installed but not pygetm_config (the whole point
# of --dump-python is a script that only needs the former).
from pathlib import Path

from pygetm_config.loader import resolve_data_path


def configure_fabm(sim, domain, config: dict) -> None:
    """Mirrors cfg_fabm.py's own configure(sim, cfg, imonth): the piece of
    FABM setup that genuinely can't be a static data_assignments entry.
    data_assignments' own fabm.<tracer_name>.<attr> target only reaches
    FABM *state variables* (sim.fabm.state_variables) -- FABM *dependencies*
    (sim.fabm.get_dependency(...), e.g. atmospheric CO2, gelbstoff
    absorption, N-deposition) are a separate, narrower API with no
    data_assignments equivalent, and need real Python here instead.

    Checks BOTH that FABM is enabled at all (sim.fabm, from
    simulation.fabm -- see cfg_fabm.py's own `if not sim.fabm: return`
    guard) AND which specific FABM-compatible biogeochemical model is
    configured (config["fabm"]["source"], e.g. "ERSEM" -- pyGETM/FABM
    supports many models, not just one, so both checks matter
    independently, matching cfg_fabm.py's own `cfg.fabm.config == "ersem"`
    branch).

    Guarded to runtype == BAROCLINIC only (explicit user decision -- unlike
    cfg_fabm.py's own real code, which has a BAROTROPIC_3D branch supplying
    constant temperature/practical_salinity/density dependencies since a
    barotropic run has no real T/S field to read from, this driver commits
    to BAROCLINIC-only FABM: no barotropic support, no dead branch for it).
    Matches hydrography.py's own identical gate (`if sim.runtype !=
    pygetm.RunType.BAROCLINIC: return`) and its own reasoning -- the core
    loader function (run_fabm_data_script) doesn't special-case runtype for
    any of its four data_script hooks, so this is checked here instead.

    ERSEM dependency setup (gelbstoff absorption from a satellite product,
    atmospheric CO2, EMEP N-deposition) is ported from cfg_fabm.py as-is,
    reading its folder from fabm.ERSEM.folder. WOA-sourced FABM tracer
    ICs/SPONGE boundaries are ALSO ported, but now gated on this driver's
    own boundaries.fabm.source (config["boundaries"]["fabm"]["source"])
    instead of cfg_fabm.py's real cfg.hydrography.source check -- FABM
    boundary data gets its own role, independent of T/S hydrography (see
    oceanicu_providers.py's boundaries.fabm comment for why), so a config
    can enable ERSEM dependencies without necessarily also using WOA for
    FABM boundaries, and vice versa -- these two blocks below are
    deliberately independent, not an if/elif.

    NOT YET VERIFIED against real NSe input files (mesh_mask.nc-style grid
    file, AMM7-EMEP-style N-deposition netCDFs, gelbstoff/CDOM product, WOA
    tracer climatologies) -- cfg_fabm.py's own inputs are AMM-domain-
    specific; this port keeps the same dependency names/file-shape
    expectations, but NSe's own equivalent files need confirming before a
    real run. The `_add_coord` regridding helper below in particular
    assumes a `mesh_mask.nc` with `nav_lat`/`nav_lon` 2D coordinate arrays
    (cfg_fabm.py's own AMM convention) -- verify NSe's own EMEP-equivalent
    source uses the same shape before trusting this unmodified.
    """
    import datetime

    import pygetm
    import pygetm.input

    if not sim.fabm:
        return

    if sim.runtype != pygetm.RunType.BAROCLINIC:
        return

    fabm_cfg = config.get("fabm") or {}
    source = fabm_cfg.get("source")

    if source != "ERSEM":
        sim.logger.critical(
            f"configure_fabm: no FABM configuration for source={source!r} "
            "-- add a branch in driver/scripts/fabm.py"
        )
        return

    sim.logger.info("configure_fabm: providing FABM configuration for ERSEM (not provided by pyGETM)")

    # NOT fabm_cfg.get("ERSEM") -- validate_config's choice-flattening puts
    # the ACTIVE choice's own fields directly on the parent dict (here,
    # fabm_cfg itself, since source == "ERSEM"); only an INACTIVE
    # alternative stays nested under its own label key. Real, reproduced
    # bug (caught on a remote production run): fabm_cfg.get("ERSEM") found
    # nothing (that key doesn't exist once ERSEM is active) and silently
    # fell back to {}, then KeyError'd on "folder" -- exactly the same
    # pitfall scripts/meteo.py's own set_meteo_data docstring already
    # documents and avoids (reading meteo.get("folder") directly, not
    # meteo.get("CMIP6")).
    fabm_folder = Path(resolve_data_path(fabm_cfg["folder"]))

    # A routine by Gennadi to make data compatible with the input manager --
    # nested here (not module-level) since codegen's _emit_script_hook only
    # embeds the directly-referenced hook function's own source via
    # inspect.getsource, not any module-level helpers it calls (a real,
    # confirmed gap -- see driver/scripts/meteo.py's own established
    # convention of staying fully self-contained for the same reason).
    def _add_coord(nc):
        import xarray as xr

        fmesh = xr.open_dataset(fabm_folder / "mesh_mask.nc")
        nc = nc.drop_vars(("lon", "lat"))
        nc = nc.rename_dims(x="longitude", y="latitude")
        lat_AMM = fmesh.nav_lat.data
        lon_AMM = fmesh.nav_lon.data
        nc = nc.rename_dims({"t": "time"})
        nc = nc.rename_vars({"t": "time"})
        nc = nc.assign_coords(
            latitude=("latitude", lat_AMM[:, 0]), longitude=("longitude", lon_AMM[0, :])
        )
        nc.coords["latitude"] = nc.latitude.assign_attrs(long_name="latitude", units="degrees_north")
        nc.coords["longitude"] = nc.longitude.assign_attrs(long_name="longitude", units="degrees_east")
        nc.coords["time"] = nc.time.assign_attrs(long_name="time")
        nc = nc.fillna(0.0)
        return nc

    # --- ERSEM dependencies (ported from cfg_fabm.py's configure()) ---
    # No BAROTROPIC_3D constant-dependency branch here -- see this
    # function's own docstring for why (BAROCLINIC-only by design, so
    # sim.temp/sim.salt/sim.density already exist for real).
    sim.fabm.get_dependency("gelbstoff_absorption_satellite").set(
        pygetm.input.from_nc(fabm_folder / "ADY_gle.nc", "gelbstoff_absorption_satellite"),
        on_grid=False,
        climatology=True,
    )
    sim.fabm.get_dependency("mole_fraction_of_carbon_dioxide_in_air").set(400.0)

    emep_path = fabm_folder / "Ndep/AMM7-EMEP-NDeposition_y????.nc"
    sim.fabm.get_dependency("N3_flux/flux").set(
        pygetm.input.from_nc(str(emep_path), "N3_flux", preprocess=_add_coord)
    )
    sim.fabm.get_dependency("N4_flux/flux").set(
        pygetm.input.from_nc(str(emep_path), "N4_flux", preprocess=_add_coord)
    )

    # --- WOA/CMEMS-sourced FABM tracer initial conditions ---
    # Independent of the dependency setup above -- gated on this driver's
    # own boundaries.fabm role, NOT cfg_fabm.py's real cfg.hydrography.source
    # check (see this function's own docstring for why). SPONGE boundary
    # type + values are NOT set here -- oceanicu_providers.derive_data_
    # assignments emits them as plain data_assignments entries instead
    # (open_boundary.N3_n / open_boundary.N3_n.values, etc.), mirroring
    # boundaries.baroclinic's own WOA/CMEMS/CMIP6 branches there exactly,
    # since they're a straightforward 1:1 file read with no computation
    # needed (same meteo.py ERA5-vs-CMIP6 split reasoning: plain reads are
    # data_assignments, only genuinely computed values stay in a script).
    # Only the IC below stays here, because it needs a one-time
    # `.isel(time=imonth)` pick that data_assignments' climatology=True flag
    # can't express (that flag cycles all 12 months for the whole run, not
    # "pick one month once") -- exactly the same reason hydrography.py's own
    # T/S IC pick isn't a data_assignments entry either.
    #
    # CMIP6 is deliberately NOT accepted here -- mirrors hydrography.py's
    # own set_hydrography_ic, which only ever takes WOA/CMEMS for the IC
    # (`if source not in ("WOA", "CMEMS"): return`). CMIP6 delta-change
    # boundary output has no equivalent "monthly_ic" snapshot file
    # convention -- CMEMS's own IC below isn't its real time-series
    # boundary file either, it's a separate, purpose-built monthly-
    # climatology-shaped file (matches hydrography.py's own CMEMS IC
    # branch: so_2025_monthly_ic.nc/thetao_2025_monthly_ic.nc, NOT the
    # real time series boundaries.baroclinic.CMEMS reads for boundary
    # VALUES) -- so boundaries.fabm.CMEMS.tracers[*].file is expected to
    # point at that same kind of monthly-IC file, not the real time series
    # oceanicu_providers.derive_data_assignments reads for the boundary.
    boundaries_fabm_cfg = config.get("boundaries", {}).get("fabm") or {}
    if boundaries_fabm_cfg.get("source") in ("WOA", "CMEMS"):
        # Monthly-climatology index pick (.isel(time=imonth)) -- a ONE-TIME
        # initial value, not pygetm-config's own climatology:True
        # data_assignments flag (which cycles the whole 12-month pattern
        # for the entire run). configure_fabm's fixed (sim, domain, config)
        # signature has no imonth parameter, so it's derived from
        # runtime.time here exactly like hydrography.py's own
        # set_hydrography_ic does (that function's own CMEMS IC branch
        # ALSO uses isel(time=imonth), on its own separate monthly-IC file
        # -- same precedent this mirrors).
        time = config.get("runtime", {}).get("time")
        if time is None:
            raise RuntimeError(
                "configure_fabm's FABM tracer initial condition needs a real start "
                "time, but runtime.time isn't set anywhere -- pass --start explicitly (either "
                "when generating this script, or when running it)."
            )
        if isinstance(time, str):
            time = datetime.datetime.fromisoformat(time)
        imonth = time.month - 1

        ic_folder = Path(resolve_data_path(boundaries_fabm_cfg["folder"]))

        # Same tracer set as derive_data_assignments' boundary_type/values
        # entries (boundaries.fabm.<source>.tracers) -- defined once, used
        # for both, so the IC and boundary tracer lists can't drift apart.
        # A FABM state variable NOT listed here is simply never touched by
        # this loop -- it keeps whatever `initial_value` its own fabm.yaml
        # declares (standard FABM behavior when the host model doesn't
        # override it), and, on the boundary side, pygetm.tracer.Tracer.
        # __init__ already gives EVERY tracer -- FABM ones included --
        # ArrayOpenBoundaries(self, ZERO_GRADIENT) at construction time,
        # unconditionally, before any config runs. So an unlisted tracer
        # needs no explicit handling anywhere: it's ZERO_GRADIENT by
        # pygetm's own default, not by anything this driver has to arrange.
        for tracer, spec in (boundaries_fabm_cfg.get("tracers") or {}).items():
            sim[tracer].set(
                pygetm.input.from_nc(ic_folder / spec["file"], spec["variable"]).isel(time=imonth)
            )
