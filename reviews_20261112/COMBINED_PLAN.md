# ocean-* refactoring plan (from four Fable reviews, 2026-11-12/13)

Per-repo findings: ocean-post.md, ocean-prep.md, ocean-data.md, ocean-plot-common-stack.md (same folder).
Codes below (B1, C2, ...) refer to those files.

## Phase 0 — safety net (tests first, no behaviour change)
- ocean-data: tests for subset_domain / subset_time / match_lon_convention, cache identity, eager vs lazy load_model_data, regrid key (S5)
- ocean-post: StatsDB NULL-key round trip, nested-experiment layout, asymmetric-NaN statistics, tiny synthetic end-to-end gridded-2d + one profile CLI
- ocean-plot: render smoke tests (plot_map, plot_map_subplots, each animation type, anim.save mocked)
- ocean-prep: trend-preservation test for QDM (expected to FAIL today), split_boundaries single-point test

## Phase 1 — safe bug fixes (APPLY; each with a test)
ocean-data: C1 subset_domain broadcast/cast, C2 lon full-range, C4 empty 2-D subset, C5 sort guard, C7 cache-hit type, C9 time_av as coordinate always, C10 regrid key, C11 target-domain lon, C12 atomic cache writes, C13 data_roots in lists, C14 --coverage reverse import, X5
ocean-post: B1 StatsDB duplicates (+ one-off dedupe), B2 residuals per year, B3 spatial correlation, B5 chunked mean, B6 multi-file year crash, B9 PLOT_TYPES glodap/station + nested audit, B13 small items, B8+B11 obs type and resolve_years together
ocean-prep: C2 split_boundaries (>=), C3 QDM NaN in hist, C5/C6 lon-convention/coverage guards in datum + separable regrid, C7 import-time basicConfig, C8 size format, C9 precip units
ocean-plot: A1 zoom-out, A2 subplot reshape, A3 share_colorbar, A4 cmap names, A5, A6, A7, A9, A12, A13
ocean-stack: E1 windows env, E2 ffmpeg binary, E3 README counts, E6 undeclared deps / CI ref

## Phase 2 — dead code and clutter (APPLY, grep-verified)
ocean-post X1 (10 cli modules, ~3000 lines), X2 (~800 lines), X4 shims, X5 tracked clutter (~78 MB: qq, kaj, *.nc, *.mp4, egg-info, BC leftovers)
ocean-prep X1, X4, X5, X6 docs
ocean-data X1, X2, X6, X9; ocean-plot C2, C4, C6, C7, C8

## Phase 3 — shared helpers across repos (needs API agreement, then APPLY)
- ocean-common: version.get_version(dist), logging.setup_logging, config.deep_merge, paths.safe_mkdir, progress_bar; ocean-data starts depending on ocean-common
- ocean_data.data_roots: load_site_config(project), load_config_layered(project, ..., post_process=) — replaces ocean-post loader_utils + ocean-prep cli_utils copies
- ocean-data public API: ESGFLoader.prefetch (ocean-prep x3), to_lon_convention (x3), nearest_valid_indices/flood_fill_nan (x3), time_utils (DISCUSS)
- ocean_plot: ensure_headless_backend(), unified gridlines/land (B4), cmap resolver, map primitives for siblings (B5 scope DISCUSS)

## Phase 4 — collapse duplicates inside repos
ocean-post D1 (five profile CLIs -> profile_runner), D2 (summary plots), D3 (source/years/output-dir helpers), D4 (model_vars, ast evaluator)
ocean-prep D5 boundary points, D4 vertical remap, D9/D10 CLI scaffolding, D8 local IO, S3 private names
ocean-data D2/D3 cache module, D6/D7 coordinate fallbacks, D1 ERDDAP (after mocked tests)
ocean-plot B3 fields.py, B2, B1, then animations package split (D1)

## Phase 5 — structural splits (behind byte-compare runs)
ocean-post: gridded_2d main (1190 lines), 3d, tidal, validate_profiles_against_model; reporting.py -> package
ocean-prep: bathymetry_regrid.py (3438) -> lib pipeline; workflow functions; bias_correction/utils.py
ocean-data: data_loader.py split with scheme registry

## Phase 6 — comment-rot sweep (per file as touched; last for the rest)

## DECISIONS NEEDED (DISCUSS items that change results, data on disk, or APIs)
1. ocean-prep C1 QDM is effectively QM -> fix algorithm (future-CDF ranks, moving window), version the method name, regenerate SSP meteo/ndep/direct-BC products. Measure first on one real variable.
2. ocean-post B4 / B12 statistics: paired means/std; exact chunk combination (published numbers change; recompute history?)
3. ocean-post B10 / ocean-plot A11 time tolerances (profile matching, animation frame sync)
4. ocean-post B7 observations dir independent of label depth (needs data move on bb-server1)
5. ocean-data C6 cache identity (old caches lose covering reuse) and C8 one load_model_data path (sibling call sites)
6. ocean-data C3 seam-crossing longitude order (output convention for ocean-prep)
7. Packaging: top-level `cli` package in five repos -> `<pkg>.cli`, config/ as package data (coordinated reinstall)
8. ocean-post X3 drop TidalConfig/ScenarioConfig (silent CWD config) for explicit constructor args
9. ocean-post D5 single statistics store (DB) vs TXT parsers
10. ocean-prep C4 depth remap NaN/extrapolation policy; D2 adopt layered config loader in 12 CLIs (${VAR} expansion)
11. ocean-plot A8 transect free surface; A14 default level surface vs bottom; B5 how much plotting moves into ocean-plot
12. Project-specific code out of packages: ocean-post NSe boundaries page, ocean-prep OceanICU constants/paths, ocean-run in stack?
