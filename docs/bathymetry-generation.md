# Regenerating the NSe bathymetry

`NSe/Bathymetry/bathymetry_nse.nc` is built by `bathymetry-regrid` (from the
`ocean-prep` repo, `ocean-prep`/`ocean-stack` conda envs) driven by
`NSe/config/nse_bathymetry.yaml`. **This is a two-pass process** — running it
once is not enough to reproduce a consistent result.

## Pass 1 — analysis

```
conda activate ocean-prep
cd NSe/config
bathymetry-regrid --config nse_bathymetry.yaml
```

This regrids GEBCO onto the target grid, applies the config's explicit
`fixes:`/`mask_regions:`/`local_min_depth:`/`smooth:` sections, then runs
thalweg and phantom-island analysis and writes **suggested** additional
fixes to `<report_dir>/fixes.yaml` (does not apply them). At this point the
written `bathymetry_nse.nc` is **incomplete** — known strait connectivity
problems (e.g. Little Belt / Great Belt / Öresund, which the coarse grid
alone cannot resolve) are still broken.

## Pass 2 — accept the thalweg fixes, re-run

Edit `<report_dir>/fixes.yaml`: find the group-level entries and set
`applied: true` on each of:

- `tw_thalweg`
- `tw_irish_sea`
- `tw_great_belt`
- `tw_little_belt`
- `tw_oresund`
- `phantom_islands` (if present — only appears when the run actually
  detects any; a clean run may show none)

Leave individual `b0xx`/`s0xx`/`a0xx` entries (`BLOCKED`/`SILL_DEFICIT`/
`AREA_DEFICIT`) as `applied: false` unless you've specifically reviewed and
want one — these are optional, finer-grained suggestions, not required for
a consistent result. Then re-run from the **same working directory** as
pass 1:

```
bathymetry-regrid --config nse_bathymetry.yaml --accept-fixes
```

This reads `fixes.yaml` from the report directory, applies every entry
whose group (or itself) is `applied: true`, and overwrites
`bathymetry_nse.nc` with the corrected file. Verify the connectivity fix
took effect — the log's `[6/6]` step should show a sane
`depth_rx0_0p20` rx0 (`<= 0.20`), and `ocean_mask` at the three Danish
straits should be wet.

## Gotchas

- **Run both passes from the same directory.** `report_dir` in the config
  is relative (`./report/nse/`); if pass 1 and pass 2 run from different
  cwds, pass 2 won't find pass 1's `fixes.yaml` and will just repeat the
  incomplete pass-1 result. (`NSe/config/` is the conventional cwd — that's
  where this note's commands assume you are.)
- **Always back up the existing file first**: `cp -p bathymetry_nse.nc
  bathymetry_nse.nc.backup_$(date +%Y%m%d_%H%M%S)` — both passes overwrite
  it in place, and `--dryrun` (prints the resolved config/paths without
  writing) is worth a quick check beforehand if you've touched the config.
- `ocean-prep`'s bathymetry pipeline may have uncommitted local changes
  (`git -C <ocean-prep repo> status`) — check before comparing runs done at
  different times, since a code difference (not just data) can change the
  result.

## 2026-09-14 regeneration: what changed vs. the 2026-09-07 file

Comparing the regenerated file against a backup of the previous
(2026-09-07) one surfaced two real differences:

- **"Black River" mask region** (1.06°E, 51.7–51.8°N, Essex, UK): the
  2026-09-07 file had this region wet despite an explicit `mask_regions`
  rule naming it for closure — that rule apparently wasn't being applied.
  The regenerated file correctly masks it. **Kept** — this is a genuine fix.
- **Weser/Elbe estuary extent** (~8.1–8.7°E, 53.4–53.9°N, 14 cells): the
  coarse mask newly resolved the estuary's real southward channel extent
  (confirmed against fine-resolution GEBCO — the old file cut off the
  estuary abruptly around 53.6°N; GEBCO shows real wet channels down to
  ~53.3–53.4°N). See `docs/bathymetry-investigation/wadden_sea_comparison.png`
  (old vs. new vs. difference) and `wadden_sea_vs_gebco.png` (both against
  the fine GEBCO source) for the comparison plots. **Reverted** — the
  underlying mechanism for why this changed is still not root-caused (ruled
  out: phantom-island detection, `local_min_depth` — neither can flip a
  mask; the connected-component code itself is unchanged), and production
  wants the previous, narrower extent kept until that's understood. The 14
  cells are explicitly reverted in `nse_bathymetry.yaml`'s `mask_regions:`
  — 11 of them share one clean rectangle (`Weser estuary revert (block)`,
  verified to contain no cell that was legitimately wet in the 2026-09-07
  file), the remaining 3 stay as individual `point` entries since each
  sits immediately next to a real open-water cell that a rectangle would
  have over-masked.

No connectivity regressions once the pass-2 thalweg fixes were applied —
Little Belt/Great Belt/Öresund all matched the original file's connectivity
after that step.

**Gotcha found while doing this**: a `phantom_islands` fixes.yaml group
appeared in one pass-1 run (2 cells at the Öresund waypoint locations,
12.82°E/55.60–55.65°N — cells that look like phantom islands *before*
thalweg forcing runs, since they're only wet due to that forcing). Setting
`phantom_islands: applied: true` masked them as land and broke Öresund
connectivity in pass 2, even though the original 2026-09-07 file used
`phantom_islands: applied: true` successfully. Left `phantom_islands` at
`applied: false` for this regeneration instead of chasing that ordering
difference too — the 5 thalweg groups alone were sufficient to match the
original connectivity everywhere that mattered.

## 2026-09-16 regeneration: closed the Ems estuary's southward tail

Per user request: close the two southernmost (lowest-latitude) wet rows of
the Ems estuary — the thin, shallow (0–4 m, right at the 2.0 m
`min_depth` floor) 1–3-cell-wide channel south of the main funnel mouth
(53.45–53.60°N, between Schiermonnikoog and Borkum, which stays open).
Added a `mask_regions` rectangle (`lon: [6.90, 7.30]`, `lat: [53.28,
53.38]`) to `nse_bathymetry.yaml`, immediately after the `Borkum` entry.

Backed up first: `bathymetry_nse.nc.bak_20260916_102753`. Ran both passes
(pass 2 needed no `fixes.yaml` edits — all 5 thalweg groups were already
`applied: true` and `phantom_islands` already `false` from the prior run).
`depth_rx0_0p20` rx0 = 0.20 (target met); Little Belt/Great Belt/Öresund/
Irish Sea all `ok`.

**Result (first pass, two rows)**: 6 cells closed total, all in the Ems
estuary, nothing else in the domain changed (confirmed with a full
domain-wide mask diff against the backup). The two explicitly-targeted
rows (53.30°N: 7.14/7.22°E; 53.35°N: 6.98/7.06/7.14°E) closed as
expected — **plus** the single cell at 53.40°N/6.98°E, which wasn't
targeted directly but became an isolated 1-cell dead-end once its only
southward neighbours closed, and `nkeep_basins: 1`'s connected-component
step pruned it automatically. New southern edge of the estuary: 53.45°N.

**Follow-up (same day): two more rows.** Extended the same rectangle to
`lon: [6.30, 7.30]`, `lat: [53.28, 53.53]` (widened west since 53.45/53.50
extend to 6.34°E, not just the 6.90–7.30 the first rectangle covered).
Backed up the post-first-fix state first
(`bathymetry_nse.nc.bak_20260916_111728`). Result: 17 more cells closed
(53.45°N and 53.50°N fully, **plus** 4 cells of 53.55°N at 6.26–6.50°E —
again not targeted directly, again `nkeep_basins`-pruned once their only
neighbour path south closed; 53.55°N's western portion at 6.02–6.18°E
stays open, connected north instead). New southern edge: 53.55°N — the
estuary funnel is now gone entirely; the coast reads as a smooth arc.
23 cells closed total across both edits, confirmed against the original
pre-edit backup, still nothing outside the Ems area touched.

**Gotcha found while doing this**: running `--accept-fixes` twice in a
row (a redundant extra invocation, not a fresh pass 1 + pass 2) produced
25 spurious opened cells far away near the Danish straits (~55.25–55.80°N,
9.4–13.0°E) that a clean single pass-1-then-pass-2 run does not produce.
Not root-caused (same category as the `phantom_islands` ordering gotcha
above) — just: **always run exactly one pass 1 then one pass 2, never
re-run `--accept-fixes` a second time to "just check something."**

### Superseded: the actual HPC baseline was a different file

Everything above this point used `bathymetry_nse.nc.bak_20260916_102753`
(the file sitting in this repo at the start of the session) as the "before"
reference and ran the full `bathymetry-regrid` two-pass pipeline to
produce the fix. Turns out **that wasn't the file HPC actually runs
against** — HPC uses `bathymetry_nse.nc.backup_20260914_075039` (despite
its filename, its content/mtime is from **2026-09-07**, older than even
the "2026-09-14 regeneration" section above).

Worse: comparing a full pipeline re-run against that real baseline showed
differences well outside the Ems box too (`depth_u`/`depth_v`/
`depth_rx0_0p20` etc. near the Skagerrak and NE England, up to ~18 m).
Root cause: `ocean-prep` (the separate repo `bathymetry-regrid` lives in)
has **uncommitted local changes** to `lib/bathymetry/smooth.py` and other
files (a new iterative "laplacian" filter mode) — see that repo's own
`git status`/`git diff` before ever comparing two bathymetry runs done at
different times, exactly as this doc's own earlier gotcha note already
warned. Any full regeneration today picks up that uncommitted code,
regardless of the Ems edit.

**Fix**: abandoned the pipeline-regeneration approach for this specific
change. Instead, patched the 23 identified cells (the same set as above —
verified this baseline has the identical pre-edit Ems shape) **directly**
in `bathymetry_nse.nc.backup_20260914_075039`'s copy, by hand, in Python:
for each cell, `bathymetry=-10.0`, `depth_fixes`/`depth_rx0_0p20`/
`depth_corrections_depth_rx0_0p20`/`depth_fixes_delta`/`depth_smooth_delta`
= NaN, `ocean_mask=0`, `wet_fraction=0.0`, `basin_labels=0`,
`mask_regions=1` — the exact convention already used by every other land
cell in the file. Also recomputed `depth_u`/`depth_v` (diagnostic-only —
confirmed the real model reads only `depth_rx0_0p20`/`ocean_mask`, per
`domain.BathymetryFile.depth_variable`/`mask_variable` in
`nse_*_model_config.yaml`/`nse_cmip6_raw.yaml`) as min-of-neighbouring-
`depth_rx0_0p20`-or-NaN-if-either-side-is-land, but only within a tight
window around Ems (lat 53.20–53.65, lon 5.90–7.30) — never touching
anything outside it.

Verified exhaustively: diffed **every variable** in the file against
`bathymetry_nse.nc.backup_20260914_075039`, not just `ocean_mask` — all 12
show changes confined to that same tight Ems window, nothing anywhere
else in the domain. This is now the authoritative result; the pipeline
runs described above were exploratory and are not what produced the
current `bathymetry_nse.nc`.

Comparison plot (cumulative, original vs. final):
`docs/bathymetry-investigation/ems_estuary_comparison.png`.
