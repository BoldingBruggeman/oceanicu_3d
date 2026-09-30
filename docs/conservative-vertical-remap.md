# Conservative vertical remap for FABM restart ICs (2026-09-30)

`driver/scripts/hydrography.py`'s `set_hydrography_ic` seeds FABM state
from a restart file (`fabm.ERSEM.restart_file`, e.g. a "perpetual ERSEM"
snapshot reused across runs) when one is configured. This note covers why
the previous approach silently corrupted that seeding whenever the saved
and fresh-start vertical grids didn't line up, and the conservative remap
that replaced it.

A rendered, interactive version of this note (with real-data comparison
charts) is published as a Claude artifact: *Conservative Vertical Remap*.

## The problem

`fabm.ERSEM.restart_file` points at a real pygetm restart-format NetCDF
(`add_restart()`/`load_restart()` shape) -- e.g.
`/data/OceanICU/oceanicu_3d/data/NSe/FABM/init/restart_ersem.nc`, which
carries `hnt` (layer thickness) and `zt` (surface elevation) alongside 74
real ERSEM state variables and ~136 physical fields (temp/salt/U/V/...).

The original implementation read this file via `sim.load_restart()`,
narrowing `sim.output_manager.fields` to just the FABM state variable
names first so temp/salt (already set from WOA/CMEMS) were left alone.
That's wrong for this file: `load_restart()` sets every field with
`on_grid=OnGrid.ALL`, which pygetm's own `pygetm/input/__init__.py`
explicitly documents as skipping vertical interpolation (`if array.z and
on_grid != OnGrid.ALL:`). It assumes the file's own layer *k* **is** this
run's own layer *k* -- true for a genuine continuation restart (same run,
same grid, by construction), false here: `restart_ersem.nc` is a snapshot
from a *different* run, saved with whatever layer heights that run had at
save time. This run's own fresh-start layer heights (computed from its
own water depth / vertical-coordinate state) can differ -- for GVC by
however much the free-surface elevation differs at the two moments, and
for Adaptive coordinates potentially much more, since Adaptive layers
also respond to the run's own accumulated stratification history, which a
fresh start has none of yet.

Doing a blind index-to-index copy in that case silently misplaces every
tracer's real vertical structure. This is worse than not seeding at all:
the result still looks like a plausible profile (right shape, right
units, no NaNs) while every value sits at the wrong depth. A missing
initial condition is obviously missing; this one is quietly wrong.

## The fix: interpolate the cumulative profile

Conservative remapping of a per-layer-*mean* field reduces to a simpler
problem: interpolate its *cumulative* (depth-integrated) profile at the
target interfaces, then difference and divide by the target layer
thickness. This is exact for a piecewise-constant source, and needs no
bespoke overlap-integration code -- just `numpy.interp`/`searchsorted` on
a running sum.

```
C(z_i) = sum_{j<i} c_j * h_j        -- cumulative content down to source interface i
c_k^target = (C(z_k) - C(z_{k+1})) / (z_k - z_{k+1})   -- interpolate C at the target's own interfaces, then difference
```

Implemented as `_conservative_remap_column(source_vals, source_ifaces,
target_ifaces)`, vectorized across every tracer within a column (they
share the same source/target interfaces, only the values differ). `nz_src`
and `nz_tgt` need not match -- the remap interpolates on real depth, never
on layer index, so it works just as well for a genuine resolution change
as for a same-resolution depth/shape mismatch.

### Validation

Prototyped and validated directly against the real `restart_ersem.nc`
before wiring it in:

- **Identity remap** (source grid onto itself): recovers the exact source
  values to floating-point precision.
- **Conservation**, remapping a real `N3_n` column onto a synthetic
  uniform-thickness grid spanning the same total depth: target integral
  matches the source integral to floating-point precision (relative error
  < 1e-9).
- **A real GVC target grid, 25% shallower** than the source column's
  save-time depth (same vertical-coordinate parameters as NSe's own
  `nse_cmip6.yaml`): the remapped column's integral came out at 56.3% of
  the source's -- correct, not a shortfall of the method, since a 25%
  shallower target column doesn't cover the source's deepest, most
  nitrate-rich layers at all.
- **An illustrative Adaptive-like mismatch** (same total depth, layers
  synthetically re-concentrated at a different pycnocline -- no live
  Adaptive run available, since that needs a real stratification history
  to evolve against): the remap recovered the column's content exactly
  (100%) despite the very different layer shape, confirming it isn't
  bounded by a depth mismatch the way GVC's own drift is -- it's a pure
  interpolation, agnostic to *why* the two grids differ.

## Two more real bugs, found seeding the actual production tracer set

Wiring the validated remap into `_seed_fabm_state_from_restart` and
running it against the full, real FABM state set (not just one test
tracer) surfaced two further issues, both fixed in the same pass:

1. **Pelagic vs. benthic state variables.** A real ERSEM restart carries
   both pelagic (z-resolved, e.g. `N3_n`, `O2_o`) and benthic (sediment,
   no z dimension at all -- `Q1_c`, `Q6_*`, `K1_p`, `H1_c`, `Y2_c`, ...)
   state variables. `sim.fabm.state_variables` includes both, so treating
   every tracer as pelagic and `np.stack`-ing them together failed
   outright, mixing `(nz, ny, nx)` and `(ny, nx)` arrays. Fixed by
   splitting on each variable's own shape: pelagic gets the conservative
   vertical remap; benthic (no vertical structure to remap) gets a direct
   horizontal copy.
2. **A restart-file gap at 3 real grid cells.** `check_finite` on a real
   run flagged exactly 3 non-finite values in several benthic fields.
   Traced via the run's own `check_finite`-triggered dump (`getm-dump.nc`)
   to yt=64,65,66, xt=107 (~51.7-51.8N, 1.06E, off the Colne estuary mouth,
   Essex) -- confirmed directly that `restart_ersem.nc`'s own `zt`/`hnt`
   are *also* NaN at those exact points, so it's a real gap in the file
   itself, not specific to any one tracer. Traced further to a
   bathymetry-mask history: `ocean_mask` at those 3 cells was briefly
   flipped to land in one intermediate bathymetry snapshot
   (`bathymetry_nse.nc.bak_20260916_111728`) before being corrected back
   -- whatever domain build produced the "perpetual ERSEM" restart never
   (re-)populated those cells once they were reclassified back to wet.

## A fix that made things worse before it made them better

The first fix for the 3-cell gap replaced any non-finite value at an
active cell with `pygetm.constants.FILL_VALUE`. That's wrong: FILL_VALUE
is a sentinel for cells the model never touches (masked/dry); writing it
into a genuinely *active* cell means FABM's own biogeochemistry integrates
that physically-absurd concentration forward like any other value. A real
run confirmed this exactly: IC-time `check_finite` passed fine (FILL_VALUE
is merely a large *finite* number, not NaN/inf), but the run then failed
at istep=40 with several pelagic (`N3_n`, `N4_n`, `O2_o`, `O3_TA`) and
benthic (`K3_n`, `K4_n`, `G2_o`, `ben_col_*`, `ben_nit_G4n`) tracers newly
non-finite -- FILL_VALUE had diverged within 40 timesteps.

Fixed by filling bad-but-wet cells from the **nearest valid (wet, finite)
neighbor** instead, for both pelagic (whole column) and benthic
(per-cell), via the standard nearest-fill recipe
(`scipy.ndimage.distance_transform_edt` with `return_indices=True`). This
preserves local spatial structure -- a coastal cell gets a value close to
its real neighbors, not a flat domain-wide default -- and was confirmed to
reproduce the real neighboring column's own values exactly. `FILL_VALUE`
is now only ever written to genuinely dry (masked) cells, matching this
hook's own WOA/CMEMS temp/salt masking convention.

## A third real bug: MPI domain decomposition

`restart_ersem.nc` holds the model's full global `(yt, xt)` domain. Under
real MPI spatial decomposition, each rank's own `sim.T` only covers one
local tile of it -- reading the file unsliced only ever worked because
every run so far had been serial or MPI-without-decomposition (which
reduces to the whole domain, so that was a special case of the general
problem, not a separate one).

Two further wrinkles surfaced fixing this:

- `sim.T.mask.all_values`/`sim.T.zf.all_values` **include halo cells**.
  `restart_ersem.nc`'s own `hnt`/`zt` were written from the halo-free
  interior. Confirmed directly: file `(251, 257)` vs. `.all_values`
  `(255, 261)` -- exactly halo=2 on each side. Fixed by reading `.values`
  (halo-excluded) instead of `.all_values`, matching `pygetm.core.Array`'s
  own convention.
- Plain `sim.T.tiling.subdomain2rawslices()` isn't enough: pygetm's real
  load-balanced decomposition (`Tiling.autodetect`, `max_protrude=0.5` by
  default) lets a tile's footprint protrude past the true global domain
  edge, to keep tile sizes uniform while skipping all-land tiles. Hit for
  real at NP=183: one edge rank's raw slice landed entirely beyond the
  file's 257-wide global array, giving a silent zero-width slice
  instead of that rank's real local width. Fixed by switching to
  `subdomain2slices()`, which returns the *clipped* (always in-bounds)
  global slice to read plus the matching slice into the rank's own full
  local tile shape to place it at -- the same local/global pair pygetm's
  own gather/scatter code uses for this exact reason. The protruding
  remainder (always land/masked in practice) is left as a placeholder the
  existing non-finite-skip logic already treats as "no data here".

Validated on the real restart file with a real `pygetm.parallel.Tiling`
(2x2 decomposition): each rank's own slice runs cleanly, and the
reassembled global array is bit-for-bit identical to a single-rank run.

## Where this lives

```
driver/scripts/hydrography.py
    set_hydrography_ic(sim, domain, config)      # call site, fabm.ERSEM.restart_file
        _seed_fabm_state_from_restart(sim, restart_path)      # nested -- see below
            _conservative_remap_column(source_vals, source_ifaces, target_ifaces)   # nested
```

Both helpers are nested *inside* `set_hydrography_ic`, not module-level
siblings. `pygetm_config.codegen`'s `--dump-python` only inlines a
`data_script` hook's own source (`inspect.getsource` on
`set_hydrography_ic` alone) -- it never inlines a sibling module-level
helper the hook calls. An earlier version had them as top-level functions
and hit exactly this: the generated standalone script got the call site
but not the definitions (`NameError`), on both a single-core and a
20-rank MPI run. Same class of bug as `rivers.py`'s `_apply_calendar_
suffix`, 2026-09-15 -- see also the `codegen-script-hook-self-contained`
note.
