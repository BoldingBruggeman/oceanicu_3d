# ENA8 boundary condition generation — runbook

Same layout and workflow as NSe (see `NSe/BOUNDARY_GENERATION.md` for the
full rationale/history this pattern comes from) — this file only covers
what's specific to ENA8.

## 0. The boundary point file — read this first

**Canonical copy:** `ENA8/Bathymetry/ena8_bdy_lonlat.txt` (374 points).
It lives alongside the other grid/mask artifacts under
`ENA8/Bathymetry/` (moved there from the old shared
`oceanicu_3d/Bathymetry/` directory 2026-10-22, matching NSe's own
per-area convention).

**This file is a generated artifact — never hand-edit it.** The single
source of truth is `ENA8/config/ena8_bdy_segments.yaml`, a short list of
3 segments (index form: `side`/`l`/`mstart`/`mstop`) transcribed directly
from `ena8_from_oceanicu.yaml`'s own known-good `open_boundaries:` block
— a restatement of what was already running, not a reinterpretation.

### Generating/verifying it — `pygetm-config boundaries-generate`/`boundaries-check-points`

```bash
conda activate pygetm
pygetm-config boundaries-generate \
  --domain ENA8/config/ena8_from_oceanicu.yaml \
  --segments ENA8/config/ena8_bdy_segments.yaml \
  --bdy-lonlat-out ENA8/Bathymetry/ena8_bdy_lonlat.txt \
  --apply-to ENA8/config/ena8_from_oceanicu.yaml \
  --report /tmp/ena8_boundaries.md \
  --data-root BATHYMETRY_FOLDER=ENA8/Bathymetry
```

**Run this after every bathymetry regeneration:**

```bash
pygetm-config boundaries-check-points \
  --domain ENA8/config/ena8_from_oceanicu.yaml \
  --lonlat ENA8/Bathymetry/ena8_bdy_lonlat.txt \
  --data-root BATHYMETRY_FOLDER=ENA8/Bathymetry
```

**2026-10-22 generation:** 374 points, 0 on land — clean on the first
real run against the current `bathymetry_ena8.nc` (`DomainSnapshot`
domain method).

### If a segment needs to move, shrink, or be dropped

1. Edit `ENA8/config/ena8_bdy_segments.yaml` — change the relevant
   segment's index tuple (or use guide-point form, `lonlat_a`/`lonlat_b`).
2. Re-run `pygetm-config boundaries-generate` (above) to regenerate both
   `ena8_bdy_lonlat.txt` and the `open_boundaries:` block together.

## 1. The rest of the pipeline — not yet set up for ENA8

ENA8 is not currently one of the site's published areas
(`regen_hosts.yaml`'s `enabled_areas:` lists only NSe/NS/AMM7), and does
not yet have its own `ocean-prep`-side configs (`ena8_bdy_create.yaml`,
`ena8_tidal_bdy.yaml`, `ena8_delta_bdy.yaml`). Only the
`oceanicu_driver.py`-level domain config (`ena8_from_oceanicu.yaml`,
covered above) exists today.
