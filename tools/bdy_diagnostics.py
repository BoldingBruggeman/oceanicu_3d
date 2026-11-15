#!/usr/bin/env python3
"""bdy-diagnostics — cross-model/cross-scenario summary plots for NSe's
future boundary conditions (delta-change thetao/so, tidal+CMIP6-mean
zos/uo/vo).

Unlike bc-diagnostics (which validates bias-corrected meteo forcing
against a reference), there's no "correction" being validated here --
the delta-change/tidal boundary files are the final product. This tool
just summarizes them: one period-mean trajectory plot per variable
(surface, boundary-mean, one line per model+scenario), analogous to
bc-diagnostics' bc_period_summary.png for meteo.

Usage (moved from ocean-prep's cli/ to this project; run with the
ocean-stack Python)
-----
    python tools/bdy_diagnostics.py
    python tools/bdy_diagnostics.py --bdy-dir /data/OceanICU/oceanicu_3d/data/NSe/CMIP6
    python tools/bdy_diagnostics.py --out-dir /data/OceanICU/oceanicu_3d/analyses/areas/NSe/boundaries
"""
import argparse
import logging
from pathlib import Path

import xarray as xr
import yaml
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from ocean_prep.bias_correction.utils import group_years_by_period
from ocean_prep.boundaries.bio_common import (
    BIO_METHOD_COLORS,
    BIO_METHOD_LABELS,
    BIO_METHODS,
    BIO_VARS,
    find_bio_file as _find_bio_file,
    normalize_bio_units as _normalize_bio_units,
)

# NSe products (were ocean_prep.boundaries.bio_common constants)
MODELS = ['GFDL-ESM4', 'CNRM-ESM2-1', 'MPI-ESM1-2-HR']
SCENARIOS = ['ssp126', 'ssp370']

DEFAULT_BDY_DIR = Path('/data/OceanICU/oceanicu_3d/data/NSe/CMIP6')
CMEMS_BDY_DIR = Path('/data/OceanICU/oceanicu_3d/data/NSe/CMEMS/bdy')

# no3/po4/si/o2 have long real CMEMS coverage (2010-2026); dissic/talk only
# the short carbon-system window (2024-2026) -- two different reference
# files, same as delta_change.py's own reference.directories split.
BIO_REF_FILES = {
    'no3': CMEMS_BDY_DIR / 'bio_daily_2010-01-01_to_2026-08-17.nc',
    'po4': CMEMS_BDY_DIR / 'bio_daily_2010-01-01_to_2026-08-17.nc',
    'si': CMEMS_BDY_DIR / 'bio_daily_2010-01-01_to_2026-08-17.nc',
    'o2': CMEMS_BDY_DIR / 'bio_daily_2010-01-01_to_2026-08-17.nc',
    'dissic': CMEMS_BDY_DIR / 'bio_carbon_new_2024-07-29_to_2026-08-17.nc',
    'talk': CMEMS_BDY_DIR / 'bio_carbon_new_2024-07-29_to_2026-08-17.nc',
}

# 2010-2014 historical-bridge files (see driver/oceanicu_providers.py's
# dic_ta_historical_method ParameterSpec in oceanicu_3d) -- dissic/talk
# only; no3/po4/si/o2 never need a bridge (their own real CMEMS file
# already covers 2010 onward). Two methods, same nbdyp/depth/time shape
# as the real deltachange files they splice onto.
DIC_TA_HIST_FILES = {
    'cycled_climatology': CMEMS_BDY_DIR / 'bio_carbon_climatology_cycled_20100101_20141231.nc',
    'pml_trend': CMEMS_BDY_DIR / 'bio_carbon_pml_trend_20100101_20141231.nc',
}

log = logging.getLogger(__name__)

CLIM_PERIOD = 10  # years per window, matching bc_diagnostics.py's convention

DELTA_VARS = ['thetao', 'so']   # from bdy_3d_<var>_*.nc, has a depth dim
TIDAL_VARS = ['zos', 'uo', 'vo']  # from bdy_2d_*.nc, no depth dim

DEFAULT_BDY_DIR = Path('/data/OceanICU/oceanicu_3d/data/NSe/CMIP6')
DEFAULT_OUT_DIR = Path('/data/OceanICU/oceanicu_3d/analyses/areas/NSe/boundaries')

# CMEMS reference (the file the delta-change correction is calibrated
# against), plotted with CMIP6 for the period it covers. Only DELTA_VARS
# (thetao/so): CMEMS zos/uo/vo include tides, the CMIP6 tidal boundary only the
# mean, so their period means are not comparable.
DEFAULT_REF_FILE = Path('/data/OceanICU/oceanicu_3d/data/NSe/CMEMS/bdy/daily_2010-01-01_to_2026-08-17.nc')

# ── Bio boundary comparison (no3/po4/si/o2/dissic/talk) ─────────────────────
# Compares generation methods (raw CMIP6, delta change, direct bias correction)
# for one model and scenario per boundary segment -- a boundary-wide mean would
# hide differences between segments. The bio settings come from
# lib.boundaries.bio_common (shared with cli/final_boundary_check.py).
DEFAULT_BIO_MODEL = 'GFDL-ESM4'
DEFAULT_BIO_SCENARIO = 'ssp126'
# All models and scenarios, for run_bio_all_combos().
BIO_CMP_MODELS = MODELS
BIO_CMP_SCENARIOS = SCENARIOS
BIO_MODEL_COLORS = {
    'GFDL-ESM4': 'tab:blue',
    'CNRM-ESM2-1': 'tab:green',
    'MPI-ESM1-2-HR': 'tab:red',
}
BIO_SCENARIO_STYLES = {
    'ssp126': '-',
    'ssp370': '--',
}
DEFAULT_BIO_OUT_DIR = Path('/data/OceanICU/oceanicu_3d/analyses/areas/NSe/boundaries_bio')


from ocean_common.log import setup_logging  # noqa: E402


def _find_file(bdy_dir: Path, model: str, scenario: str, var: str) -> Path | None:
    subdir = bdy_dir / model / scenario / 'bdy'
    if var in DELTA_VARS:
        matches = sorted(subdir.glob(f'bdy_3d_{var}_*.nc'))
    else:
        matches = sorted(subdir.glob('bdy_2d_*.nc'))
    return matches[0] if matches else None


def _period_means(da: xr.DataArray, has_depth: bool) -> dict:
    """Return {(win_start, win_end): boundary-mean value} for surface
    (shallowest depth level, if any) averaged over nbdyp, per ~decade window."""
    if has_depth:
        da = da.isel(depth=0)  # surface
    years = da['time'].dt.year.values
    y0, y1 = int(years.min()), int(years.max())
    windows = group_years_by_period(y0, y1, CLIM_PERIOD)
    out = {}
    for w0, w1 in windows:
        mask = (years >= w0) & (years <= w1)
        if not mask.any():
            continue
        chunk = da.isel(time=mask)
        out[(w0, w1)] = float(chunk.mean(skipna=True).compute())
    return out


def _segment_period_means(da: xr.DataArray, segment_id: xr.DataArray, has_depth: bool | str | None) -> dict:
    """Return {segment: {(win_start, win_end): mean}} for the surface, averaged
    over nbdyp within each segment per ~decade window (see the BIO_VARS
    comment for why per segment).

    has_depth is True/False or the depth dimension's name: rawcmip6 files
    use 'lev' (cmip6_processor's convention), the CMEMS/delta-change/direct-bc
    files 'depth'; without the right name the "surface" value would be a
    whole-column mean.
    """
    depth_dim = has_depth if isinstance(has_depth, str) else ("depth" if has_depth else None)
    if depth_dim:
        da = da.isel({depth_dim: 0})
    years = da['time'].dt.year.values
    y0, y1 = int(years.min()), int(years.max())
    windows = group_years_by_period(y0, y1, CLIM_PERIOD)
    segs = sorted(int(s) for s in set(segment_id.values.tolist()))
    out: dict = {}
    for seg in segs:
        seg_mask = segment_id.values == seg
        da_seg = da.isel(nbdyp=seg_mask)
        out[seg] = {}
        for w0, w1 in windows:
            mask = (years >= w0) & (years <= w1)
            if not mask.any():
                continue
            chunk = da_seg.isel(time=mask)
            out[seg][(w0, w1)] = float(chunk.mean(skipna=True).compute())
    return out


def run_bio(bdy_dir: Path, out_dir: Path,
            model: str = DEFAULT_BIO_MODEL, scenario: str = DEFAULT_BIO_SCENARIO) -> None:
    """Per-segment, multi-method comparison for the 6 real ERSEM boundary
    state variables -- one figure per variable, a 3x3 grid (one subplot per
    boundary segment), one line per generation method actually present
    (rawcmip6 always; deltachange for all 6; directbc only for talk/dissic
    so far) plus the real CMEMS reference line where coverage overlaps."""
    for var in BIO_VARS:
        method_data: dict = {}
        for method in BIO_METHODS:
            f = _find_bio_file(bdy_dir, model, scenario, var, method)
            if f is None:
                continue
            log.info('Reading %s', f)
            ds = xr.open_dataset(f, chunks={'time': 3650})
            if var not in ds:
                log.warning('  %s not a variable in %s -- skipping', var, f)
                ds.close()
                continue
            da = _normalize_bio_units(ds[var])
            # rawcmip6 uses 'lev', not 'depth' (see _segment_period_means).
            depth_dim = next((d for d in ('depth', 'lev') if d in da.dims), None)
            method_data[method] = _segment_period_means(da, ds['segment_id'], depth_dim)
            ds.close()

        ref_file = BIO_REF_FILES.get(var)
        ref_pm = None
        if ref_file is not None and ref_file.exists():
            log.info('Reading CMEMS reference %s', ref_file)
            ref_ds = xr.open_dataset(ref_file, chunks={'time': 3650})
            if var in ref_ds:
                da = ref_ds[var]
                depth_dim = next((d for d in ('depth', 'lev') if d in da.dims), None)
                ref_pm = _segment_period_means(da, ref_ds['segment_id'], depth_dim)
            else:
                log.warning('  %s not in CMEMS reference -- skipping reference line', var)
            ref_ds.close()

        if not method_data and not ref_pm:
            log.warning('No data at all for bio var %s (%s/%s) -- skipping', var, model, scenario)
            continue

        plots_dir = out_dir / var / 'plots'
        tables_dir = out_dir / var / 'tables'
        plots_dir.mkdir(parents=True, exist_ok=True)
        tables_dir.mkdir(parents=True, exist_ok=True)

        segs = sorted(set().union(*(set(d.keys()) for d in method_data.values()))
                      | (set(ref_pm.keys()) if ref_pm else set()))

        fig, axes = plt.subplots(3, 3, figsize=(15, 12), sharex=True)
        for idx, seg in enumerate(segs[:9]):
            ax = axes.flat[idx]
            if ref_pm and seg in ref_pm:
                windows = sorted(ref_pm[seg].keys())
                xs = [w0 for w0, _ in windows]
                ys = [ref_pm[seg][w] for w in windows]
                ax.plot(xs, ys, marker='o', markersize=4, color='black',
                        linewidth=2, label='CMEMS (reference)', zorder=5)
            for method in BIO_METHODS:
                pm = method_data.get(method, {}).get(seg)
                if not pm:
                    continue
                windows = sorted(pm.keys())
                xs = [w0 for w0, _ in windows]
                ys = [pm[w] for w in windows]
                ax.plot(xs, ys, marker='o', markersize=3,
                        color=BIO_METHOD_COLORS[method],
                        label=BIO_METHOD_LABELS[method], alpha=0.85)
            ax.set_title(f'segment {seg}', fontsize=9)
            ax.grid(alpha=0.3)
        for idx in range(len(segs), 9):
            axes.flat[idx].axis('off')

        handles, labels = axes.flat[0].get_legend_handles_labels()
        if handles:
            fig.legend(handles, labels, loc='lower center', ncol=len(handles), fontsize=9)
        fig.suptitle(f'{var}: per-segment period-mean at NSe boundary, {model}/{scenario} (surface)')
        fig.tight_layout(rect=(0, 0.05, 1, 0.96))
        plot_path = plots_dir / f'{var}_{model}_{scenario}_segments.png'
        fig.savefig(plot_path, dpi=150)
        plt.close(fig)
        log.info('Wrote %s', plot_path)

        doc = {
            'variable': var,
            'model': model,
            'scenario': scenario,
            'methods_present': list(method_data.keys()),
            'per_segment_period_means': {
                method: {
                    str(seg): {f'{w0}-{w1}': v for (w0, w1), v in pm.items()}
                    for seg, pm in segdata.items()
                }
                for method, segdata in method_data.items()
            },
        }
        if ref_pm:
            doc['per_segment_period_means']['CMEMS (reference)'] = {
                str(seg): {f'{w0}-{w1}': v for (w0, w1), v in pm.items()}
                for seg, pm in ref_pm.items()
            }
        table_path = tables_dir / f'{var}_{model}_{scenario}_segment_statistics.yaml'
        with open(table_path, 'w') as fh:
            yaml.dump(doc, fh, default_flow_style=False, sort_keys=False)
        log.info('Wrote %s', table_path)


HIST_BRIDGE_COLORS = {
    'cycled_climatology': 'tab:purple',
    'pml_trend': 'tab:brown',
}
HIST_BRIDGE_LABELS = {
    'cycled_climatology': 'historical bridge: cycled climatology (2010-2014)',
    'pml_trend': 'historical bridge: PML trend (2010-2014)',
}


def run_bio_historical_bridge(bdy_dir: Path, out_dir: Path, model: str = DEFAULT_BIO_MODEL) -> None:
    """dissic/talk only: plot both candidates for bridging 2010-2014 (neither
    the CMIP6 delta-change file, from 2015, nor CMEMS bio_carbon_new, from
    2024-07-29, covers it -- see dic_ta_historical_method in oceanicu_3d's
    driver/oceanicu_providers.py): cycled_climatology and pml_trend, each
    continued into the 2015-2100 delta-change files for ssp126 and ssp370, so
    both the bridge and the future signal can be judged in one figure.
    """
    for var in ('dissic', 'talk'):
        series: dict = {}

        for method, path in DIC_TA_HIST_FILES.items():
            if not path.exists():
                log.warning('  %s bridge file missing: %s -- skipping', method, path)
                continue
            log.info('Reading %s bridge %s', method, path)
            ds = xr.open_dataset(path, chunks={'time': 1826})
            if var not in ds:
                log.warning('  %s not in %s -- skipping', var, path)
                ds.close()
                continue
            series[method] = _segment_period_means(_normalize_bio_units(ds[var]), ds['segment_id'], 'depth')
            ds.close()

        for scenario in SCENARIOS:
            f = _find_bio_file(bdy_dir, model, scenario, var, 'deltachange')
            if f is None:
                continue
            log.info('Reading %s', f)
            ds = xr.open_dataset(f, chunks={'time': 3650})
            if var not in ds:
                ds.close()
                continue
            series[f'deltachange_{scenario}'] = _segment_period_means(
                _normalize_bio_units(ds[var]), ds['segment_id'], 'depth')
            ds.close()

        if not series:
            log.warning('No data at all for historical-bridge comparison of %s -- skipping', var)
            continue

        plots_dir = out_dir / var / 'plots'
        tables_dir = out_dir / var / 'tables'
        plots_dir.mkdir(parents=True, exist_ok=True)
        tables_dir.mkdir(parents=True, exist_ok=True)

        segs = sorted(set().union(*(set(d.keys()) for d in series.values())))
        fig, axes = plt.subplots(3, 3, figsize=(15, 12), sharex=True)
        for idx, seg in enumerate(segs[:9]):
            ax = axes.flat[idx]
            for method, segdata in series.items():
                pm = segdata.get(seg)
                if not pm:
                    continue
                windows = sorted(pm.keys())
                xs = [w0 for w0, _ in windows]
                ys = [pm[w] for w in windows]
                if method in HIST_BRIDGE_COLORS:
                    ax.plot(xs, ys, marker='s', markersize=4, linestyle=':',
                            color=HIST_BRIDGE_COLORS[method], label=HIST_BRIDGE_LABELS[method])
                else:
                    scenario = method.removeprefix('deltachange_')
                    ax.plot(xs, ys, marker='o', markersize=3,
                            linestyle=BIO_SCENARIO_STYLES.get(scenario, '-'),
                            color='tab:blue' if scenario == 'ssp126' else 'tab:red',
                            label=f'deltachange (future): {scenario}')
            ax.axvline(2015, color='black', lw=0.8, ls=':')
            ax.set_title(f'segment {seg}', fontsize=9)
            ax.grid(alpha=0.3)
        for idx in range(len(segs), 9):
            axes.flat[idx].axis('off')

        handles, labels = axes.flat[0].get_legend_handles_labels()
        if handles:
            fig.legend(handles, labels, loc='lower center', ncol=2, fontsize=8)
        fig.suptitle(f'{var}: historical bridge (2010-2014) vs. future delta-change '
                     f'({model}, surface, per segment) -- dotted vertical line marks the splice')
        fig.tight_layout(rect=(0, 0.08, 1, 0.96))
        plot_path = plots_dir / f'{var}_historical_bridge_and_future.png'
        fig.savefig(plot_path, dpi=150)
        plt.close(fig)
        log.info('Wrote %s', plot_path)

        doc = {
            'variable': var,
            'model': model,
            'methods_present': list(series.keys()),
            'per_segment_period_means': {
                method: {
                    str(seg): {f'{w0}-{w1}': v for (w0, w1), v in pm.items()}
                    for seg, pm in segdata.items()
                }
                for method, segdata in series.items()
            },
        }
        table_path = tables_dir / f'{var}_historical_bridge_statistics.yaml'
        with open(table_path, 'w') as fh:
            yaml.dump(doc, fh, default_flow_style=False, sort_keys=False)
        log.info('Wrote %s', table_path)


def run_bio_all_combos(bdy_dir: Path, out_dir: Path) -> None:
    """One figure per variable, all 3 CMIP6 models x 2 scenarios overlaid
    (delta-change method, the production forcing), plus the CMEMS reference:
    how consistent the models are with each other. run_bio()'s per-combination
    plots show instead how much the correction changed one model/scenario.
    """
    for var in BIO_VARS:
        combo_data: dict = {}
        for model in BIO_CMP_MODELS:
            for scenario in BIO_CMP_SCENARIOS:
                f = _find_bio_file(bdy_dir, model, scenario, var, 'deltachange')
                if f is None:
                    continue
                log.info('Reading %s', f)
                ds = xr.open_dataset(f, chunks={'time': 3650})
                if var not in ds:
                    log.warning('  %s not a variable in %s -- skipping', var, f)
                    ds.close()
                    continue
                da = _normalize_bio_units(ds[var])
                depth_dim = next((d for d in ('depth', 'lev') if d in da.dims), None)
                combo_data[(model, scenario)] = _segment_period_means(da, ds['segment_id'], depth_dim)
                ds.close()

        ref_file = BIO_REF_FILES.get(var)
        ref_pm = None
        if ref_file is not None and ref_file.exists():
            log.info('Reading CMEMS reference %s', ref_file)
            ref_ds = xr.open_dataset(ref_file, chunks={'time': 3650})
            if var in ref_ds:
                da = ref_ds[var]
                depth_dim = next((d for d in ('depth', 'lev') if d in da.dims), None)
                ref_pm = _segment_period_means(da, ref_ds['segment_id'], depth_dim)
            else:
                log.warning('  %s not in CMEMS reference -- skipping reference line', var)
            ref_ds.close()

        if not combo_data and not ref_pm:
            log.warning('No delta-change data at all for bio var %s across any combo -- skipping', var)
            continue

        plots_dir = out_dir / var / 'plots'
        tables_dir = out_dir / var / 'tables'
        plots_dir.mkdir(parents=True, exist_ok=True)
        tables_dir.mkdir(parents=True, exist_ok=True)

        segs = sorted(set().union(*(set(d.keys()) for d in combo_data.values()))
                      | (set(ref_pm.keys()) if ref_pm else set()))

        fig, axes = plt.subplots(3, 3, figsize=(15, 12), sharex=True)
        for idx, seg in enumerate(segs[:9]):
            ax = axes.flat[idx]
            if ref_pm and seg in ref_pm:
                windows = sorted(ref_pm[seg].keys())
                xs = [w0 for w0, _ in windows]
                ys = [ref_pm[seg][w] for w in windows]
                ax.plot(xs, ys, marker='o', markersize=4, color='black',
                        linewidth=2, label='CMEMS (reference)', zorder=5)
            for (model, scenario), pm_all in combo_data.items():
                pm = pm_all.get(seg)
                if not pm:
                    continue
                windows = sorted(pm.keys())
                xs = [w0 for w0, _ in windows]
                ys = [pm[w] for w in windows]
                ax.plot(xs, ys, marker='o', markersize=3,
                        color=BIO_MODEL_COLORS[model],
                        linestyle=BIO_SCENARIO_STYLES[scenario],
                        label=f'{model}/{scenario}', alpha=0.85)
            ax.set_title(f'segment {seg}', fontsize=9)
            ax.grid(alpha=0.3)
        for idx in range(len(segs), 9):
            axes.flat[idx].axis('off')

        handles, labels = axes.flat[0].get_legend_handles_labels()
        if handles:
            fig.legend(handles, labels, loc='lower center',
                       ncol=min(len(handles), 4), fontsize=8)
        fig.suptitle(f'{var}: per-segment period-mean at NSe boundary, '
                     f'all 3 CMIP6 models x 2 scenarios (delta-change, surface)')
        fig.tight_layout(rect=(0, 0.1, 1, 0.96))
        plot_path = plots_dir / f'{var}_all_combos_segments.png'
        fig.savefig(plot_path, dpi=150)
        plt.close(fig)
        log.info('Wrote %s', plot_path)

        doc = {
            'variable': var,
            'method': 'deltachange',
            'combos_present': [f'{m}/{s}' for (m, s) in combo_data.keys()],
            'per_segment_period_means': {
                f'{m}/{s}': {
                    str(seg): {f'{w0}-{w1}': v for (w0, w1), v in pm.items()}
                    for seg, pm in segdata.items()
                }
                for (m, s), segdata in combo_data.items()
            },
        }
        if ref_pm:
            doc['per_segment_period_means']['CMEMS (reference)'] = {
                str(seg): {f'{w0}-{w1}': v for (w0, w1), v in pm.items()}
                for seg, pm in ref_pm.items()
            }
        table_path = tables_dir / f'{var}_all_combos_segment_statistics.yaml'
        with open(table_path, 'w') as fh:
            yaml.dump(doc, fh, default_flow_style=False, sort_keys=False)
        log.info('Wrote %s', table_path)


def run(bdy_dir: Path, out_dir: Path, ref_file: Path | None = DEFAULT_REF_FILE) -> None:
    all_vars = DELTA_VARS + TIDAL_VARS
    # results[var][(model, scenario)] = {(w0, w1): mean}
    results: dict = {v: {} for v in all_vars}
    file_attrs: dict = {}  # var -> one representative file's attrs (for captions)
    ref_period_means: dict = {}  # var -> {(w0, w1): mean}, DELTA_VARS only

    for model in MODELS:
        for scenario in SCENARIOS:
            for var in all_vars:
                f = _find_file(bdy_dir, model, scenario, var)
                if f is None:
                    log.warning('  missing: %s/%s/%s', model, scenario, var)
                    continue
                log.info('Reading %s', f)
                ds = xr.open_dataset(f, chunks={'time': 3650})
                if var not in ds:
                    log.warning('  %s not a variable in %s -- skipping', var, f)
                    continue
                da = ds[var]
                has_depth = 'depth' in da.dims
                results[var][(model, scenario)] = _period_means(da, has_depth)
                if var not in file_attrs:
                    file_attrs[var] = dict(ds.attrs)
                ds.close()

    if ref_file is not None and ref_file.exists():
        log.info('Reading CMEMS reference %s', ref_file)
        ref_ds = xr.open_dataset(ref_file, chunks={'time': 3650})
        for var in DELTA_VARS:
            if var not in ref_ds:
                log.warning('  %s not in CMEMS reference -- skipping reference line', var)
                continue
            da = ref_ds[var]
            has_depth = 'depth' in da.dims
            ref_period_means[var] = _period_means(da, has_depth)
        ref_ds.close()
    elif ref_file is not None:
        log.warning('CMEMS reference file not found: %s -- plots will have no reference line', ref_file)

    for var in all_vars:
        plots_dir = out_dir / var / 'plots'
        tables_dir = out_dir / var / 'tables'
        plots_dir.mkdir(parents=True, exist_ok=True)
        tables_dir.mkdir(parents=True, exist_ok=True)

        combos = results[var]
        if not combos:
            log.warning('No data at all for %s -- skipping plot/table', var)
            continue

        # ── plot ──────────────────────────────────────────────────────
        fig, ax = plt.subplots(figsize=(8, 5))
        colors = plt.colormaps['tab10'].colors
        linestyles = {'ssp126': '-', 'ssp370': '--'}

        # Real CMEMS reference, plotted first so it sits on the same
        # continuous timeline BEFORE the CMIP6 projection (2010-2026
        # observed, then 2015-2100 projected -- the two periods overlap
        # 2015-2026, which is deliberate: shows the projection picking up
        # from where the real reference already is, not starting cold).
        ref_pm = ref_period_means.get(var)
        if ref_pm:
            windows = sorted(ref_pm.keys())
            xs = [w0 for w0, _ in windows]
            ys = [ref_pm[w] for w in windows]
            ax.plot(xs, ys, marker='o', markersize=6, color='black',
                    linewidth=2.5, label='CMEMS (reference)', zorder=5)

        for i, model in enumerate(MODELS):
            for scenario in SCENARIOS:
                pm = combos.get((model, scenario))
                if not pm:
                    continue
                windows = sorted(pm.keys())
                xs = [w0 for w0, _ in windows]
                ys = [pm[w] for w in windows]
                ax.plot(xs, ys, marker='o', markersize=4,
                        color=colors[i % len(colors)],
                        linestyle=linestyles.get(scenario, '-'),
                        label=f'{model} {scenario}', alpha=0.85)
        ax.set_title(f'{var}: period-mean at NSe boundary (surface, boundary-mean)')
        ax.set_xlabel('Period start year')
        ax.set_ylabel(var)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        plot_path = plots_dir / f'{var}_period_summary.png'
        fig.savefig(plot_path, dpi=150)
        plt.close(fig)
        log.info('Wrote %s', plot_path)

        # ── stats table ───────────────────────────────────────────────
        doc = {
            'variable': var,
            'source_attrs': {
                k: str(v) for k, v in file_attrs.get(var, {}).items()
                if k in ('source_datasets', 'frequency', 'total_boundary_points',
                         'delta_hist_period', 'delta_future_period',
                         'delta_method', 'delta_transient')
            },
            'period_means': {
                f'{model}/{scenario}': {
                    f'{w0}-{w1}': v for (w0, w1), v in pm.items()
                }
                for (model, scenario), pm in combos.items()
            },
        }
        if var in ref_period_means:
            doc['period_means']['CMEMS (reference)'] = {
                f'{w0}-{w1}': v for (w0, w1), v in ref_period_means[var].items()
            }
        table_path = tables_dir / f'{var}_statistics.yaml'
        with open(table_path, 'w') as fh:
            yaml.dump(doc, fh, default_flow_style=False, sort_keys=False)
        log.info('Wrote %s', table_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bdy-dir', type=Path, default=DEFAULT_BDY_DIR,
                        help='Root dir containing <model>/<scenario>/bdy/*.nc')
    parser.add_argument('--out-dir', type=Path, default=DEFAULT_OUT_DIR,
                        help='Output analyses dir')
    parser.add_argument('--ref-file', type=Path, default=DEFAULT_REF_FILE,
                        help='Real CMEMS reference file (thetao/so) to overlay on the '
                             'period-summary plots as a reference line -- pass a '
                             'nonexistent path or omit the underlying file to disable')
    parser.add_argument('--bio', action='store_true',
                        help='Also (or instead, with --bio-only) run the bio boundary '
                             'comparison (no3/po4/si/o2/dissic/talk), per-segment, '
                             'one line per generation method actually present')
    parser.add_argument('--bio-only', action='store_true',
                        help='Run only the bio boundary comparison, skip the physics one')
    parser.add_argument('--bio-out-dir', type=Path, default=DEFAULT_BIO_OUT_DIR,
                        help='Output dir for --bio/--bio-only')
    parser.add_argument('--bio-model', default=DEFAULT_BIO_MODEL,
                        help='Model to compare methods for, for --bio/--bio-only '
                             '(one model/scenario at a time, all generation methods)')
    parser.add_argument('--bio-scenario', default=DEFAULT_BIO_SCENARIO)
    parser.add_argument('--bio-all-combos', action='store_true',
                        help='Also (or instead, with --bio-only) run the cross-model/'
                             'cross-scenario bio comparison: one figure per variable, '
                             'all 3 CMIP6 models x 2 scenarios overlaid, delta-change '
                             'method only -- see run_bio_all_combos()')
    parser.add_argument('--bio-historical-bridge', action='store_true',
                        help='Also (or instead, with --bio-only) run the dissic/talk '
                             '2010-2014 historical-bridge comparison (cycled_climatology '
                             'vs pml_trend) continuing into the real future delta-change '
                             'file for both scenarios -- see run_bio_historical_bridge()')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()

    setup_logging(args.verbose)
    if not args.bio_only:
        run(args.bdy_dir, args.out_dir, args.ref_file)
    if args.bio or args.bio_only:
        run_bio(args.bdy_dir, args.bio_out_dir, args.bio_model, args.bio_scenario)
    if args.bio_all_combos:
        run_bio_all_combos(args.bdy_dir, args.bio_out_dir)
    if args.bio_historical_bridge:
        run_bio_historical_bridge(args.bdy_dir, args.bio_out_dir, args.bio_model)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
