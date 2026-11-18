# Open items after the 2026-11 refactor (2026-11-16)

All work is committed and pushed (`claude` branch); bb-server1 runs the
current code.

## To do

- **Website deploy** (content already regenerated on bb-server1; old content
  in `/data/OceanICU/backups/hugo_before_refactor_20261115`):

  ```
  ssh bb-server1 'cd /opt/ocean-src/ocean-post && /opt/conda-envs/ocean-stack/bin/python deploy_ghpages.py --apply'
  ```

- **scylla**: follow `scylla_update_20261115.md` (commands in
  `scylla_update_20261115.txt`).
- **Old MPI delta-change files** on bb-server1 (~26 GB), kept after the
  2026-11-15 regeneration: delete when no longer needed.

  ```
  /data/OceanICU/oceanicu_3d/data/NSe/CMIP6/MPI-ESM1-2-HR/{ssp126,ssp370}/bdy_old_20261115/
  ```

- **Uncommitted notes** in oceanicu_3d: `scylla_update_20261115.md`,
  `scylla_update_20261115.txt`, `nse_forcing_status.md`, this file.

## Review items not done (DISCUSS items outside the accepted decisions)

| Repo | Item |
|---|---|
| ocean-data | S3 done 2026-11-18; S4 variable renaming: decision pending, see decision_variable_renaming_S4.md |
| ocean-plot | A10 done 2026-11-18 (shared bar = one range; per-panel limits ignored with a warning) |
| ocean-plot | C3 done 2026-11-18 (OceanPlotter engine removed) |
| ocean-plot | C5 done 2026-11-18 (deprecation warning; legacy example removed) |
| ocean-plot | D5 done 2026-11-18 |
| ocean-stack | E7 done 2026-11-18 (notes/ kept local, ops guides in guides/) |
| ocean-stack | E5 ocean-run outside the stack (kept outside for now) |
| ocean-prep | D13 done 2026-11-18 except the bathymetry report (by choice) |

Review files (summaries and full texts): reviews_20261112/ -- ocean-data.md, ocean-post.md, ocean-prep.md,
ocean-plot-common-stack.md (2026-11-12/13 reviews).
