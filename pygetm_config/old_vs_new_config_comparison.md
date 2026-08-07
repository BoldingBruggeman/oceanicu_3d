# Old vs. new system: generic model configuration comparison

**Old system**: `/tmp/run_model.py` + `/tmp/ns_3d.yaml` (setup: `"ns"`)
**New system**: `pygetm_config/{nse_from_oceanicu.yaml, generated_nse.py, generated_nse_config.yaml}` (setup: `"nse"`)

The two setups are for **different domains** (`ns` vs `nse`) with different bathymetry/open-boundary geometry, so grid-specific numbers (exact boundary indices, output filenames, etc.) aren't meaningfully comparable and are excluded. This focuses on **generic model configuration** — numerical schemes, physics options, and runtime policy that should carry over regardless of domain.

Values below are read directly from the files, not assumed. Where the old system's YAML value is never actually applied to a live pyGETM call (checked against `run_model.py`'s real code, not just the YAML), that's called out explicitly — the YAML documenting a setting is not the same as it taking effect.

## Comparison table

| Category | Setting | Old (`ns_3d.yaml` / `run_model.py`) | New (`nse_from_oceanicu.yaml` / generated) | Match? |
|---|---|---|---|---|
| Vertical coordinates | Type | GVC (`type: 2`) | GVC | ✅ |
| | `nz` | 40 | 40 | ✅ |
| | `ddu` / `ddl` | 0.75 / 0.75 | 0.75 / 0.75 | ✅ |
| | `Dgamma` | 10.0 | 10.0 | ✅ |
| | `gamma_surf` | True | true | ✅ |
| Internal pressure | Scheme | ShchepetkinMcwilliams (`type: 2`) | ShchepetkinMcwilliams | ✅ |
| Momentum | `An` | 25. — **applied** (explicit `sim.momentum.An.set(cfg.momentum.An)`) | 25.0 — applied via `Momentum(...)` constructor | ✅ (same value, different mechanism) |
| | `Am` | 0. in YAML — **never applied**: no `cfg_momentum` module is imported by `run_model.py`, and `momentum=momentum` is commented out in `final_kwargs` (line 119). Runtime value is pyGETM's own default (0.0) | 0.0 — applied | ⚠️ Same net value, but old system's YAML value is dead config |
| | `cnpar` | 1. in YAML — same dead-config issue; runtime value is pyGETM's default (1.0) | 1.0 — applied | ⚠️ Same net value, dead config in old |
| | `avmol`/`avmmol` | `avmol: 1.8e-6` in YAML — **wrong parameter name** (real pyGETM param is `avmmol`, not `avmol`) *and* dead config (same reason as above); runtime value is pyGETM's default (1.8e-6) | `avmmol: 1.8e-6` — correct name, applied | ⚠️ Same net value by coincidence (YAML value happens to equal pyGETM's own default); new system's config is the first one that's actually live |
| Radiation / light attenuation | Scheme | **Hardcoded in Python**, not YAML-configurable at all: `sim.radiation.A.set(0.7)`, `kc1.set(0.54)`, `kc2.set(3.23)` — fixed constants, no climatology | Jerlov `Type_II` (`A=0.77, kc1=1.5, kc2=14.0` from pyGETM's own Type_II preset), then `kc2` **overridden by a real KD490 climatology** (`data_assignments: simulation.radiation.kc2` ← `KD490_clim.nc`) | ❌ Genuinely different physical configuration, not just a wiring difference — old uses fixed constants (A/kc1 notably different from Type_II's own values: 0.7 vs 0.77, 0.54 vs 1.5), new uses Type_II + real space-varying attenuation data |
| Simulation construction | `advection_scheme` | Explicit `pygetm.AdvectionScheme.SUPERBEE` | Not set — falls back to pyGETM's own default | ✅ (pyGETM's default *is* SUPERBEE, confirmed) |
| | `delay_slow_ip` | Explicit `False` | Not set — falls back to pyGETM's own default | ✅ (default is `False`) |
| | `gotm` (turbulence closure config) | Explicit `gotm=Path("gotm.yaml")` — a real external GOTM config file | **Not set at all** — falls back to `None` (pyGETM's own internal k-ε defaults) | ❌ Real, unaddressed gap: no equivalent of `gotm.yaml` in the new pipeline |
| | `fabm` | `cfg.fabm.file` (from `FABM_YAML_FILE` env var, usually unset → `None` in practice) | Not set — `None` | ✅ in practice, but FABM is not modeled by any provider in the new system at all (acknowledged in `nse_from_oceanicu.yaml`'s own comment) — would need to be added if a run actually needs FABM |
| `Dmin` / `Dcrit` | | 0.1 / 0.5 | 0.1 / 0.5 | ✅ |
| `z0` | | 0.01 | 0.01 | ✅ |
| Runtime | `timestep` | 60 s | 20 s | ⚠️ Different domains (`ns` vs `nse`) have different CFL limits — the `nse` domain's own `cfl_check()` reports max stable 2D timestep ≈ 30.5 s, so 60 s would actually be *unstable* there; likely domain-driven, not a policy choice |
| | `split_factor` | 30 | 20 | ⚠️ Same caveat — macrotimestep is 1800 s (old) vs 400 s (new); domain-dependent |
| | `check_finite` | `False` | `True` | ❌ Real policy difference — new system checks for non-finite values every step, old does not |
| | `dump_on_error` | `True` | `true` | ✅ |
| | `report` / `report_totals` | Hardcoded in `main()`'s call to `run()` (`datetime.timedelta(days=1)` / `(days=7)`) — **not YAML-configurable** in the old system at all | `{days: 1}` / `{days: 7}` in YAML — same values, but now a real config field | ✅ same values; new system exposes it as config instead of a Python-only literal |
| | `profile` | `False` (boolean flag; if set, profiles under `cfg.setup`) | `null` (disabled) | ✅ equivalent (both off) |
| `debug_output` | | `False` | `false` | ✅ |
| Hydrography | Source | WOA | WOA | ✅ |
| | `constant` fallback (temp/salt) | 15 / 34 (not used — source is WOA, not constant) | 9 / 36 (not used — source is WOA, not constant) | ⚠️ Different placeholder values, but inert either way since `source: WOA` in both — worth checking if this was an intentional change or a migration slip, since it'd matter if source is ever switched to `constant` |
| Rivers | Source | emorid | emorid | ✅ |
| | Threshold | 0. | 0.0 | ✅ |
| Output | Configuration style | Boolean flags (`meteo`, `tides`, `barotropic.default_2d/3d`, `baroclinic.daily.surface/bottom`, `baroclinic.monthly`) feeding a shared `cfg_output.py` | Explicit file list, each with its own variable groups | (different mechanism, expected) |
| | Which output is actually on | `meteo=True`, `tides=False`, `barotropic.default_2d=False`, `barotropic.default_3d=False`, `baroclinic.daily.surface=True`, `baroclinic.daily.bottom=True`, `baroclinic.monthly=True` | meteo ✅, tides ✅ (**on**, unlike old), barotropic_2d ✅ (**on**, unlike old), barotropic_3d+baroclinic_3d ✅ (**on**, unlike old) — but **no daily surface/bottom baroclinic file at all** | ❌ The new system's actual active-output set doesn't correspond to the old system's flags — tides/barotropic-2d/3d are enabled in the new config despite being disabled in `ns_3d.yaml`, and the old system's daily-surface/bottom baroclinic output has no equivalent in the new file list. (The new config's own comments already flag this as illustrative/unverified — "Intervals below are illustrative placeholders, NOT verified against cfg_output.py's actual save-frequency logic".) |

## Key findings, ranked by likely impact

1. **Radiation/light attenuation is a real physical difference, not a wiring gap.** Old: fixed hardcoded constants (`A=0.7, kc1=0.54, kc2=3.23`), never exposed via YAML at all. New: Jerlov Type_II defaults (`A=0.77, kc1=1.5`) plus a real, space-varying KD490 climatology for `kc2`. This will change how light — and therefore heating/primary production, if FABM is ever added — is attenuated with depth. Worth a deliberate decision (keep the new, arguably more physically grounded setup, or replicate the old constants) rather than leaving it as an accidental byproduct of the migration.

2. **`gotm.yaml` (turbulence closure) has no equivalent in the new pipeline.** The old system always pointed pyGETM at a real external GOTM config; the new generated script never sets `gotm` at all, so it silently falls back to pyGETM's internal k-ε defaults. If `gotm.yaml` encoded real tuning (e.g., non-default stability functions), that's currently lost.

3. **`check_finite` policy flipped (`False` → `True`).** The new system now checks for non-finite values every timestep by default; the old one didn't. This is probably a deliberate safety improvement, but it does mean the new system will halt on instabilities the old one would have silently carried (or only caught via `dump_on_error`).

4. **Output configuration doesn't correspond 1:1** — the new file list enables tides/barotropic-2D/barotropic-3D output that was explicitly *off* in `ns_3d.yaml`, and has no equivalent of the old system's daily surface/bottom baroclinic files. Already flagged as unverified/illustrative in the new config's own comments — worth a real pass to match intended cadence before treating output as final.

5. **Momentum's `Am`/`cnpar`/`avmol` were dead config in the old system** (no `cfg_momentum` module, `momentum=` kwarg commented out) — only `An` was ever actually live. The values happen to equal pyGETM's own defaults, so the *numbers* match by coincidence, but the new system is the first one where all four are genuinely wired in. Worth knowing if `ns_3d.yaml`'s values were ever meant to differ from pyGETM's defaults — if so, they never took effect in the old system either.

6. Everything else checked (vertical coordinates, internal pressure scheme, `Dmin`/`Dcrit`, `z0`, advection scheme, `delay_slow_ip`, rivers, `report`/`report_totals`, `dump_on_error`, `debug_output`) matches cleanly.
