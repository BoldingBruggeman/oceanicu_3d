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
# _ndep_scenario_paths below) gets copied verbatim into the embedded code and executes
# on the target machine at call time -- a real, reproduced ModuleNotFoundError
# on a machine with pygetm installed but not pygetm_config (the whole point
# of --dump-python is a script that only needs the former).
from pathlib import Path

from pygetm_config.loader import expand_year_glob, resolve_data_path


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

    NOT YET VERIFIED against real NSe input files (AMM7-EMEP-style
    N-deposition netCDFs, gelbstoff/CDOM product, WOA tracer
    climatologies) -- cfg_fabm.py's own inputs are AMM-domain-specific;
    this port keeps the same dependency names/file-shape expectations,
    but NSe's own equivalent files need confirming before a real run.
    The N-deposition files themselves were rewritten 2026-09-23 to carry
    real `latitude`/`longitude` dimension coordinates directly (same
    convention as the fish/gelbstoff files below), so no runtime
    coordinate-reconstruction step (and no `mesh_mask.nc` dependency) is
    needed for them any more -- that used to live here as a nested
    `_add_coord` helper.
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

    # --- ERSEM dependencies (ported from cfg_fabm.py's configure()) ---
    # No BAROTROPIC_3D constant-dependency branch here -- see this
    # function's own docstring for why (BAROCLINIC-only by design, so
    # sim.temp/sim.salt/sim.density already exist for real).
    sim.fabm.get_dependency("gelbstoff_absorption_satellite").set(
        pygetm.input.from_nc(fabm_folder / "ADY_gle.nc", "gelbstoff_absorption_satellite"),
        on_grid=False,
        climatology=True,
    )
    # Atmospheric CO2 (2026-09-23): real, time-varying input4MIPs GHG
    # concentration pathway when boundaries.fabm is CMIP6-scenario-driven
    # -- reuses that role's own `scenario` rather than a second, separate
    # scenario field (deliberately coupled, not an oversight: the one real
    # use case here is a genuine CMIP6 future run, which wants atmosphere
    # and boundary tracers both matching the same scenario). Falls back to
    # the old flat 400.0 constant otherwise (WOA/CMEMS boundaries, or no
    # boundaries.fabm at all -- a near-term/historical run has no real
    # future scenario to pick a pathway for anyway).
    #
    # Source: input4MIPs GHGConcentrations -- the SAME file every CMIP6
    # model reads identically for a given SSP (real marker-scenario IAM
    # pairing: UoM-IMAGE-ssp126-1-2-1 / UoM-AIM-ssp370-1-2-1), confirmed
    # real and correctly scaled 2026-09-23: ssp126 reaches ~446 ppm by
    # 2100, ssp370 ~873 ppm -- both match the real, independently-known
    # values for these scenarios. `sector` dim (0/1/2) is global/NH/SH
    # mean -- confirmed sector=0 is GLOBAL directly: its 2015-01 value,
    # ~400 ppm, matches the real historical record, with sector 1/2
    # (NH/SH) both offset from it in the expected direction. Files fetched
    # via ocean_data.esgf_loader.ESGFLoader.search_input4mips (not a
    # per-run download -- these are tiny, static-per-scenario files, saved
    # once to ${GHG_CONCENTRATION_FOLDER}/co2_{scenario}.nc).
    # Shared by CO2/N2O below -- both read from an input4MIPs GHG
    # concentration file with the same Global/NH/SH `sector` dim. Index 1
    # is Northern Hemisphere -- NOT the global mean (index 0) this used to
    # read -- confirmed directly from the file's own `sector` coordinate
    # attrs (`ids: 0: Global; 1: Northern Hemisphere; 2: Southern
    # Hemisphere`), not just inferred from values. NSe is a Northern-
    # Hemisphere domain, so the NH sector is the physically correct choice
    # for both CO2 and N2O -- a regional model's air-sea gas flux should
    # see the atmospheric concentration actually overhead, not a planet-
    # wide average that's measurably offset from it (NH runs ~1% above
    # global for CO2, per the real 2015-01 values: NH 403.36 ppm vs.
    # global 399.99 ppm). Defined once, unconditionally, rather than
    # nested inside just the CO2 `if` block below: it's used by BOTH
    # dependencies gated on the SAME co2_scenario check, but a second,
    # independent-looking `if co2_scenario:` for N2O shouldn't have to
    # rely on Python's lack of block scoping (and a linter's own
    # "possibly unbound" flag) to stay correct if the two checks ever
    # diverge later.
    def _nh_sector(nc):
        return nc.isel(sector=1)

    fabm_cfg_boundaries = config.get("boundaries", {}).get("fabm") or {}
    co2_scenario = fabm_cfg_boundaries.get("scenario")
    # has_dependency guards this the same way N-deposition/fish/
    # fishing_pressure below do -- a fabm.yaml without ERSEM's carbonate
    # module (iswCO2 off, or a model without it at all) has no such
    # dependency to set at all; get_dependency on a genuinely-absent name
    # raises, so this must be checked, not assumed present just because
    # every fabm.yaml tested so far happens to have it on.
    if sim.fabm.has_dependency("mole_fraction_of_carbon_dioxide_in_air"):
        if co2_scenario:
            co2_folder = Path(resolve_data_path("${GHG_CONCENTRATION_FOLDER}"))
            co2_path = co2_folder / f"co2_{co2_scenario}.nc"

            sim.fabm.get_dependency("mole_fraction_of_carbon_dioxide_in_air").set(
                pygetm.input.from_nc(
                    str(co2_path), "mole_fraction_of_carbon_dioxide_in_air",
                    preprocess=_nh_sector,
                ),
                on_grid=False,
            )
            sim.logger.info(f"configure_fabm: providing mole_fraction_of_carbon_dioxide_in_air from {co2_path}")
        else:
            sim.fabm.get_dependency("mole_fraction_of_carbon_dioxide_in_air").set(400.0)

    # Atmospheric N2O (2026-09-23): a genuinely NEW dependency -- ERSEM's
    # real nitrous_oxide.F90 module (air-sea N2O flux, on by default via
    # its own iswN2O switch) registers partial_pressure_of_n2o, units
    # natm, with NO prior wiring anywhere in this project (unlike CO2,
    # which at least had a hardcoded constant). Same source/scenario-
    # coupling logic as CO2 above -- see that dependency's own comment
    # for the reasoning -- and NO real fallback value when boundaries.fabm
    # isn't CMIP6-scenario-driven, since (unlike CO2's pre-existing 400.0)
    # there was never a prior constant to fall back to; simply left unset
    # in that case, same as any FABM dependency this driver doesn't
    # provide (pyfabm's own error at initialize() makes an unset,
    # non-optional dependency loud and immediate, not silently wrong).
    #
    # Same input4MIPs source as CO2, same NH sector choice (sector=1, not
    # global) for the same reason -- confirmed real and correctly scaled
    # 2026-09-23: NH 2015-01 value is ~328.1 ppb vs. global ~327.8 ppb --
    # a much smaller NH/global spread than CO2's (N2O is longer-lived and
    # more evenly mixed), but the same NH value is still the physically
    # correct one to use for this domain. Units
    # conversion: input4MIPs reports mole fraction as ppb (units: 1.e-9,
    # i.e. the raw stored number IS the ppb value); ERSEM wants natm
    # (nanoatmospheres) -- numerically identical at ~1 atm total surface
    # pressure (1 ppb x 1 atm = 1e-9 atm = 1 natm), the same standard
    # approximation FABM's own test harness uses (environment.yaml's
    # partial_pressure_of_n2o: 335, the same order of magnitude as the
    # real ~328 ppb 2015 value) -- so the raw file value is used directly,
    # no scaling factor, same as CO2's own raw-ppm-number convention.
    # has_dependency guards this the same way CO2 above (and N-deposition/
    # fish/fishing_pressure below) do -- ERSEM's nitrous_oxide module
    # (iswN2O) may be off, or absent from a given fabm.yaml entirely, in
    # which case there is no such dependency to set. Per this function's
    # own earlier comment, still deliberately left UNSET (no call, no
    # constant fallback) when co2_scenario is falsy -- pyfabm's own
    # initialize()-time error is the intended signal for that case.
    if co2_scenario and sim.fabm.has_dependency("partial_pressure_of_n2o"):
        n2o_folder = Path(resolve_data_path("${GHG_CONCENTRATION_FOLDER}"))
        n2o_path = n2o_folder / f"n2o_{co2_scenario}.nc"
        sim.fabm.get_dependency("partial_pressure_of_n2o").set(
            pygetm.input.from_nc(
                str(n2o_path), "mole_fraction_of_nitrous_oxide_in_air",
                preprocess=_nh_sector,
            ),
            on_grid=False,
        )
        sim.logger.info(f"configure_fabm: providing partial_pressure_of_n2o from {n2o_path}")

    # N-deposition (2026-09-23): switched from the old 30-year climatology
    # (AMM7-EMEP-NDeposition_y1992..2021, no scenario, one file covers
    # every run regardless of period) to the new HPC-supplied, scenario-
    # aware dataset (AMM7_Ndep_BC-EMEP_{historical,ssp126,ssp370}_y{year},
    # historical 1993-2014 + each scenario's own 2015-2100 -- confirmed
    # real, different values from the old files for every overlapping
    # year, not a duplicate) -- but only when boundaries.fabm is actually
    # CMIP6-scenario-driven (the SAME co2_scenario used for CO2/N2O above,
    # deliberately coupled for the same reason: a real future run wants
    # atmosphere, boundary tracers, AND N-deposition all matching one
    # scenario). Falls back to the old unscenarioed file for a WOA/CMEMS-
    # boundaries run (or no boundaries.fabm at all), which has no
    # scenario to pick a new-dataset pathway from and never needs one --
    # its own period is always within the old dataset's 1992-2021
    # coverage.
    #
    # Both old and new files were rewritten 2026-09-23 to carry real
    # latitude(y)/longitude(x) dimension coordinates directly (same
    # convention as the fish/gelbstoff files below) -- backed up first to
    # /data/FABM/Ndep_backup_20260923_before_coordfix/ on bb-server1. No
    # preprocess/regridding-helper/mesh_mask.nc dependency needed any
    # more to read these, unlike before.
    #
    # Resolved to only the files this run's own [start, stop) actually
    # needs via expand_year_glob -- same reasoning and same historical/
    # scenario splicing shape as meteo.py's own _spliced_paths (see that
    # function's docstring): handing pygetm.input.from_nc a bare `????`
    # glob against this folder would also match every OTHER scenario's
    # files sitting in the same Ndep/ folder, and read far more of a
    # decades-long archive than any one run needs.
    NDEP_HIST_CUTOFF_YEAR = 2014  # this dataset's own historical/scenario split -- matches meteo.py's CMIP6 HIST_CUTOFF_YEAR

    runtime_cfg = config.get("runtime") or {}
    _start = runtime_cfg.get("time")
    _stop = runtime_cfg.get("stop")

    def _ndep_scenario_paths(scenario: str) -> list:
        if not _start or not _stop:
            pattern = str(fabm_folder / f"Ndep/AMM7_Ndep_BC-EMEP_{scenario}_y????.nc")
            return expand_year_glob(pattern, _start, _stop)

        start_year = datetime.datetime.fromisoformat(_start).year
        stop_year = datetime.datetime.fromisoformat(_stop).year

        paths = []
        if start_year <= NDEP_HIST_CUTOFF_YEAR:
            hist_stop = _stop if stop_year <= NDEP_HIST_CUTOFF_YEAR else f"{NDEP_HIST_CUTOFF_YEAR}-12-31"
            pattern = str(fabm_folder / "Ndep/AMM7_Ndep_BC-EMEP_historical_y????.nc")
            paths += expand_year_glob(pattern, _start, hist_stop)
        if stop_year > NDEP_HIST_CUTOFF_YEAR:
            scen_start = _start if start_year > NDEP_HIST_CUTOFF_YEAR else f"{NDEP_HIST_CUTOFF_YEAR + 1}-01-01"
            pattern = str(fabm_folder / f"Ndep/AMM7_Ndep_BC-EMEP_{scenario}_y????.nc")
            paths += expand_year_glob(pattern, scen_start, _stop)
        return paths

    if co2_scenario:
        ndep_paths = _ndep_scenario_paths(co2_scenario)
        if sim.fabm.has_dependency("N3_flux/flux"):
            sim.fabm.get_dependency("N3_flux/flux").set(
                pygetm.input.from_nc(ndep_paths, "N3_flux")
            )
        if sim.fabm.has_dependency("N4_flux/flux"):
            sim.fabm.get_dependency("N4_flux/flux").set(
                pygetm.input.from_nc(ndep_paths, "N4_flux")
            )
    else:
        emep_pattern = str(fabm_folder / "Ndep/AMM7-EMEP-NDeposition_y????.nc")
        emep_paths = expand_year_glob(emep_pattern, _start, _stop)
        if sim.fabm.has_dependency("N3_flux/flux"):
            sim.fabm.get_dependency("N3_flux/flux").set(
                pygetm.input.from_nc(emep_paths, "N3_flux")
            )
        if sim.fabm.has_dependency("N4_flux/flux"):
            sim.fabm.get_dependency("N4_flux/flux").set(
                pygetm.input.from_nc(emep_paths, "N4_flux")
            )

    # Fish/fishing-pressure dependency (2026-09-23): ported from Ricardo's
    # manual HPC additions (bb-server1:/tmp/generated_nse_cmems_utils.py,
    # written directly into a --dump-python'd script rather than this
    # source file -- folded back in here so it survives the next
    # regeneration instead of needing to be re-applied by hand each time).
    # Only relevant to a fabm.yaml that actually includes the mizer/fish
    # size-spectrum model (fabm_mizer.yaml here, not fabm_ersem.yaml) --
    # has_dependency guards this the same way the N-deposition
    # dependencies above now do.
    if sim.fabm.has_dependency("fish/fishing_pressure"):
        fish_path = fabm_folder / "fish/fishing_effort_????_AMM7.nc"
        sim.fabm.get_dependency("fish/fishing_pressure").set(
            pygetm.input.from_nc(str(fish_path), "tot_EffActiveHours")
        )
        sim.logger.info(f"configure_fabm: providing fish/fishing_pressure from {fish_path}")

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
