# NW domain silicate/carbon blow-up after ~7 simulated years

Hi,

We're seeing domain-mean silicate and carbon diverge from physically
reasonable values starting around simulated year 2016-2017 in the NSe
CMEMS production runs (`v01E_gvc`/`v01E_avc`, 2010-01-01 start). Silicate
goes from near-zero to ~3x10^8 mmol Si/m^3 (domain mean!) over a few
months; carbon climbs more gradually but with a visible kink at the same
time. Three separate diagnostic plots (pelagic silicate, large-size
POM/carbon, benthic DIC) all show the anomaly concentrated at the exact
same grid cells, so this write-up focuses on that one location.

## What's confirmed

**Location**: the anomaly sits almost exactly on open-boundary **segment
2** -- lon -6.22, lat 58.50-59.15N (the western segment north of
Scotland, per `NSe/config/nse_bdy_create.yaml`'s own documented segment
layout). Confirmed directly against the real boundary file's coordinates
on bb-server1 (`segment_id==2` → `boundary_lon=-6.22`,
`boundary_lat=58.55..59.15`) -- an exact match to where all three
anomalous fields peak.

**The raw CMEMS boundary forcing at that segment is clean.** Pulled `si`
directly from `bio_daily_2010-01-01_to_2026-08-17.nc` at segment 2 for
the window straddling the 2016-2017 onset: min/max 0.63-9.5 mmol Si/m^3,
zero NaNs. Across the *entire* 2010-2026 record at that segment, `si`
never exceeds ~14.4 mmol/m^3. So whatever's happening is downstream of
the boundary data, not a bad/corrupted input product.

**The `nse_cmems.yaml` FABM boundary assignment for silicate is a plain
1:1 read** (`N5_s` ← `si`, `boundary_condition_type: SPONGE`, no scale/
offset). Not a config/unit bug on our side as far as we can tell.

## Leading hypothesis (ERSEM-side)

All three anomalous fields map onto ERSEM pools that go through the
**same benthic-sediment machinery**, which is what makes us suspect a
shared numerical issue rather than three independent problems:

- Pelagic silicate `N5_s` ← benthic silicate `K5` (composition `s`)
- Benthic DIC `G3_c` -- same model type as `K5`
- Large-size POM/carbon ← benthic particulate pools `Q6`/`Q7`, whose
  silicate fraction remineralises into `K5` (`Q6s_aerobic`/
  `Q6s_anaerobic`, per our `fabm_ersem.yaml`)

`K3`/`K4`/`K5` (DIC/ammonium/silicate) are all instances of
**`ersem_benthic_column_dissolved_matter`** (`benthic_column_dissolved_
matter.F90`). Since none of them set `last_layer` in our config, they all
go through the `last_layer==nlayers` branch of `process_constituent`
(lines 381-451), whose benthic→pelagic exchange term is:

```fortran
P_res_int = (c_int-c_int_eq)/norm_res_int*Dm(nlayers)
_SET_BOTTOM_EXCHANGE_(info%id_pel, sms+P_res_int)
```

`norm_res_int` is a diffusivity/porosity-weighted normalisation built
from the per-layer sediment diffusivities `diff(1:3)`. Unlike most of
the rest of this module, **this term has no explicit clamp**. Those
diffusivities aren't domain-constant -- they're a per-cell diagnostic
(`ersem_benthic_column.F90`'s `bioturbation_do_bottom`) enhanced by local
bioturbation/bioirrigation activity (`Tur_enh = 1 + mtur*Ytur/(Ytur+
htur)`), so a local build-up of organic-matter-driven benthic activity
over several years is, in principle, a path to `norm_res_int` collapsing
and `P_res_int` -- and hence the exchange it injects into `N5_s`/`O3_c`/
`N4_n` -- growing without the kind of bound the rest of the module
enforces elsewhere.

This would explain why silicate, carbon, and benthic DIC all blow up
**together, at the identical cell**, only after several years (time to
drift into the degenerate regime), while the raw boundary forcing stays
clean the whole time.

## What we checked and ruled out

- **`legacy_ersem_compatibility`**: the sibling particulate-matter module
  (`benthic_column_particulate_matter.F90`) has a well-known *documented*
  runaway (`d_pen -> infinity`) in its *legacy* penetration-depth
  formulation (`(d_sms-d_pen)*sms/c_int`, no clamp) -- exactly this class
  of bug, and explicitly called out as such in the code's own comments
  (JornB, 11/3/2015). But `legacy_ersem_compatibility` is a hardcoded
  Fortran `parameter = .false.` in `shared.F90`, not a runtime config
  option, so unless scylla is running a much older ERSEM build, this
  specific path is not live. Worth a quick sanity check on which ERSEM
  commit is actually compiled there.
- **The *current* (non-legacy) penetration-depth update** in both
  `benthic_column_particulate_matter.F90`'s top-level bioturbation ODE
  (`z_mean_sms = min(D/z_mean, 0.05)*(1-exp(-z_tur/z_mean))`, hard-capped
  at 0.05 m/d) and its `_layer` submodel's exchange term (`relax`
  clamped to ±4/d, and multiplied by `(1-exp(-d_tot/d_pen))`) are both
  deliberately bounded -- we don't see an obvious divide-by-zero there.
  (One residual note: the 0.05 m/d cap has no explicit *upper* bound
  reset, so sustained growth at the cap for years is still arithmetically
  possible, even though it's not divide-by-zero. Flagging in case it's
  relevant, but we don't have a mechanism for why it would.)
- **Sediment column depth (`Dtot`/`depth_of_sediment_column`) and
  porosity/transfer constant (`poro`/`cmix`) are domain-uniform
  constants** (`benthic_column.F90` registers them with
  `missing_value=<fixed parameter>`, `source=source_none`) -- so a locally
  shallow/anomalous bathymetry at the boundary-corner cell is *not* the
  mechanism, ruling out our first guess.

## Proposed fix (draft patch against `ERSEM/src/benthic_column_dissolved_matter.F90`)

The structural asymmetry that makes this branch special: the sibling
`last_layer/=nlayers` case (used for constituents where the pore-water
concentration tapers to zero partway down the column) divides its
equilibration term by an explicit, configurable relaxation *time*
(`self%relax`, days) -- bounding how fast the system can adjust. The
`last_layer==nlayers` case -- the default, and what `K3`/`K4`/`K5` all
use -- has no such divisor. `P_res_int = (c_int-c_int_eq)/norm_res_int
*Dm(nlayers)` is an "instantaneous equilibrium" flux, and
`norm_res_int` is a purely geometric/diffusivity-dependent
normalisation that shrinks as `diff(1:3)` grows (bioturbation/
bioirrigation enhancement is individually bounded, but doesn't need to
be unbounded -- it only needs to make `norm_res_int` small relative to
a persistent `(c_int-c_int_eq)` mismatch for the resulting flux to run
away).

Draft fix: add a `relax_min` parameter (days, default `1.0`) and clamp
`P_res_int` so it can never imply adjusting faster than that, mirroring
the existing `relax` pattern used by the other branch -- while guarding
the division itself (not just the result) so a degenerate
`norm_res_int≈0` can't produce a `NaN` a post-hoc clamp would miss:

```fortran
norm_res_int = poro*sum(self%ads*residual_per_layer)
if (abs(norm_res_int) > epsilon(norm_res_int)) then
   P_res_int = (c_int-c_int_eq)/norm_res_int*Dm(nlayers)
   P_res_int = sign(min(abs(P_res_int), abs(c_int-c_int_eq)/self%relax_min), P_res_int)
else
   P_res_int = 0.0_rk
end if
```

This is a local, uncommitted edit in this machine's `FABM/ERSEM` clone
(`master`, clean otherwise) -- not yet built or tested. `relax_min=1.0
d` is a starting guess, not a tuned value; it should be checked against
how fast `K3`/`K4`/`K5` are expected to genuinely equilibrate in a
healthy run, so the clamp doesn't mask a real signal elsewhere.
Flagging as a concrete starting point for discussion rather than a
finished fix.

**The patched file has been copied to `bb-server1:/tmp/
benthic_column_dissolved_matter.F90`** so it can be dropped into the
ERSEM source tree and tried on scylla directly against `v01E_gvc`/
`v01E_avc` (or a short instrumented rerun from a restart near the
2016 onset) -- no FABM rebuild context needed on our end to make that
available.

## What we can't confirm from here

We don't have direct access to the running experiment's diagnostic
output (orca/bb-server1 only hold the boundary-forcing files, not the
model's own output). In particular `poro`/`Dtot`/`cmix` and
`diff1`/`diff2`/`diff3` are registered with `output=output_none` and
aren't in this config's `fabm_ersem_2D` output group at all, so they
likely aren't in `nse_3d.nc` regardless -- only a restart file (which
holds full state, not just requested diagnostics) can show `K5`'s state
and `D1m`/`D2m` directly.

We've written `running/diagnose_ersem_benthic_blowup.py` (in the code
repo, same branch as everything else -- `claude`) to pull whatever of
this *is* available from the experiment's own existing output/restart
files at the segment-2 location, without rerunning anything. Usage:

```bash
python3 running/diagnose_ersem_benthic_blowup.py \
    --experiment-root /path/to/experiments/NSe/CMEMS/v01E_gvc
```

If nothing useful turns up (most likely for `diff1-3`/`poro`/`Dtot`/
`cmix`, per the `output_none` point above), the next step would be a
short instrumented rerun from a restart just before the 2016 onset, with
those diagnostics added to `fabm_ersem_2D` in `nse_cmems.yaml` -- happy
to prepare that config change once we know roughly which restart to
start from.

## Suggested next steps

1. Run the diagnostic script above against `v01E_gvc`/`v01E_avc` on
   scylla; `--list-vars` first if the default variable-name guesses
   (`N5_s`, `G3_c`, `K5`/`D1m`/`D2m` substrings) don't match what's
   actually in the files.
2. Confirm which ERSEM commit is compiled on scylla (rule the
   `legacy_ersem_compatibility` path in/out for certain).
3. If `K5`'s state or the `N5_s`/`G3_c` restart values near segment 2
   are already enormous, that confirms the benthic exchange path above;
   if not, we'll need the instrumented rerun to catch `diff1-3`/`poro`/
   `Dtot` at the moment things go wrong.

Happy to jump on a call if useful.
