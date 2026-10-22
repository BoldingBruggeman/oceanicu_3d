# AMM7 boundary condition generation — runbook

Same layout and workflow as NSe (see `NSe/BOUNDARY_GENERATION.md` for the
full rationale/history this pattern comes from) — this file only covers
what's specific to AMM7.

## 0. The boundary point file — read this first

**Canonical copy:** `AMM7/Bathymetry/amm7_bdy_lonlat.txt` (701 points).
It lives alongside the other grid/mask artifacts under `AMM7/Bathymetry/`
(moved there from the old shared `oceanicu_3d/Bathymetry/` directory
2026-10-22, matching NSe's own per-area convention — see below), not the
YAML domain configs in `AMM7/config/`.

**This file is a generated artifact — never hand-edit it.** The single
source of truth is `AMM7/config/amm7_bdy_segments.yaml`, a short list of
5 segments (index form: `side`/`l`/`mstart`/`mstop`) transcribed directly
from `amm7_from_oceanicu.yaml`'s own known-good `open_boundaries:` block
— a restatement of what was already running, not a reinterpretation. Both
the point file and the `open_boundaries:` block are derived from that one
file, so they can't silently diverge (same reasoning as NSe's own
`nse_bdy_segments.yaml` — see that area's doc for the 2026-08-03
"123 points silently on land" incident this design exists to prevent).

### Generating/verifying it — `pygetm-config boundaries-generate`/`boundaries-check-points`

```bash
conda activate pygetm
pygetm-config boundaries-generate \
  --domain AMM7/config/amm7_from_oceanicu.yaml \
  --segments AMM7/config/amm7_bdy_segments.yaml \
  --bdy-lonlat-out AMM7/Bathymetry/amm7_bdy_lonlat.txt \
  --apply-to AMM7/config/amm7_from_oceanicu.yaml \
  --report /tmp/amm7_boundaries.md \
  --data-root BATHYMETRY_FOLDER=AMM7/Bathymetry
```

This calls pygetm's own real `add_by_index`/`adjust_mask` under the hood,
so a segment that's on land, overlaps another, or crosses another fails
loudly here, with the offending segment named — instead of silently
writing a bad point file. `--apply-to` patches
`amm7_from_oceanicu.yaml`'s own `open_boundaries:` list in place (by
position), preserving each segment's existing `type_2d`/`type_3d` unless
`--update-types` is also given.

**Run this after every bathymetry regeneration:**

```bash
pygetm-config boundaries-check-points \
  --domain AMM7/config/amm7_from_oceanicu.yaml \
  --lonlat AMM7/Bathymetry/amm7_bdy_lonlat.txt \
  --data-root BATHYMETRY_FOLDER=AMM7/Bathymetry
```

Exits non-zero and prints every `ON LAND: lon lat` point if the current
bathymetry has moved any boundary point onto land.

**2026-10-22 generation:** 701 points, 0 on land — clean on the first
real run against the current `amm7_topo_getm.nc`.

### If a segment needs to move, shrink, or be dropped

1. Edit `AMM7/config/amm7_bdy_segments.yaml` — change the relevant
   segment's index tuple (or use guide-point form, `lonlat_a`/`lonlat_b`,
   for an easier-to-pick-by-eye new segment).
2. Re-run `pygetm-config boundaries-generate` (above) to regenerate both
   `amm7_bdy_lonlat.txt` and the `open_boundaries:` block together.

## 1. The rest of the pipeline — not yet set up for AMM7

Unlike NSe, AMM7 does not yet have its own `ocean-prep`-side configs
(`amm7_bdy_create.yaml`, `amm7_tidal_bdy.yaml`, `amm7_delta_bdy.yaml`) —
see the Data Sources page / `AMM7/metadata.yaml`'s own `input_generation`
entries, which flag these as "Config not yet committed." Only the
`oceanicu_driver.py`-level domain config (`amm7_from_oceanicu.yaml`,
covered above) exists today. Once those configs are added, this section
should be filled in the same shape as NSe's own §1 (historical reference /
future scenario T-S / future scenario SSH-currents, one stage each).
