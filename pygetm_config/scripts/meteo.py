"""Meteo forcing pieces that genuinely need real Python, not a static
data_assignments entry (nse_from_oceanicu.yaml's meteo.<source>.data_script and
the post_data_script hook -- see oceanicu_providers.py). Verified against
cfg_airsea.py's own real data() -- see each function's own docstring.

Loaded via pygetm_config.providers.load_dotted_target ("path/to/file.py:name"),
never imported directly -- see nse_driver.py's own module docstring for how
that's wired (meteo.<source>.data_script/post_data_script defaults, both
pointing here).
"""

from __future__ import annotations


def set_meteo_data(sim, domain, config: dict) -> None:
    """Mirrors cfg_airsea.py's own data() -- the piece that would need real
    Python (not a static data_assignments entry): CMIP6's net shortwave/
    longwave, `swr = rsds - rsus`/`ql = rlds - rlus`, a subtraction of TWO
    separate files (pre_transform only supports a scale/offset on ONE
    file's value; pre_transform_expression is deliberately refused for
    security reasons).

    CURRENTLY A NO-OP (user-confirmed real data gap, 2026-08-06): the
    actual bias-corrected CMIP6 dataset this project uses
    (/data/BiasCorrected/CMIP6/<model>/<scenario>/meteo/, re-interpolated
    onto ERA5's own grid/calendar/hourly convention -- verified directly
    against a real sample file: latitude/longitude coords, calendar
    proleptic_gregorian, matching ERA5 exactly) provides evspsbl/huss/pr/
    psl/tas/uas/vas -- NO rsds/rsus/rlds/rlus (shortwave/longwave) and NO
    cloud cover at all. "Not been finally fixed yet" (user's own words) on
    the data-provider side -- not fabricated here. sim.airsea.swr/.ql are
    left to pygetm's own FluxesFromMeteo auto-compute (ROSATI_MIYAKODA/
    CLARK, the schema's own default shortwave_method/longwave_method),
    same as ERA5's own default path already does when it isn't given an
    explicit NET_FLUX/DOWNWARD_FLUX file either.

    Registered via meteo.data_script (see pygetm_config.loader.
    run_meteo_data_script) so the wiring is ready the moment real
    radiation/cloud-cover data exists -- until then this intentionally
    does nothing.
    """
    return


def set_sst_proxy(sim, domain, config: dict) -> None:
    """Barotropic runtypes (BAROTROPIC_2D/BAROTROPIC_3D) have no computed sea
    surface temperature to give pygetm's FluxesFromMeteo airsea
    implementation (which requires sst to be set regardless of runtype), so
    they use t2m (2m air temperature) as a stand-in. BAROCLINIC runs have a
    real, model-calculated SST and don't need this substitution at all.
    Verified directly against cfg_airsea.py's own data() function -- its
    comment there: "if not a baroclinic run use the t2m temperatures as
    proxy for SST" (`sim.sst = sim.airsea.t2m`). Without this, sim.start()
    crashes under a non-baroclinic runtype with `AssertionError: sst is
    masked`.

    Must run AFTER data_assignments (needs sim.airsea.t2m to already hold a
    real value, not just exist -- cfg_airsea.py's own version of this line
    lives right after its own t2m/d2m/u10/... assignments, not before them)
    -- registered via post_data_script (see pygetm_config.loader.
    run_post_data_script), not river_discharge.script (that hook runs
    before the simulation object even exists, too early for this).
    """
    import pygetm

    if sim.runtype < pygetm.RunType.BAROCLINIC:
        sim.sst = sim.airsea.t2m
