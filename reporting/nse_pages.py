"""OceanICU reporting plugin for ocean-post (``reporting --page-plugin``).

NSe-specific website content that does not belong in the generic ocean-post
package: the NSe future-boundary-conditions page (content/areas/
nse-boundaries.md) and the raw-CMIP6 section of the meteo scenarios page.
regenerate_hugo.py passes this file to ocean-post's reporting CLI.

Hooks (all optional, see ocean_post.reporting):
    area_pages(manager, hugo_root)                -> None
    scenarios_meteo_section(manager, hugo_root)   -> str (Markdown)
"""
import logging
import shutil
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

log = logging.getLogger(__name__)


def area_pages(manager, hugo_root: Path) -> None:
    """Write the NSe boundaries page when its diagnostics exist."""
    _NSePages(manager)._generate_nse_boundaries_page(hugo_root)


def scenarios_meteo_section(manager, hugo_root: Path) -> str:
    """The raw-CMIP6 part of the meteo scenarios page."""
    return _NSePages(manager)._generate_raw_cmip6_section(hugo_root)


class _NSePages:
    """The page code, as it was in ocean-post's ReportingScenariosMixin."""

    def __init__(self, manager) -> None:
        self.setups_dir = manager.setups_dir
        self.verbose = getattr(manager, 'verbose', False)

    def _generate_nse_boundaries_page(self, hugo_root: Path) -> Optional[str]:
        """Generate content/areas/nse-boundaries.md from bdy-diagnostics'
        output (``<analyses>/areas/NSe/boundaries/<var>/{plots,tables}``).

        NSe-specific for now (the only area with future boundary
        diagnostics); written as its own page rather than folded into the
        generic area page since it summarises per-model/per-scenario
        boundary forcing, not per-experiment validation results.

        Returns the page's URL path if written, else ``None`` (no
        boundary-diagnostics output yet -- the page is simply omitted,
        and _generate_area_index's link is conditioned on the file's
        existence).
        """
        if self.setups_dir is None:
            return None
        src = self.setups_dir / 'areas' / 'NSe' / 'boundaries'
        if not src.is_dir():
            return None

        DELTA_VARS = ['thetao', 'so']
        TIDAL_VARS = ['zos', 'uo', 'vo']
        VAR_LABELS = {
            'thetao': 'Temperature (thetao)',
            'so':     'Salinity (so)',
            'zos':    'Sea Surface Height (zos)',
            'uo':     'Eastward Velocity (uo)',
            'vo':     'Northward Velocity (vo)',
        }

        content_dir = hugo_root / 'content' / 'areas'
        static_dir  = hugo_root / 'static'  / 'areas' / 'nse-boundaries'
        content_dir.mkdir(parents=True, exist_ok=True)

        sections: List[str] = []
        any_var = False

        for group_label, var_list in (
            ('Delta-change (temperature / salinity)', DELTA_VARS),
            ('Tidal + CMIP6-mean (elevation / currents)', TIDAL_VARS),
        ):
            group_lines: List[str] = []
            for var in var_list:
                var_dir = src / var
                plot  = var_dir / 'plots'  / f'{var}_period_summary.png'
                table = var_dir / 'tables' / f'{var}_statistics.yaml'
                if not plot.exists() or not table.exists():
                    continue

                if not any_var:
                    if static_dir.exists():
                        shutil.rmtree(static_dir)
                    static_dir.mkdir(parents=True)
                any_var = True

                shutil.copyfile(plot, static_dir / plot.name)

                try:
                    doc = yaml.safe_load(table.read_text()) or {}
                except Exception:
                    doc = {}
                src_attrs = doc.get('source_attrs', {})

                group_lines.append(f"### {VAR_LABELS.get(var, var)}\n")
                if src_attrs.get('source_datasets'):
                    group_lines.append(f"*Source: {src_attrs['source_datasets']}*\n")
                for attr_key, label in (
                    ('delta_method', 'Method'), ('delta_hist_period', 'Historical period'),
                    ('delta_future_period', 'Future period'),
                ):
                    if src_attrs.get(attr_key):
                        group_lines.append(f"- **{label}:** {src_attrs[attr_key]}")
                if any(src_attrs.get(k) for k in
                       ('delta_method', 'delta_hist_period', 'delta_future_period')):
                    group_lines.append("")
                group_lines.append(
                    f"![{var} period summary](/areas/nse-boundaries/{plot.name})\n"
                )

                # CMEMS-vs-CMIP6-historical validation plot: thetao/so only (DELTA_VARS) --
                # CMEMS sea level and transports include tides, CMIP6-historical is the
                # mean state only. Written by physics_cmip6_vs_cmems_v1.py into the same
                # <var>/plots/ directory as bdy-diagnostics.
                if var in DELTA_VARS:
                    cmems_plot = var_dir / 'plots' / f'{var}_cmems_vs_cmip6_historical.png'
                    if cmems_plot.exists():
                        shutil.copyfile(cmems_plot, static_dir / cmems_plot.name)
                        group_lines.append(
                            "**Validation: CMEMS reference vs CMIP6-historical climatology** "
                            "(1985-2014) at the same boundary points -- confirms the bias-"
                            "correction's own historical baseline is sound before trusting the "
                            "future delta above.\n"
                        )
                        group_lines.append(
                            f"![{var} CMEMS vs CMIP6 historical](/areas/nse-boundaries/{cmems_plot.name})\n"
                        )
            if group_lines:
                sections.append(f"## {group_label}\n\n" + '\n'.join(group_lines))

        # Biogeochemistry feasibility check: validation plots (CMEMS NWS BGC
        # reference climatology vs CMIP6-historical climatology at the boundary
        # points), not a boundary condition -- evidence for whether delta change
        # suits bio, given CMIP6 ESM biogeochemistry is much simpler than the CMEMS
        # regional product. Independent of the delta/tidal groups above.
        BIO_VARS = ['no3', 'po4', 'o2', 'ph', 'si']
        BIO_LABELS = {
            'no3': 'Nitrate (no3)', 'po4': 'Phosphate (po4)', 'o2': 'Oxygen (o2)',
            'ph': 'pH', 'si': 'Silicate (si)',
        }
        # The directory is 'bio_feasibility' (no '_check' suffix).
        bio_src = self.setups_dir / 'areas' / 'NSe' / 'bio_feasibility'
        bio_lines: List[str] = []
        if bio_src.is_dir():
            bio_static_dir = hugo_root / 'static' / 'areas' / 'nse-boundaries' / 'bio_feasibility'
            for var in BIO_VARS:
                plot = bio_src / f'{var}_cmems_vs_cmip6_historical.png'
                if not plot.exists():
                    continue
                if not bio_lines:
                    if bio_static_dir.exists():
                        shutil.rmtree(bio_static_dir)
                    bio_static_dir.mkdir(parents=True)
                shutil.copyfile(plot, bio_static_dir / plot.name)
                bio_lines.append(f"### {BIO_LABELS.get(var, var)}\n")
                bio_lines.append(
                    f"![{var} CMEMS vs CMIP6 historical](/areas/nse-boundaries/bio_feasibility/{plot.name})\n"
                )
        if bio_lines:
            any_var = True
            sections.append(
                "## Biogeochemistry — feasibility check (not a boundary condition)\n\n"
                "Surface, boundary-mean monthly climatology: the real CMEMS NWS BGC "
                "reference (black) against CMIP6-historical (1985-2014) climatology "
                "from the same 3 models used for physics, sampled at the identical "
                "boundary points -- a diagnostic check, not a correction. Nutrients "
                "(no3, po4, o2) broadly track the reference's seasonal shape with "
                "model-dependent offsets (MPI-ESM1-2-HR consistently low, especially "
                "in summer); si shows the same shape but a wider spread. pH is the "
                "clear outlier: GFDL-ESM4 sits ~0.5 units below the reference "
                "essentially year-round (large enough to suggest a scale/convention "
                "mismatch worth checking on its own, not just model bias), and none "
                "of the three models reproduce the reference's spring/summer pH rise "
                "at all.\n\n"
                + '\n'.join(bio_lines)
            )

        # Alkalinity / DIC / ammonium coverage: unlike BIO_VARS, none has a CMEMS
        # reanalysis (MY) product in the region. Static prose (a decision record,
        # no plots), shown whenever any other section exists.
        sections.append(
            "## Alkalinity, DIC, and ammonium — coverage investigation\n\n"
            "Extending delta-change to alkalinity (`talk`), dissolved "
            "inorganic carbon (`dissic`), and ammonium (`nh4`) hit a harder "
            "constraint than the five variables above: none of the three "
            "have a CMEMS **reanalysis** (multi-year, MY) product anywhere "
            "in the region.\n\n"
            "| variable | NWS MY | Baltic MY/ANFC | Global MY | NWS ANFC |\n"
            "|---|---|---|---|---|\n"
            "| `talk` | ✗ (never produced) | ✗ (no source at all) | ✗ | ✓ from 2024-07-29 |\n"
            "| `dissic` | ✗ (never produced) | ✓ (ANFC only) | ✗ | ✓ from 2024-07-29 |\n"
            "| `nh4` | ✗ | ✓ (MY *and* ANFC) | ✗ | ✓ from 2024-07-29 |\n\n"
            "For `talk`/`dissic`, the earliest real CMEMS data of any kind "
            "is the NWS analysis-and-forecast (ANFC) product, confirmed via "
            "a live dry-run against the real catalogue to start 2024-07-29 "
            "(an older config comment, written for AMM7, had claimed "
            "2024-02-28) -- a ~2-year window, not a multi-decade "
            "reanalysis. This doesn't block delta-change the way it first "
            "appeared to: the CMIP6-vs-CMIP6 trend calculation (the actual "
            "future *signal*) only ever depends on CMIP6's own historical "
            "run (1985-2014) and future run, never on CMEMS. CMEMS's only "
            "role is supplying realistic day-to-day/seasonal cycling "
            "values via analog-date repetition (`t_analog`) -- which works "
            "with a short window, just with less sampled interannual "
            "variability than the 16-year window used for temperature/"
            "salinity. Decision: proceed using the real ANFC-only window "
            "as that reference for `talk`/`dissic`.\n\n"
            "`nh4` has real CMEMS data (Baltic MY and ANFC both have it, "
            "NWS ANFC has it too) but is blocked by a different, genuine "
            "gap: some boundary segments have a source for a given period "
            "and others don't, and the CMEMS extraction tool "
            "(`cmems_processor.py`) has no NaN-padding support for that "
            "mixed-coverage case yet -- a tooling gap, not a data-"
            "availability one. Deferred for now.\n\n"
            "**Two ways to get a future signal.** Delta-change (used above "
            "for `thetao`/`so`, and now `talk`/`dissic`) keeps CMEMS's own "
            "real regional variability as the base and adds only the "
            "CMIP6-derived *mean* climate-change signal on top -- "
            "appropriate because CMIP6's own coarse ocean resolution has "
            "essentially no realistic regional structure worth preserving "
            "even after bias-correcting it directly. That reasoning is "
            "much weaker for `talk`/`dissic` specifically, given CMEMS "
            "itself has almost no real regional variability on record for "
            "either -- direct CMIP6 bias-correction (the same method "
            "already used for atmospheric meteo forcing) was built and run "
            "alongside delta-change for exactly this reason (see the "
            "per-segment comparison below): both land close to the real "
            "CMEMS reference, so no further decision was forced between "
            "them -- the raw (uncorrected) CMIP6 signal is what actually "
            "needed correcting, and both methods do that.\n\n"
            "**Naive vs. honest CMEMS request, tested directly:** two "
            "variants of the extraction were run side by side -- one "
            "requesting the same full 2010-2026 window as every other "
            "boundary variable with no awareness of the ANFC-only "
            "limitation, one explicitly bounded to the real ANFC coverage. "
            "Result: byte-for-byte identical output (same 750-timestep "
            "time axis, same values, same file size) -- the extraction "
            "tool already clips silently to whatever the product actually "
            "has, with no NaN-padding for the unavailable 2010-2024 span "
            "either way. Kept both entries in the source config "
            "regardless, since documenting *why* the window is short is "
            "clearer written down explicitly than left implicit."
        )
        any_var = True

        # Per-segment, multi-method bio boundary comparison for all 6 ERSEM
        # boundary variables, all 3 models x 2 scenarios: bdy_diagnostics.py's
        # run_bio() output in <analyses>/areas/NSe/boundaries_bio/<var>/{plots,
        # tables} (a sibling of boundaries/, like bio_feasibility/). Delta change
        # and direct bias correction side by side.
        BIO_CMP_VARS = ['no3', 'po4', 'si', 'o2', 'dissic', 'talk']
        BIO_CMP_LABELS = {
            'no3': 'Nitrate (no3)', 'po4': 'Phosphate (po4)', 'si': 'Silicate (si)',
            'o2': 'Oxygen (o2)', 'dissic': 'Dissolved Inorganic Carbon (dissic)',
            'talk': 'Alkalinity (talk)',
        }
        BIO_CMP_METHOD_LABELS = {
            'rawcmip6': 'raw CMIP6 (uncorrected)',
            'deltachange': 'delta-change',
            'directbc': 'direct bias-correction',
        }
        BIO_CMP_MODELS = ['GFDL-ESM4', 'CNRM-ESM2-1', 'MPI-ESM1-2-HR']
        BIO_CMP_SCENARIOS = ['ssp126', 'ssp370']
        bio_cmp_src = self.setups_dir / 'areas' / 'NSe' / 'boundaries_bio'
        bio_cmp_lines: List[str] = []
        if bio_cmp_src.is_dir():
            bio_cmp_static_dir = hugo_root / 'static' / 'areas' / 'nse-boundaries' / 'boundaries_bio'
            for var in BIO_CMP_VARS:
                var_lines: List[str] = []

                # Primary view: one plot per variable, 3 models x 2 scenarios overlaid,
                # delta change only (bdy_diagnostics.py's run_bio_all_combos()) -- how
                # consistent the CMIP6 models are with each other.
                all_combos_plot = bio_cmp_src / var / 'plots' / f'{var}_all_combos_segments.png'
                if all_combos_plot.exists():
                    if not bio_cmp_lines and not var_lines:
                        if bio_cmp_static_dir.exists():
                            shutil.rmtree(bio_cmp_static_dir)
                        bio_cmp_static_dir.mkdir(parents=True)
                    shutil.copyfile(all_combos_plot, bio_cmp_static_dir / all_combos_plot.name)
                    var_lines.append(
                        f"![{var} all models/scenarios comparison](/areas/nse-boundaries/boundaries_bio/{all_combos_plot.name})\n"
                    )

                # Secondary view: per-combo, multi-method breakdown (raw
                # CMIP6 / delta-change / direct bias-correction) -- shows
                # how much correction actually changed for one model/
                # scenario at a time, which the combined plot above can't
                # (it's delta-change only, to keep 6+ lines per subplot
                # readable).
                per_combo_lines: List[str] = []
                for model in BIO_CMP_MODELS:
                    for scenario in BIO_CMP_SCENARIOS:
                        plot = bio_cmp_src / var / 'plots' / f'{var}_{model}_{scenario}_segments.png'
                        table = bio_cmp_src / var / 'tables' / f'{var}_{model}_{scenario}_segment_statistics.yaml'
                        if not plot.exists():
                            continue
                        if not bio_cmp_lines and not var_lines and not per_combo_lines:
                            if bio_cmp_static_dir.exists():
                                shutil.rmtree(bio_cmp_static_dir)
                            bio_cmp_static_dir.mkdir(parents=True)
                        shutil.copyfile(plot, bio_cmp_static_dir / plot.name)
                        try:
                            doc = yaml.safe_load(table.read_text()) or {}
                        except Exception:
                            doc = {}
                        methods = doc.get('methods_present', [])
                        method_str = ', '.join(BIO_CMP_METHOD_LABELS.get(m, m) for m in methods)
                        per_combo_lines.append(f"#### {model} / {scenario}\n")
                        if method_str:
                            per_combo_lines.append(f"*Methods compared: {method_str} (plus the real CMEMS reference where its coverage overlaps)*\n")
                        per_combo_lines.append(
                            f"![{var} {model} {scenario} per-segment method comparison](/areas/nse-boundaries/boundaries_bio/{plot.name})\n"
                        )
                if per_combo_lines:
                    var_lines.append("<details>\n<summary>Per-model/scenario method breakdown "
                                      "(raw CMIP6 vs. delta-change vs. direct bias-correction)</summary>\n")
                    var_lines.extend(per_combo_lines)
                    var_lines.append("</details>\n")

                if var_lines:
                    bio_cmp_lines.append(f"### {BIO_CMP_LABELS.get(var, var)}\n")
                    bio_cmp_lines.extend(var_lines)
        if bio_cmp_lines:
            sections.append(
                "## Bio boundary generation: per-segment method comparison\n\n"
                "Period-mean per boundary segment (9 segments). The main "
                "plot under each variable overlays all three CMIP6 models "
                "x both scenarios (delta-change, the actual production "
                "method) so the models can be compared against each other "
                "directly; expand \"per-model/scenario method breakdown\" "
                "below it for the raw-CMIP6-vs-corrected comparison for "
                "one combo at a time. The raw CMIP6 line there is "
                "converted from its native `mol m-3` to `mmol m-3` here for "
                "comparison only (the real raw-CMIP6 files themselves stay "
                "unconverted and correctly labeled). **Segment 7 (lon "
                "~12.98°E, lat 54.5-55.25°N -- the Baltic-entrance corner, "
                "the Fehmarnbelt/Great Belt) was entirely NaN for `dissic`/"
                "`talk` in both delta-change and direct bias-correction "
                "until 2026-09-22 -- now fixed, two different ways. "
                "`no3`/`po4`/`si`/`o2` were already real CMEMS data here "
                "(a pre-existing per-segment override to a Baltic BGC "
                "product, since the long-standing NWS `bio_daily` product "
                "simply doesn't reach this segment -- same mechanism "
                "already used for temperature/salinity/currents at this "
                "exact segment, a Baltic ocean model, since NWS is masked "
                "across the whole Kattegat there). `dissic` got the same "
                "Baltic-product override, newly added. `talk` has no CMEMS "
                "source anywhere, Baltic included (confirmed against the "
                "live catalogue) -- instead derived from real Baltic pH + "
                "DIC + temperature/salinity through actual seawater "
                "carbonate-system equilibrium (PyCO2SYS), not a regression "
                "or a raw-CMIP6 fallback. See `cli/derive_baltic_talk.py`. "
                "Every other segment was already real, direct CMEMS data "
                "and is untouched.\n\n"
                + '\n'.join(bio_cmp_lines)
            )
            any_var = True

        # Bio boundary corrections: static prose recording the fixes and their
        # verification numbers (no plots), shown whenever any other section
        # exists.
        sections.append(
            "## Bio boundary generation: 2026-09-22 bug-fix campaign\n\n"
            "A user question about an odd-looking plot (\"why does the "
            "future signal look 3x the historical one?\") led to a single "
            "day's debugging that found and fixed five independent, real "
            "bugs in the bio boundary pipeline -- every one would otherwise "
            "have silently shipped wrong production forcing:\n\n"
            "1. **Diagnostics-only depth bug.** The per-segment averaging "
            "used to make the comparison plots above checked for a `depth` "
            "dimension by that exact name; raw CMIP6 files use `lev` "
            "instead, so the averaging silently fell back to the whole "
            "water column instead of the surface for the raw-CMIP6 line "
            "only -- this alone produced the apparent \"factor of 3\" jump "
            "between the historical and future `no3` plots that first "
            "prompted this investigation. Diagnostics-only: it never "
            "touched any generated boundary file.\n"
            "2. **A real ~1000x-too-small climate-change signal.** CMIP6's "
            "native units for all six bio variables are `mol m-3`; the "
            "CMEMS reference is `mmol m-3`. The delta-change generator's "
            "shared CMIP6 loader had no unit conversion, so the imported "
            "climate-change signal was ~1000x too small for every bio "
            "variable, every model, every file this method had ever "
            "produced. Fixed at the point CMIP6 data is loaded.\n"
            "3. **A longitude-wraparound bug, regular-grid models only.** "
            "NSe's boundary mixes mostly-negative longitudes (segments "
            "0-6/8, -7.5° to +4.74°) with segment 7 alone at +12.98°E. For "
            "GFDL-ESM4 (the one CMIP6 model here on a regular lon/lat "
            "grid), a bounding-box helper in the shared spatial-"
            "interpolation library mis-measured that point set's span as "
            "~359° instead of the true ~13°, corrupting the interpolated "
            "delta for the whole 310-point set. Worked around per-segment "
            "in the boundary generator itself, given the size of what else "
            "depends on the shared library. CNRM-ESM2-1/MPI-ESM1-2-HR use "
            "a curvilinear grid and a different code path, unaffected.\n"
            "4. **A stale disk-cached climatology.** The CMIP6 historical-"
            "baseline climatology is cached to disk (recomputing it on "
            "every run had turned out to be the real bottleneck, not the "
            "boundary-point interpolation). Two models' `talk`/`dissic` "
            "caches had been written minutes before fix #2 landed, so they "
            "kept the old, unconverted values -- against the now-correct "
            "future values, that made the climate-change delta come out "
            "close to the full future absolute value, roughly doubling the "
            "final output. Fixed by clearing the affected cache entries "
            "and regenerating.\n"
            "5. **Additive delta-change has no positivity floor.** `no3` "
            "came back with a small but real fraction of negative values "
            "per segment (up to ~9%), concentrated where the CMEMS "
            "reference is already near its seasonal bloom-depletion "
            "minimum -- a structural limitation of an *additive* delta "
            "(reference + climate-change shift), not a numerical bug. "
            "Switched `no3`/`po4`/`si`/`o2`/`dissic`/`talk` to a "
            "*multiplicative* delta (reference × ratio) instead, which is "
            "non-negative by construction given a non-negative reference "
            "-- reusing the same additive-vs-multiplicative variable table "
            "the direct bias-correction method already used, for the same "
            "reason. This also fixed an unrelated-looking anomaly as a "
            "side effect: CNRM-ESM2-1's `dissic` at segment 7 had come out "
            "physically implausible for the ssp126 scenario only (a smooth "
            "but wrong ~42→110 mmol/m³ trend against a real ~1649 mmol/m³ "
            "reference), traced to the additive method's sensitivity to "
            "one CMIP6 grid cell's absolute value; the multiplicative form "
            "isn't sensitive to that and produces a smooth, plausible "
            "~1680→1739 mmol/m³ instead.\n\n"
            "The multiplicative switch introduced a smaller failure mode "
            "of its own: dividing by a small-but-nonzero historical "
            "baseline at one month/point can inflate the ratio past "
            "what's physically sensible. Confirmed for `no3`, at segment 7 "
            "(the Baltic-entrance/Fehmarnbelt corner), winter months only, "
            "across all three models. The evaluated ratio is now capped "
            "to [1/5, 5] before being applied, which removed every case "
            "actually caused by ratio instability. A separate, larger set "
            "of high values at that same segment turned out to be real, "
            "not an artefact: the CMEMS reference itself reaches similarly "
            "large nitrate values there on record (a real, river-"
            "influenced, short-duration signal -- up to ~94 mmol/m³ in the "
            "pure, uncorrected reference) -- kept as-is rather than "
            "clipped further, since a hard ceiling there would suppress "
            "genuine reference-data behaviour, not a bug.\n\n"
            "**Final verified state, all three CMIP6 models × both "
            "scenarios × all six variables** (delta-change method; mean / "
            "max in mmol m⁻³ across the whole 2015-2100 boundary, dissic/"
            "talk given as their boundary-mean at segment 0 for brevity; "
            "zero unexplained negative values anywhere -- the handful that "
            "do occur are ~-0.01 mmol/m³ `o2` values at segment 7's deep "
            "layers, real reanalysis noise at the edge of the Baltic's "
            "genuinely near-anoxic bottom water, correctly floored to 0):\n\n"
            "| | GFDL-ESM4<br>ssp126 | GFDL-ESM4<br>ssp370 | CNRM-ESM2-1<br>ssp126 | CNRM-ESM2-1<br>ssp370 | MPI-ESM1-2-HR<br>ssp126 | MPI-ESM1-2-HR<br>ssp370 |\n"
            "|---|---|---|---|---|---|---|\n"
            "| no3 | 5.4 / 71.5 | 5.8 / 79.3 | 5.8 / 90.5 | 5.8 / 105.0 | 4.7 / 97.9 | 4.5 / 97.7 |\n"
            "| po4 | 0.35 / 11.4 | 0.35 / 11.8 | 0.39 / 14.0 | 0.40 / 16.3 | 0.31 / 11.4 | 0.30 / 11.1 |\n"
            "| si | 4.1 / 82.4 | 4.3 / 76.7 | 4.8 / 102.2 | 4.9 / 99.9 | 3.6 / 89.4 | 3.6 / 96.3 |\n"
            "| o2 | 256.5 / 573.0 | 253.2 / 570.5 | 258.8 / 578.3 | 254.9 / 571.8 | 256.1 / 569.8 | 254.9 / 568.4 |\n"
            "| dissic (seg 0) | 2244 | 2332 | 2216 | 2303 | 2188 | 2255 |\n"
            "| talk (seg 0) | 2403 | 2402 | 2367 | 2360 | 2338 | 2312 |\n\n"
            "CNRM-ESM2-1's `no3` shows a noticeably stronger, monotonically-"
            "growing correction toward the end of the century -- the ratio "
            "cap engages on a growing fraction of month/points, from ~3% "
            "by 2075 to ~8.5% by 2100 -- than GFDL-ESM4 or MPI-ESM1-2-HR, "
            "whose correction stays small and roughly constant across the "
            "run. Likely a real difference in projected nutrient-cycling "
            "trend strength between the three CMIP6 Earth-system models, "
            "though with a single ensemble member each this can't be "
            "fully separated from internal variability.\n\n"
            "A dedicated automated check (`final-boundary-check`) "
            "confirmed every one of the 36 delta-change files above passes "
            "cleanly. It also found that the raw, uncorrected CMIP6 "
            "boundary files (kept only as a diagnostic reference here, "
            "never used as real production forcing) still carry the "
            "unconverted-units bug described above, unfixed -- since "
            "nothing downstream actually reads that method's output as a "
            "boundary condition, this is tracked as a known limitation of "
            "that reference-only path rather than fixed immediately."
        )
        any_var = True

        if not any_var:
            return None

        frontmatter = {
            'title':   'NSe — Future Boundary Conditions',
            'slug':    'nse-boundaries',
            'date':    date.today().isoformat(),
            'ShowToc': True,
            'TocOpen': False,
            'draft':   False,
        }
        content = (
            f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n"
            "Cross-model, cross-scenario summary of NSe's future open-boundary "
            "conditions: delta-change temperature/salinity (CMIP6, bias-corrected "
            "against the CMEMS Northwest Shelf reanalysis), tidal elevation/"
            "currents (TPXO9 plus the CMIP6 ensemble-mean sea-level trend), and "
            "(as of 2026-09-22) delta-change biogeochemistry -- nitrate, "
            "phosphate, silicate, oxygen, dissolved inorganic carbon, and "
            "alkalinity -- across all three CMIP6 models and both scenarios "
            "(see the bug-fix campaign section below for how that came "
            "together).\n\n"
            "*This page is the scientific comparison -- per-model/per-scenario "
            "plots and the bug-fix history behind them. For an automated "
            "pass/fail sanity check of this same bio boundary data (plausible "
            "ranges, cross-model consistency, storage encoding), see the "
            "[NSe Bio Boundary QA Report](/guides/boundary-final-check-report/) "
            "instead.*\n\n"
            + '\n\n'.join(sections) + '\n'
        )

        page_path = content_dir / 'nse-boundaries.md'
        page_path.write_text(content)
        log.info("Wrote NSe boundaries page: %s", page_path)
        return '/areas/nse-boundaries/'

    # Raw CMIP6 archive root (ocean-data's DataLoader, fetched via Pangeo/
    # ESGF -- NOT bias-corrected). This class already runs on the relay
    # host (bb-server1, see deploy_ghpages.py/deploy_hosts.yaml -- content
    # generation always happens there so building it there too needs no
    # data sync), which has direct filesystem access to this path, same
    # basis as CMIP6_RAW_FOLDER in OceanICU/oceanicu_3d's own
    # machines.yaml/data_roots.yaml.example.
    _RAW_CMIP6_ROOT = Path('/data/CMIP6')

    # variable_id -> the actual stored data-variable name in a raw-fetched
    # file, wherever it differs from variable_id itself. Previously rsds/
    # rsus/pr came back renamed (to a shared 'sw', and to 'precip'
    # respectively) by ocean-data's own CMIP6 normalisation --
    # name_mappings.py's CF-attribute-based standardize_variable_names,
    # meant for OCEAN CMIP6 variables (thetao/so/tos -> temp/salt/sst)
    # matching on generic CF standard_name/long_name substrings, which
    # unintentionally also caught these atmospheric ones. Fixed at the
    # source (ocean-data's data_loader.py:_normalise_cmip6_conventions now
    # skips that rename for known atmospheric variable_ids) and the 9
    # already-fetched files affected were repaired in place (ncrename) --
    # every raw-fetched file now keeps its own real CMIP6 variable_id, so
    # this mapping is empty. Kept as an explicit empty dict (not deleted
    # outright), same reasoning as OceanICU/oceanicu_3d's own
    # driver/scripts/meteo.py:set_meteo_data (_RAW_STORED_VARNAME there,
    # kept in sync by hand -- two separate repos, no shared dependency to
    # hang a single definition off of): a regression in that upstream fix
    # then surfaces as a clear KeyError reading the wrong variable name,
    # not a silent wrong-file read.
    _RAW_CMIP6_STORED_NAME: dict = {}

    # Model/scenario/variable combinations present under _RAW_CMIP6_ROOT:
    # GFDL-ESM4, all 10 core variables, historical (1990-2014) and ssp126/ssp370
    # (2015-2099). MPI-ESM1-2-HR/CNRM-ESM2-1 are not fetched (see
    # _RAW_CMIP6_3HR_AVAILABILITY for what could be).
    _RAW_CMIP6_FETCHED_MODELS = ('GFDL-ESM4',)

    # 3-hourly SOURCE availability (Pangeo union ESGF, checked directly
    # against both catalogs) for the two other core models -- an
    # inventory of what COULD be fetched the same way GFDL-ESM4 already
    # has been, not what IS fetched (see _RAW_CMIP6_FETCHED_MODELS).
    # GFDL-ESM4 itself has all 10 at 3-hourly for every period (already
    # fetched, omitted here). MPI-ESM1-2-HR's own availability is
    # identical across historical/ssp126/ssp370 (checked all three
    # separately); CNRM-ESM2-1's is NOT -- its historical run has all 10,
    # but ssp126 and especially ssp370 are considerably sparser (the
    # ScenarioMIP-vs-CMIP experiment classes are published independently
    # per model, and CNRM's own ScenarioMIP submission covers far fewer
    # 3-hourly variables than its CMIP/historical one does).
    _RAW_CMIP6_3HR_AVAILABILITY = {
        'MPI-ESM1-2-HR': {
            'historical': {'tas', 'pr', 'uas', 'vas', 'huss'},
            'ssp126':     {'tas', 'pr', 'uas', 'vas', 'huss'},
            'ssp370':     {'tas', 'pr', 'uas', 'vas', 'huss'},
        },
        'CNRM-ESM2-1': {
            'historical': {'tas', 'pr', 'uas', 'vas', 'huss', 'rsds', 'rlds', 'rsus', 'rlus', 'ps'},
            'ssp126':     {'tas', 'pr', 'huss', 'rsds', 'rlds'},
            'ssp370':     {'tas', 'pr', 'huss'},
        },
    }

    def _raw_cmip6_file(self, model: str, experiment: str, var: str) -> Optional[Path]:
        """One whole-period file for *var* under *experiment* (historical or
        an SSP scenario), or None if it hasn't been fetched. Matched with a
        `*` wildcard, not a fixed suffix -- the real fetched files carry a
        fetch-method tag ('_onfly'/'_nostream', an ocean-data implementation
        detail, not a stable naming convention), same reasoning as
        OceanICU/oceanicu_3d's own driver/scripts/meteo.py:set_meteo_data
        (_raw_paths there).
        """
        matches = sorted(
            (self._RAW_CMIP6_ROOT / model / experiment).glob(f'{var}_3hr_{experiment}_*.nc')
        )
        return matches[0] if matches else None

    def _raw_cmip6_decadal_series(self, model: str, var: str) -> Tuple[dict, dict, dict]:
        """Decadal-mean, domain-averaged time series for *var*, split into
        (historical, ssp126, ssp370) dicts of {decade_start_year: mean_value}.
        An experiment with no fetched file yet contributes an empty dict,
        not an error -- lets the caller plot whatever subset exists.

        Two differences from a plain groupby mean:

        1. cos(latitude) area weighting: the domain spans 45.5-74.5degN
           (cos 0.70 to 0.27), so an unweighted mean over-weights the northern
           rows ~2.6x.
        2. Partial decades are dropped: historical ends 2014 and the scenarios
           start 2015, so the 2010 bucket holds half a decade on either side and
           is noisier right at the splice. The remaining decade-to-decade
           variation (wind, pressure, precipitation) is internal variability of a
           single realisation and is not smoothed.
        """
        import numpy as np
        import xarray as xr

        name = self._RAW_CMIP6_STORED_NAME.get(var, var)

        def _decadal(path: Optional[Path]) -> dict:
            if path is None:
                return {}
            ds = xr.open_dataset(path, chunks={'time': 2400})
            da = ds[name].squeeze(drop=True)
            spatial_dims = [d for d in da.dims if d in ('lat', 'lon')]
            if 'lat' in spatial_dims:
                weights = np.cos(np.deg2rad(da['lat']))
                spatial_mean = da.weighted(weights).mean(dim=spatial_dims)
            else:
                spatial_mean = da.mean(dim=spatial_dims) if spatial_dims else da
            decade = ((spatial_mean['time'].dt.year // 10) * 10).rename('decade')
            grouped = spatial_mean.groupby(decade)
            counts = grouped.count().compute()
            means = grouped.mean().compute()
            full = float(counts.max()) if counts.size else 0.0
            return {
                int(k): float(m)
                for k, c, m in zip(means['decade'].values, counts.values, means.values)
                if full and c >= 0.9 * full
            }

        return (
            _decadal(self._raw_cmip6_file(model, 'historical', var)),
            _decadal(self._raw_cmip6_file(model, 'ssp126', var)),
            _decadal(self._raw_cmip6_file(model, 'ssp370', var)),
        )

    def _generate_raw_cmip6_section(self, hugo_root: Path) -> str:
        """Markdown for the "Raw CMIP6 Data" section of /scenarios/ -- the
        model's own published output, before any bias correction, with a
        decadal-mean trend plot per variable and a 3-hourly source-
        availability inventory across all three core models. Placed FIRST
        on the page (ahead of the bias-corrected section that follows it)
        -- this is the reference the bias correction is calibrated
        against, and the natural reading order for "what did the model
        actually publish, then what did we do to it".

        Returns "" if nothing has been fetched yet (_RAW_CMIP6_ROOT
        missing or no files under it) rather than a section full of
        broken image links -- same "don't claim data that isn't there"
        principle as has_data/scenarios_src elsewhere in this file.
        """
        if not self._RAW_CMIP6_ROOT.is_dir():
            return ''

        _VAR_ORDER = ['tas', 'pr', 'uas', 'vas', 'huss', 'rsds', 'rlds', 'rsus', 'rlus', 'ps']
        _VAR_TITLE = {
            'tas': 'Air temp (tas)', 'pr': 'Precip (pr)',
            'uas': 'Eastward wind (uas)', 'vas': 'Northward wind (vas)',
            'huss': 'Specific humidity (huss)', 'rsds': 'Downwelling SW (rsds)',
            'rlds': 'Downwelling LW (rlds)', 'rsus': 'Upwelling SW (rsus)',
            'rlus': 'Upwelling LW (rlus)', 'ps': 'Surface pressure (ps)',
        }

        static_dir = hugo_root / 'static' / 'scenarios' / 'meteo' / '_raw_cmip6'
        static_dir.mkdir(parents=True, exist_ok=True)

        content = (
            "## Raw CMIP6 Data\n\n"
            "Meteorological forcing fields exactly as published by the source "
            "model -- no bias correction applied. This is the reference the "
            "bias-corrected fields further down this page are calibrated "
            "against, fetched directly from Pangeo/GCS or ESGF (see "
            "ocean-data's `DataLoader`) rather than through the bias-"
            "correction pipeline.\n\n"
        )

        plots_written = []
        for model in self._RAW_CMIP6_FETCHED_MODELS:
            fig_path = static_dir / f'{model.lower()}_decadal_trends.png'
            try:
                import matplotlib
                matplotlib.use('Agg')
                import matplotlib.pyplot as _plt
                import numpy as _np

                n_vars = len(_VAR_ORDER)
                n_cols = min(4, max(1, int(_np.ceil(_np.sqrt(n_vars)))))
                n_rows = int(_np.ceil(n_vars / n_cols))
                fig, axes = _plt.subplots(n_rows, n_cols, figsize=(4.0 * n_cols, 3.2 * n_rows), squeeze=False)
                for idx in range(n_vars, n_rows * n_cols):
                    axes.flat[idx].set_visible(False)

                any_data = False
                for ax, var in zip(axes.flat, _VAR_ORDER):
                    hist, ssp126, ssp370 = self._raw_cmip6_decadal_series(model, var)
                    if hist or ssp126 or ssp370:
                        any_data = True
                    if hist:
                        years = sorted(hist)
                        ax.plot(years, [hist[y] for y in years], color='black',
                                linewidth=1.8, label='historical')
                    for series, color, label in [(ssp126, 'tab:blue', 'ssp126'), (ssp370, 'tab:red', 'ssp370')]:
                        if series:
                            years = sorted(series)
                            ax.plot(years, [series[y] for y in years], color=color,
                                    linestyle='--', linewidth=1.6, label=label)
                    # The historical→scenario-driven-forcing boundary -- a
                    # real change in which CMIP6 experiment produced the
                    # data (the model's own historical run vs. an
                    # SSP-forced projection), not a data-quality artifact.
                    # Marked on every panel so it reads the same way
                    # regardless of which variable happens to show a visible
                    # trend change there or not.
                    ax.axvline(2015, color='gray', linestyle=':', linewidth=1.0, alpha=0.8)
                    ax.set_title(_VAR_TITLE.get(var, var), fontsize=10)
                    ax.tick_params(labelsize=8)

                if not any_data:
                    _plt.close(fig)
                    continue

                handles, labels = axes.flat[0].get_legend_handles_labels()
                if handles:
                    fig.legend(handles, labels, loc='lower center', ncol=3, fontsize=9,
                               bbox_to_anchor=(0.5, -0.02))
                fig.suptitle(f'{model} — decadal-mean trends, 1990–2099', fontsize=13, fontweight='bold')
                _plt.tight_layout(rect=(0, 0.04, 1, 0.96))
                fig.savefig(fig_path, dpi=150, bbox_inches='tight')  # type: ignore[union-attr]
                _plt.close(fig)
                plots_written.append((model, fig_path))
            except Exception:
                log.exception("Failed to build raw-CMIP6 decadal plot for %s", model)

        for model, fig_path in plots_written:
            rel = fig_path.relative_to(hugo_root / 'static')
            content += (
                f"![{model} decadal trends](/{rel.as_posix()})\n\n"
                f"*{model}, domain-averaged decadal means. Solid black = "
                "historical (1990–2014); dashed = ssp126/ssp370 "
                "(2015–2099). The dotted vertical line at 2015 marks where "
                "the underlying CMIP6 experiment switches from the model's "
                "own historical run to SSP-forced projections -- a real "
                "change in data source, not a plotting artifact.*\n\n"
            )

        if not plots_written:
            content += "*No raw CMIP6 data fetched yet.*\n\n"

        # 3-hourly source-availability inventory, all three core models --
        # what's fetchABLE (Pangeo/ESGF), not just what's already fetched
        # (only GFDL-ESM4 so far, see the note below the table).
        content += "### 3-hourly source availability\n\n"
        content += (
            "Which core variables actually publish 3-hourly output at all "
            "(checked directly against Pangeo and ESGF), independent of "
            "whether they've been fetched into the raw archive yet:\n\n"
        )
        content += "| Variable | GFDL-ESM4 | MPI-ESM1-2-HR | CNRM-ESM2-1 (hist) | CNRM-ESM2-1 (ssp126) | CNRM-ESM2-1 (ssp370) |\n"
        content += "|---|---|---|---|---|---|\n"
        for var in _VAR_ORDER:
            mpi_ok = var in self._RAW_CMIP6_3HR_AVAILABILITY['MPI-ESM1-2-HR']['historical']
            cnrm = self._RAW_CMIP6_3HR_AVAILABILITY['CNRM-ESM2-1']
            content += (
                f"| `{var}` | ✅ | {'✅' if mpi_ok else '❌'} | "
                f"{'✅' if var in cnrm['historical'] else '❌'} | "
                f"{'✅' if var in cnrm['ssp126'] else '❌'} | "
                f"{'✅' if var in cnrm['ssp370'] else '❌'} |\n"
            )
        content += (
            "\nGFDL-ESM4 publishes all 10 at 3-hourly for every period and is "
            "the only model fetched into the raw archive so far. "
            "MPI-ESM1-2-HR is capped at 5/10 (no radiation or surface-"
            "pressure fields at 3-hourly, in any period). CNRM-ESM2-1's "
            "historical run has full coverage, but its ScenarioMIP "
            "submission is considerably sparser -- ssp370 publishes only "
            "`tas`/`pr`/`huss` at 3-hourly.\n\n"
        )
        content += "---\n\n"
        return content
