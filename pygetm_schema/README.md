# pygetm-schema integration for OceanICU

Files here demonstrate/use [`pygetm-schema`](https://github.com/BoldingBruggeman/pygetm-schema)
(a separate, independent repo at `~/source/repos/pygetm-schema`) against a real
OceanICU setup. Moved here from that repo's own `examples/` directory since
they're genuinely OceanICU-specific, not generic pygetm-schema material — see
that repo's `docs/yaml_vs_python.md`, `docs/providers.md`, and `docs/overview.md`
for the general design these files are an instance of.

- **`nse_from_oceanicu.yaml`** — `oceanicu_3d/nse_model_config.yaml` converted
  to pygetm-schema's schema-validated format. Includes a real
  `data_assignments:` block feeding meteo/radiation/boundary forcing into the
  simulation (verified working, see below), a `bathymetry:` section pointing
  at the real bathymetry file, and (see next bullet) schema-validated
  `hydrography:`/`boundaries:`/`meteo:`/`river_discharge:` sections with
  EVERY real data-source alternative present — not just the one this setup
  actually uses. This is `pygetm_schema`'s nested-by-label convention (now
  the ONLY way every `ChoiceSpec` works, not an opt-in flag — see
  pygetm-schema's `docs/overview.md`): switching e.g. `meteo.source: ERA5` to
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
  that's genuinely just data goes through `pygetm_schema.loader` generically;
  only the dynamic, threshold-filtered river-loading loop stays bespoke Python
  (domain-building used to be bespoke too, until pygetm-schema grew a generic
  `bathymetry:` schema section covering this exact NetCDF-reading convention).
  Automatically registers `oceanicu_providers.py` (below) via
  `PYGETM_SCHEMA_PROVIDERS` before calling `build_schema()` — no manual export
  needed to run this file.
- **`oceanicu_providers.py`** — a populated `pygetm_schema.providers` registry
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
  scheme) that `pygetm_schema.providers.make_provider_slot` can't represent
  as a single flat `ChoiceSpec` — a real gap in that mechanism, not something
  fixed by adding more registry entries here (see the module's own comment on
  `river_discharge`). Not wired up as a real installed entry point yet
  (`oceanicu_3d` has no `pyproject.toml` to hang one off), but works today
  anyway via `PYGETM_SCHEMA_PROVIDERS` (`nse_driver.py` sets this
  automatically; only needed manually for other tools, e.g.
  `pygetm-schema template`/`validate` run directly):
  ```bash
  export PYGETM_SCHEMA_PROVIDERS="$(pwd)/oceanicu_providers.py:register_oceanicu_providers"
  ```
  See the module's own docstring for details, and pygetm-schema's
  `docs/providers.md`.

**Path convention**: a hardcoded absolute path (tried first) is wrong the
moment this runs on another machine, and a path relative to the current
directory or the config file's own location breaks once `nse_driver.py`/the
config live in a separate run-folder per run, at a location that's the
user's choice, unrelated to where the shared data actually lives. So
`bathymetry.path` in `nse_from_oceanicu.yaml` is just a filename
(`bathymetry_nse.nc`); `nse_driver.py` resolves its folder from the
`BATHYMETRY_FOLDER` environment variable, exactly like OceanICU's own
`run_model.py` already resolves `TPXO_FOLDER`/`ERA5_FOLDER`/`RIVER_FOLDER`/
`HYDROGRAPHY_FOLDER`/`FABM_FOLDER` (`os.getenv(VAR, <default>)`, see
`run_model.py:263-469`) — `machines.yaml` already has a `BATHYMETRY_FOLDER`
key per hostname for exactly this, just not consumed by `run_model.py`
itself (only `run_simulation.py`'s separate symlink-into-the-rundir step uses
it). **Found while wiring this up**: `machines.yaml`'s `BATHYMETRY_FOLDER`
for `orca` is `/data/Bathymetry/NS`, which doesn't exist on this machine —
stale. `nse_driver.py`'s fallback default (used only when the env var isn't
set) points at the real, verified location instead
(`oceanicu_3d/Bathymetry/`); fix `machines.yaml`'s entry (or export
`BATHYMETRY_FOLDER` correctly) to rely on the env var alone. ERA5/EMORID
paths still don't follow this convention yet (see the date-mismatch note
below) — a natural next step, not done here.

To run (verified working from an arbitrary directory, not just this one):

```bash
conda activate pygetm  # needs pygetm AND pygetm-schema (pip install -e ".[introspect]" from the pygetm-schema repo)
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
script's own `add_rivers`, not `pygetm_schema.loader`'s, is what actually adds
rivers here, so they're not part of this — see that function's own per-river
logging via pygetm's `domain.rivers` logger instead, already visible by
default).

```bash
python nse_driver.py nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-01T01:00:00 --dry-run --log-level DEBUG
```

Sometimes even the logged native calls aren't enough — you actually want a real
breakpoint or a stray print inside the construction sequence, with no
`pygetm_schema`/`nse_driver.py` abstraction in the way at all. `--dump-python
[PATH]` writes a self-contained, standalone script implementing this exact
config as literal `pygetm`/`pygetm.domain`/`pygetm.simulation` calls (`import
pygetm_schema` genuinely absent from the output — verified) and exits without
building/running anything here; see pygetm-schema's `pygetm_schema/codegen.py`
module docstring for the "regenerate, don't hand-maintain" scoping. **Caveat**:
rivers here are added by this script's own bespoke `add_rivers()` (dynamic,
threshold-filtered from the real EMORID file at run time), not from a static
config `rivers:` list codegen can read from, so the generated script does NOT
include them — add that loop by hand in the output if you need river forcing
there too. Verified end to end against this exact config: the generated
script's `sim.start()` reproduces the same domain-integral salt/heat as
`nse_driver.py --dry-run` itself (34.999 g/kg, 5.06°C), confirming it's a
faithful standalone reproduction, not just syntactically-valid output.

```bash
python nse_driver.py nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-01T01:00:00 --dry-run --dump-python generated_nse.py
python generated_nse.py   # no pygetm_schema import, real pygetm calls only
```

`generated_nse.py` has its own real `argparse` CLI — `python generated_nse.py -h`
shows it. `--start`/`--stop`/`--dry-run`/`--load-restart`/`--save-restart` are
genuine runtime arguments of the generated script itself, defaulting to
whatever was given to `--dump-python`, so the SAME generated file can be rerun
with different ones (`python generated_nse.py --stop 2025-03-02T00:00:00`)
without regenerating from the YAML. The one rule that still matters: regenerate
whenever `nse_from_oceanicu.yaml` itself changes — don't hand-edit
`generated_nse.py` and keep using it, or it becomes a second, drifting source
of truth. The rivers caveat above still applies regardless.

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
pygetm-schema's new `data_assignments` `kind: boundary_type`, mirroring
`cfg_boundaries.py::data_3d`'s real CMEMS branch) all verified working end to
end. Real signal this is actually taking effect, not just running: the
domain-integral salt/heat after `sim.start()` are no longer exactly
35.000/5.000 (the pre-boundary-data constants) — 34.999 g/kg /
5.06°C once real CMEMS values are read at the boundaries.

Actually advancing (`pygetm-schema run ... --stop ...`, no `--dry-run`) gets
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
