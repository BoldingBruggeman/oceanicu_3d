# pygetm-config integration for OceanICU

Files here demonstrate/use [`pygetm-config`](https://github.com/BoldingBruggeman/pygetm-config)
(a separate, independent repo at `~/source/repos/pygetm-config`) against a real
OceanICU setup. Moved here from that repo's own `examples/` directory since
they're genuinely OceanICU-specific, not generic pygetm-config material — see
that repo's `docs/yaml_vs_python.md`, `docs/providers.md`, and `docs/overview.md`
for the general design these files are an instance of.

- **`nse_from_oceanicu.yaml`** — `oceanicu_3d/nse_model_config.yaml` converted
  to pygetm-config's schema-validated format. Includes a real
  `data_assignments:` block feeding meteo/radiation/boundary forcing into the
  simulation (verified working, see below), a `domain:` section (`method:
  BathymetryFile`) pointing at the real bathymetry file, and (see next bullet)
  schema-validated
  `hydrography:`/`boundaries:`/`meteo:`/`river_discharge:` sections with
  EVERY real data-source alternative present — not just the one this setup
  actually uses. This is `pygetm_config`'s nested-by-label convention (now
  the ONLY way every `ChoiceSpec` works, not an opt-in flag — see
  pygetm-config's `docs/overview.md`): switching e.g. `meteo.source: ERA5` to
  `CMIP6` for a different run-folder, or `simulation.vertical_coordinates.
  type: GVC` to `Adaptive`, needs no other edit — every alternative's fields
  are already present, nested under its own `<label>:` sub-key, with real
  pygetm defaults for whichever alternative isn't currently active. Matches
  how `nse_model_config.yaml`'s own `hydrography`/`boundaries`/`meteo`/
  `rivers` blocks already worked (this is a close, now-validated port of
  those, values taken directly from that file); the `simulation.*` strategy
  sections (`vertical_coordinates`/`internal_pressure`/`radiation`) got the
  same treatment later, once it became clear switching those was just as
  real a need — see the file's own comments on those sections for exactly
  which values are real/tuned vs. untouched schema defaults.
- **`nse_driver.py`** — reference driver script for the above: everything
  that's genuinely just data goes through `pygetm_config.loader` generically;
  only the dynamic, threshold-filtered river-loading loop stays bespoke Python
  (domain-building used to be bespoke too, until pygetm-config grew a generic
  `domain:` `BathymetryFile` choice covering this exact NetCDF-reading
  convention). Automatically registers `oceanicu_providers.py` (below) via
  `PYGETM_CONFIG_PROVIDERS` before calling `build_schema()` — no manual export
  needed to run this file.
- **`scripts/`** — the real `<role>.data_script`/`script`/`post_data_script`
  target functions (`rivers.py`: `add_rivers`/`set_river_data`, `meteo.py`:
  `set_meteo_data`/`set_sst_proxy`, `hydrography.py`: `set_hydrography_ic`),
  one file per provider role rather than bundled into `nse_driver.py` itself
  — each is fully self-contained (own local imports, no shared state) and
  loaded the same way regardless (`pygetm_config.providers.
  load_dotted_target`, `"path/to/file.py:name"`, resolved by
  `oceanicu_providers.py`'s schema defaults / `nse_driver.py`'s fallbacks).
- **`oceanicu_providers.py`** — a populated `pygetm_config.providers` registry
  for OceanICU's data-provenance vocabulary, using the same `source:`
  discriminator convention OceanICU's own config already uses. Every real
  alternative per role is registered, verified directly against
  `lib/cfg_airsea.py`/`lib/cfg_boundaries.py`/`lib/cfg_ic.py` (not just
  whichever one `nse_model_config.yaml` happens to pick):
  `hydrography.source`: `CMEMS` | `WOA` | `constant`;
  `boundaries.barotropic.source`: `TPXO` | `CMEMS` | `CMIP6`;
  `boundaries.baroclinic.source`: `CMEMS` | `WOA` | `CMIP6`;
  `meteo.source`: `ERA5` | `CMIP6`. `river_discharge` is a known exception —
  still only `emorid`, since the real `cfg.rivers.source` is a two-level
  choice (`historic` wrapping its own `historic.source` sub-choice, plus a
  sibling `CMIP6` with a completely different per-source variable-naming
  scheme) that `pygetm_config.providers.make_provider_slot` can't represent
  as a single flat `ChoiceSpec` — a real gap in that mechanism, not something
  fixed by adding more registry entries here (see the module's own comment on
  `river_discharge`). Not wired up as a real installed entry point yet
  (`oceanicu_3d` has no `pyproject.toml` to hang one off), but works today
  anyway via `PYGETM_CONFIG_PROVIDERS` (`nse_driver.py` sets this
  automatically; only needed manually for other tools, e.g.
  `pygetm-config template`/`validate` run directly):
  ```bash
  export PYGETM_CONFIG_PROVIDERS="$(pwd)/oceanicu_providers.py:register_oceanicu_providers"
  ```
  See the module's own docstring for details, and pygetm-config's
  `docs/providers.md`.

**Editing this file in the TUI** (`pygetm-config edit`, runs pygetm-free —
see pygetm-config's own `README.md`'s "Two environments" section) needs a
schema *snapshot* (`build_schema()` itself requires a live pygetm import, so
the pygetm-free TUI can't call it directly): `dist/schema.json` in this
directory. It's a build artifact (gitignored, regenerate on demand, don't
commit) and must be dumped WITH `oceanicu_providers.py` registered, or
`hydrography:`/`boundaries:`/`river_discharge:`/`meteo:` validate silently as
unknown-but-ignored top-level keys instead of real, checked fields (`pygetm-config
dump`'s plain core schema has no providers at all — confirmed directly:
16 sections vs. 21 with providers registered, and a different `schema_fingerprint`
than what `nse_driver.py` itself actually builds and validates against):

```bash
conda activate pygetm
PYGETM_CONFIG_PROVIDERS="$(pwd)/oceanicu_providers.py:register_oceanicu_providers" \
  pygetm-config dump -o dist/schema.json

conda activate .venv-tui  # or: source <pygetm-config repo>/.venv-tui/bin/activate
pygetm-config edit --schema dist/schema.json nse_from_oceanicu.yaml
```

`build_schema()` itself logs its own provenance at `INFO` (pygetm-config's
own `schema.py`) — real question this answers: "how was this schema
obtained?" It never reads a `schema.json` in any of `nse_driver.py`'s own
code paths (`build_schema()` runs live every time); only `pygetm-config edit`
loads a pre-dumped one, since it's the one command that has to run pygetm-free.

**Path convention**: `nse_from_oceanicu.yaml` has NO hardcoded absolute paths
anywhere — every `domain.path`/`tpxo_folder`/provider `folder`/`data_assignments`
`file` is a `"${VAR}"` reference, resolved lazily by pygetm-config's own
`loader.resolve_data_path` (TODO item 15, pygetm-config repo) only at the
point of actual file access — never at `validate`/generation time, so
`--dump-python`/`--print-config`/`pygetm-config edit` all work fine even with
none of these exported (a config with an unresolved `${VAR}` still validates
and generates cleanly; only actually *opening* the file, in real execution or
when the generated script itself runs, needs the var set). Export each one
directly, pass `--data-root NAME=VALUE` (repeatable), or list them in a
`--data-roots-file` (a flat `NAME: value` YAML, gap-fill only — see
`nse_driver.py -h`). Currently referenced:

| Variable | Used for |
|---|---|
| `BATHYMETRY_FOLDER` | `domain.path` |
| `TPXO_FOLDER` | tidal harmonics — genuinely the same TPXO9 atlas regardless of role, so NOT role-suffixed (`data_assignments` `kind: tpxo`, `boundaries.barotropic.TPXO`) |
| `HYDROGRAPHY_FOLDER_CMEMS` / `HYDROGRAPHY_FOLDER` | `hydrography.CMEMS`/`.WOA` initial-condition folders — **also** the real source of boundaries.baroclinic's own WOA 3D boundary data (`boundaries.baroclinic.WOA` has no `folder` of its own at all — verified directly against `cfg_boundaries.py::data_3d`: its WOA branch reads `cfg.hydrography.WOA.folder`, not its own role's folder, deliberately reusing the same `woa_t.nc`/`woa_s.nc` climatology) |
| `BOUNDARY_FOLDER_BAROTROPIC` | `boundaries.barotropic.CMEMS` only (used to be shared with baroclinic's own CMEMS folder too — a real naming bug, fixed: each (role, source) pair now gets its own var) |
| `BOUNDARY_FOLDER_BAROTROPIC_CMIP6` | `boundaries.barotropic.CMIP6` |
| `BOUNDARY_FOLDER_BAROCLINIC_CMEMS` | `boundaries.baroclinic.CMEMS` |
| `BOUNDARY_FOLDER_BAROCLINIC_CMIP6` | `boundaries.baroclinic.CMIP6` |
| `ERA5_FOLDER` | `meteo.ERA5` (only has 2025 data on disk today — see the date-mismatch note below) |
| `CMIP6_FOLDER` | `meteo.CMIP6` (the bias-corrected dataset) |
| `RIVER_FOLDER` | `river_discharge.emorid` |
| `KD490_FOLDER` | radiation `kc2` climatology file |
| `OUTPUT_FOLDER` | `output.folder` |

`machines.yaml` already has some of these per hostname (e.g. `BATHYMETRY_FOLDER`,
verified stale for `orca` — `/data/Bathymetry/NS` doesn't exist there) — not
consumed here at all; export the correct value directly instead of relying on
that file.

To run (verified working from an arbitrary directory, not just this one):

```bash
conda activate pygetm  # needs pygetm AND pygetm-config (pip install -e ".[introspect]" from the pygetm-config repo)
export BATHYMETRY_FOLDER=... TPXO_FOLDER=... # ...and the rest of the table above
python nse_driver.py nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-02T00:00:00 --dry-run
```

To check a modified config resolves the way you expect (e.g. after switching
a provider's `source:` for a different run-folder) without building or
running anything: `--print-config` prints the fully validated/merged config
as YAML and exits. Verified this actually reflects the switch, not just the
raw file: flipping `meteo.source: ERA5` to `CMIP6` in an otherwise-identical
copy changes which block's `folder`/`model`/`scenario` end up at the top of
`meteo:` (the ERA5 block stays present too, just no longer active).

```bash
# --stop is required by argparse but unused on this path -- any valid value works
python nse_driver.py nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-02T00:00:00 --print-config
```

To see every actual native pyGETM call this makes (real user need: the
config-driven abstraction otherwise makes it hard to tell what pyGETM API
calls are actually happening underneath, e.g. when debugging why a real run
produces a particular result) -- `--log-level DEBUG` (default `INFO`) shows
each one with its real, resolved arguments, in order: domain construction,
each strategy object (`radiation`/`vertical_coordinates`/...), `Simulation(...)`
itself, every `data_assignments` `.set()`/`.type=`/`pygetm.input.from_nc(...)`
call with the actual file+variable, every output file + its requested fields,
`sim.start()`/`finish()`. Large arrays are shown as shape/dtype only, never
dumped. `DEBUG` additionally shows one line per `open_boundaries` entry (this
script's own `add_rivers`, not `pygetm_config.loader`'s, is what actually adds
rivers here, so they're not part of this — see that function's own per-river
logging via pygetm's `domain.rivers` logger instead, already visible by
default).

```bash
python nse_driver.py nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-01T01:00:00 --dry-run --log-level DEBUG
```

Sometimes even the logged native calls aren't enough — you actually want a real
breakpoint or a stray print inside the construction sequence, with no
`pygetm_config`/`nse_driver.py` abstraction in the way at all. `--dump-python
[PATH]` writes a self-contained, standalone script implementing this exact
config as literal `pygetm`/`pygetm.domain`/`pygetm.simulation` calls (`import
pygetm_config` genuinely absent from the output — verified) and exits without
building/running anything here; see pygetm-config's `pygetm_config/codegen.py`
module docstring for the "regenerate, don't hand-maintain" scoping. Verified
end to end against this exact config: the generated script's `sim.start()`
reproduces the same domain-integral salt/heat as `nse_driver.py --dry-run`
itself (34.999 g/kg, 5.06°C), confirming it's a faithful standalone
reproduction, not just syntactically-valid output.

**Rivers are included, with real discharge data** (`river_discharge.emorid.script`,
resolved above, points at this file's own `add_rivers` for POSITION;
`river_discharge.emorid.data_script` points at this file's own `set_river_data`
for the actual discharge time series — two distinct functions on the same role,
mirroring OceanICU's own `cfg_rivers.py`'s real create()/data() split. pygetm-config's
`codegen.py` embeds both functions' real source text into the generated script, not a
reference; see that repo's `loader.run_river_discharge_script`/`run_river_discharge_data_script`
and `codegen._emit_river_discharge_script`/`_emit_river_discharge_data_script` for the
mechanism, and `pygetm-config/TODO` item 9 for the design). Verified with real
per-river placement log lines matching `nse_driver.py`'s own real execution exactly
(262 real rivers, all given a real `TemporalInterpolation(Q from EMORID_1990_2024.nc)`).

```bash
python nse_driver.py nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-01T01:00:00 --dry-run --skip-unavailable-output --dump-python generated_nse.py
python generated_nse.py --dry-run --skip-unavailable-output   # no pygetm_config import, real pygetm calls only
```

`generated_nse.py` has its own real `argparse` CLI — `python generated_nse.py -h`
shows it. `--start`/`--stop`/`--dry-run`/`--load-restart`/`--save-restart`/
`--skip-unavailable-output` are genuine runtime arguments of the generated
script itself, defaulting to whatever was given to `--dump-python`, so the
SAME generated file can be rerun with different ones (`python generated_nse.py
--stop 2025-03-02T00:00:00`) without regenerating from the YAML. The one rule
that still matters: regenerate whenever `nse_from_oceanicu.yaml` itself
changes — don't hand-edit `generated_nse.py` and keep using it, or it becomes
a second, drifting source of truth.

`generated_nse.py` is structured into real functions — `create_domain()`,
`create_simulation(domain)`, `configure_output(sim)`, `run(sim)` — mirroring
this project's own `run_model.py`, not one long flat sequence of statements.
`--dump-python generated_nse.py` also writes `generated_nse_config.yaml`
alongside it: the full validated config, readable/diffable on its own instead
of embedded as one giant inline `repr()`'d dict literal. `generated_nse.py`
reads it back via `Path(__file__).parent`, not the current working directory,
so the pair — copy both files together — stays runnable regardless of where
or on which machine you invoke it from (verified: run standalone from a
scratch directory unrelated to this repo, `EXIT: 0`).

**The `sst = airsea.t2m` gap is closed too**: `set_sst_proxy` (this file,
needed for `BAROTROPIC_2D`/`3D` — pygetm's `FluxesFromMeteo` requires `sst`
set but there's no baroclinic temperature to derive it from) is now
registered via `post_data_script`, a second, distinct hook from
`river_discharge.script` — this one runs *after* `data_assignments`, since
the real fix needs `sim.airsea.t2m` to already hold a real value (verified
directly against `cfg_airsea.py`: that line lives inside its own `data()`
function, right after the meteo assignments, not in `run_model.py`'s
top-level sequence at all). Generated against a `BAROTROPIC_2D` copy of this
exact config and run standalone: `sim.start()` now succeeds completely, real
domain integrals reported, no traceback — `generated_nse.py` is a genuinely
complete, working, standalone replacement for `nse_driver.py`'s own real
execution.

Note the `2025` start date, not the `2024-03` this setup is nominally for:
`meteo.ERA5.folder` (`/data/ERA5/kaj`) only has 2025 data on this machine
right now (confirmed real 2024 ERA5 data exists at `/data/ERA5/NA/` instead,
but that folder wasn't confirmed to be the intended spatial/QC choice for
this setup — swap it in once that's confirmed, in BOTH `meteo.ERA5.folder`
and the `data_assignments:` file paths above it, which aren't auto-derived
from each other yet -- see the YAML's own comment on that).

**Current status**: `sim.start()` (and `--dry-run`) succeeds completely —
domain-building, river-loading, meteo forcing, radiation (`simulation.
radiation.jerlov_type: Type_II` -- `run_model.py` itself has
`set_jerlov_type(pygetm.Jerlov.Type_II)` commented out, real evidence this
was the intended/considered water type -- with `kc2` further overridden from
the real KD490 climatology, `/data/Kd490/KD490_clim.nc`'s `KD490_filled`
variable, `climatology: true`, mirroring `run_model.py`'s own `ObsKd: True`
branch exactly; confirmed via the InputManager's own log line that `kc2` is
registered for dynamic per-timestep updates from that real file, same
mechanism already proven for the ERA5 meteo fields), and now open-boundary
temp/salt (real CMEMS data,
`/home/kb/source/repos/boundaries/boundary_data/nse/daily/daily_2024-03-01_to_2026-01-01.nc`
— 311 boundary points, matching this setup's real boundary count exactly;
`thetao`/`so`, `on_grid: true`, boundary type forced to `SPONGE` via
pygetm-config's new `data_assignments` `kind: boundary_type`, mirroring
`cfg_boundaries.py::data_3d`'s real CMEMS branch) all verified working end to
end. Real signal this is actually taking effect, not just running: the
domain-integral salt/heat after `sim.start()` are no longer exactly
35.000/5.000 (the pre-boundary-data constants) — 34.999 g/kg /
5.06°C once real CMEMS values are read at the boundaries.

Actually advancing (`pygetm-config run ... --stop ...`, no `--dry-run`) gets
9 timesteps in (`istep=9`, `2025-03-01 00:03:00`) before a NEW, different
kind of problem: `Exception: Non-finite values found`, localized to momentum
advection (`advU`/`advV`/`ru`/`rv`, ~31 cells out of ~29000 unmasked — a
small, specific blowup, not everything). This is no longer a "missing
config" gap like the previous three — those are all closed now — but a
genuine numerical-stability question (bathymetry data quality near a
boundary, timestep/CFL, or a boundary-geometry issue) needing real
diagnostic work (inspecting `getm-dump.nc`, the 261-field dump this failure
writes out, for where exactly the blowup starts). Not attempted in this
pass.
