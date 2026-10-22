# ENA4 boundary condition generation — runbook

Same layout and workflow as NSe (see `NSe/BOUNDARY_GENERATION.md` for the
full rationale/history this pattern comes from) — this file only covers
what's specific to ENA4.

## 0. The boundary point file — read this first

**Canonical copy:** `ENA4/Bathymetry/ena4_bdy_lonlat.txt` (213 points).
It lives alongside the other grid/mask artifacts under
`ENA4/Bathymetry/` (moved there from the old shared
`oceanicu_3d/Bathymetry/` directory 2026-10-22, matching NSe's own
per-area convention).

**This file is a generated artifact — never hand-edit it.** The single
source of truth is `ENA4/config/ena4_bdy_segments.yaml`, a short list of
4 segments (index form: `side`/`l`/`mstart`/`mstop`) transcribed directly
from `ena4_from_oceanicu.yaml`'s own known-good `open_boundaries:` block
— a restatement of what was already running, not a reinterpretation.

### Generating/verifying it — `pygetm-config boundaries-generate`/`boundaries-check-points`

```bash
conda activate pygetm
pygetm-config boundaries-generate \
  --domain ENA4/config/ena4_from_oceanicu.yaml \
  --segments ENA4/config/ena4_bdy_segments.yaml \
  --bdy-lonlat-out ENA4/Bathymetry/ena4_bdy_lonlat.txt \
  --apply-to ENA4/config/ena4_from_oceanicu.yaml \
  --report /tmp/ena4_boundaries.md \
  --data-root BATHYMETRY_FOLDER=ENA4/Bathymetry
```

**Run this after every bathymetry regeneration:**

```bash
pygetm-config boundaries-check-points \
  --domain ENA4/config/ena4_from_oceanicu.yaml \
  --lonlat ENA4/Bathymetry/ena4_bdy_lonlat.txt \
  --data-root BATHYMETRY_FOLDER=ENA4/Bathymetry
```

**2026-10-22 generation:** 213 points, 0 on land — clean on the first
real run against the current `bathymetry_ena4.nc` (`DomainSnapshot`
domain method).

### If a segment needs to move, shrink, or be dropped

1. Edit `ENA4/config/ena4_bdy_segments.yaml` — change the relevant
   segment's index tuple (or use guide-point form, `lonlat_a`/`lonlat_b`).
2. Re-run `pygetm-config boundaries-generate` (above) to regenerate both
   `ena4_bdy_lonlat.txt` and the `open_boundaries:` block together.

## 1. The rest of the pipeline — not yet set up for ENA4

ENA4 is not currently one of the site's published areas
(`regen_hosts.yaml`'s `enabled_areas:` lists only NSe/NS/AMM7), and does
not yet have its own `ocean-prep`-side configs (`ena4_bdy_create.yaml`,
`ena4_tidal_bdy.yaml`, `ena4_delta_bdy.yaml`). Only the
`oceanicu_driver.py`-level domain config (`ena4_from_oceanicu.yaml`,
covered above) exists today.
