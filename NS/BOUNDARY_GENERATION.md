# NS boundary condition generation — runbook

Same layout and workflow as NSe (see `NSe/BOUNDARY_GENERATION.md` for the
full rationale/history this pattern comes from) — this file only covers
what's specific to NS.

## 0. The boundary point file — read this first

**Canonical copy:** `NS/Bathymetry/ns_bdy_lonlat.txt` (69 points). It
lives alongside the other grid/mask artifacts under `NS/Bathymetry/`
(moved there from the old shared `oceanicu_3d/Bathymetry/` directory
2026-10-22, matching NSe's own per-area convention).

**This file is a generated artifact — never hand-edit it.** The single
source of truth is `NS/config/ns_bdy_segments.yaml`, a short list of
4 segments (index form: `side`/`l`/`mstart`/`mstop`) transcribed directly
from `ns_from_oceanicu.yaml`'s own known-good `open_boundaries:` block —
a restatement of what was already running, not a reinterpretation.

### Generating/verifying it — `pygetm-config boundaries-generate`/`boundaries-check-points`

```bash
conda activate pygetm
pygetm-config boundaries-generate \
  --domain NS/config/ns_from_oceanicu.yaml \
  --segments NS/config/ns_bdy_segments.yaml \
  --bdy-lonlat-out NS/Bathymetry/ns_bdy_lonlat.txt \
  --apply-to NS/config/ns_from_oceanicu.yaml \
  --report /tmp/ns_boundaries.md \
  --data-root BATHYMETRY_FOLDER=NS/Bathymetry
```

**Run this after every bathymetry regeneration:**

```bash
pygetm-config boundaries-check-points \
  --domain NS/config/ns_from_oceanicu.yaml \
  --lonlat NS/Bathymetry/ns_bdy_lonlat.txt \
  --data-root BATHYMETRY_FOLDER=NS/Bathymetry
```

**2026-10-22 generation:** 69 points, 0 on land — clean on the first real
run against the current `bathymetry_ns.nc` (read via the `DomainSnapshot`
domain method — see that file's own header comment for why NS/ENA4/ENA8
use this instead of AMM7/NSe's plain `BathymetryFile`).

### If a segment needs to move, shrink, or be dropped

1. Edit `NS/config/ns_bdy_segments.yaml` — change the relevant segment's
   index tuple (or use guide-point form, `lonlat_a`/`lonlat_b`).
2. Re-run `pygetm-config boundaries-generate` (above) to regenerate both
   `ns_bdy_lonlat.txt` and the `open_boundaries:` block together.

## 1. The rest of the pipeline — not yet set up for NS

Unlike NSe, NS does not yet have its own `ocean-prep`-side configs
(`ns_bdy_create.yaml`, `ns_tidal_bdy.yaml`, `ns_delta_bdy.yaml`) — see the
Data Sources page / `NS/metadata.yaml`'s own `input_generation` entries.
Only the `oceanicu_driver.py`-level domain config (`ns_from_oceanicu.yaml`,
covered above) exists today.
