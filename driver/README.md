# pygetm-config integration for OceanICU

General-purpose pygetm-config driver/provider machinery for OceanICU, usable
by any domain that follows the same schema-validated config shape (today:
NSe; future domains follow the same pattern) — NOT domain-specific itself,
unlike a domain's own config, which lives under that domain's own config
directory instead (e.g. `NSe/config/nse_from_oceanicu.yaml`, see that
directory's own README.md for the real command-line invocation and
domain-specific details). This directory used to be named `pygetm_config/`
and held the domain config too — renamed to `driver/` (and the domain config
moved out) because `pygetm_config/` collided with the actual `pygetm_config`
PACKAGE name, which was confusing.

Demonstrates/uses [`pygetm-config`](https://github.com/bolding/pygetm-config)
(a separate, independent repo at `~/source/repos/pygetm-config`) against a real
OceanICU setup. Originally moved here from that repo's own `examples/` directory
since these files are genuinely OceanICU-specific, not generic pygetm-config
material — see that repo's `docs/yaml_vs_python.md`, `docs/providers.md`, and
`docs/overview.md` for the general design these files are an instance of.

- **`oceanicu_driver.py`** (renamed from `nse_driver.py` — it was never
  actually NSe-specific) — reference driver script: everything that's
  genuinely just data goes through `pygetm_config.loader` generically; only
  the dynamic, threshold-filtered river-loading loop stays bespoke Python
  (domain-building used to be bespoke too, until pygetm-config grew a generic
  `domain:` `BathymetryFile` choice covering this exact NetCDF-reading
  convention). Automatically registers `oceanicu_providers.py` (below) via
  `PYGETM_CONFIG_PROVIDERS` before calling `build_schema()` — no manual export
  needed to run this file. Takes a config path as its first positional
  argument — point it at any domain's own config, e.g.
  `NSe/config/nse_from_oceanicu.yaml`.
- **`scripts/`** — the real `<role>.data_script`/`script`/`post_data_script`
  target functions (`rivers.py`: `add_rivers`/`set_river_data`, `meteo.py`:
  `set_meteo_data`/`set_sst_proxy`, `hydrography.py`: `set_hydrography_ic`),
  one file per provider role rather than bundled into `oceanicu_driver.py`
  itself — each is fully self-contained (own local imports, no shared state)
  and loaded the same way regardless (`pygetm_config.providers.
  load_dotted_target`, `"path/to/file.py:name"`, resolved by
  `oceanicu_providers.py`'s schema defaults / `oceanicu_driver.py`'s
  fallbacks, both via the `${SCRIPT_FOLDER}` data-root — the target string
  itself may use `${VAR}`/`$VAR` syntax too, expanded lazily at load time
  the same way any other data path is).
  - `river_discharge` uses TWO distinct functions on the same role, not
    one: `script` (`add_rivers`) places each river by position, `data_script`
    (`set_river_data`) attaches its actual discharge time series — mirroring
    OceanICU's own original `cfg_rivers.py` `create()`/`data()` split.
  - `post_data_script` (`set_sst_proxy`) is a distinct hook from `script`/
    `data_script`, guaranteed to run *after* `data_assignments` — needed
    because `FluxesFromMeteo` requires `sst` set even under a non-baroclinic
    runtype (`BAROTROPIC_2D`/`3D`, which has no computed sea-surface
    temperature to derive one from), and the fix (`sst = airsea.t2m`) needs
    `t2m` to already hold a real value.
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
  anyway via `PYGETM_CONFIG_PROVIDERS` (`oceanicu_driver.py` sets this
  automatically; only needed manually for other tools, e.g.
  `pygetm-config template`/`validate` run directly):
  ```bash
  export PYGETM_CONFIG_PROVIDERS="$(pwd)/oceanicu_providers.py:register_oceanicu_providers"
  ```
  See the module's own docstring for details, and pygetm-config's
  `docs/providers.md`.
- **`data_roots.yaml.example`** — template for `--data-roots-file`
  (`pygetm_config.loader.apply_data_roots`): a flat `NAME: value` YAML
  mapping, deliberately NOT hostname-keyed (each machine reads its own copy
  — see that function's own docstring on why, and `machines.yaml`'s
  confirmed-stale-entry bug class this avoids). Copy it, rename it (anything
  you like), edit the values for the target machine's real data layout,
  then pass it to `oceanicu_driver.py` or a `--dump-python`'d generated
  script via this same flag. Precedence: `--data-root NAME=VALUE` on the
  command line always wins over this file; an already-exported real env var
  always wins over this file too (it only fills gaps, never clobbers a
  deliberate export). The values in the committed copy are THIS machine's
  own — a starting point, not necessarily correct elsewhere — sorted to
  match a domain config's own section order (domain, hydrography,
  boundaries.barotropic, boundaries.baroclinic, meteo, river_discharge,
  data_assignments, output, then driver plumbing) so it's easy to find the
  variable a given config section actually needs.

**TODO, 2026-08-19 — moving to a new machine: which folders and which env
vars.** `data_roots.yaml.example`'s committed values are bb-server1's own,
but several are stale or simply wrong for bb-server1's actual layout (found
while regenerating the NSe delta-change boundaries this session) --
recorded here so a real migration checklist exists, not fixed yet:

| env var | should point to (bb-server1, verified) | kind | ~size |
|---|---|---|---|
| `BATHYMETRY_FOLDER` | `NSe/Bathymetry` (in-repo) | repo data | small |
| `HYDROGRAPHY_FOLDER_WOA` | `/data/WOA` | external ref (WOA climatology) | 606M |
| `HYDROGRAPHY_FOLDER_CMEMS` | ? (not verified this session) | external ref | ? |
| `TPXO_FOLDER` | `/data/TPXO9` | external ref (OSU TPXO9-atlas) | — |
| `BOUNDARY_FOLDER_BAROTROPIC_CMEMS` | `/data/OceanICU/oceanicu_3d/data/NSe/CMEMS/bdy` | generated/live extraction | — |
| `BOUNDARY_FOLDER_BAROTROPIC_CMIP6` | `/data/CMIP6` (raw, partial) or the new delta-change output -- unreconciled, see below | generated | 27G |
| `BOUNDARY_FOLDER_BAROCLINIC_WOA` | `/data/WOA` | external ref | (shared with HYDROGRAPHY_FOLDER_WOA) |
| `BOUNDARY_FOLDER_BAROCLINIC_CMEMS` | `/data/OceanICU/oceanicu_3d/data/NSe/CMEMS/bdy` | generated/live extraction | — |
| `BOUNDARY_FOLDER_BAROCLINIC_CMIP6` | `/data/OceanICU/oceanicu_3d/data/NSe/CMIP6` | generated (ocean-prep `run-delta-boundaries`) | ~12G (6 combos) |
| `ERA5_FOLDER` | `/data/ERA5` | external ref (ECMWF reanalysis) | 925G |
| `CMIP6_FOLDER` | `/data/BiasCorrected` | generated (ocean-prep `bc-correct`) | 4.0T |
| `RIVER_FOLDER` | `/data/EMORID` | external ref (EMORID observations) | 1.1G |
| `RIVER_FOLDER_CMIP6` | `/data/BiasCorrected` | generated (ocean-prep `river-projection`) | (part of the 4.0T above) |
| `KD490_FOLDER` | `/data/Kd490` | external ref | 909M |
| `FABM_ERSEM_FOLDER` | not confirmed -- no real NSe files yet | external ref (ERSEM dependency data: gelbstoff/CDOM product, AMM7-EMEP N-deposition) | ? |
| `BOUNDARY_FOLDER_FABM_WOA` | not confirmed -- no real NSe files yet | external ref (WOA BGC tracer climatology) | ? |

**Fixed, 2026-08-19**: `HYDROGRAPHY_FOLDER_WOA`/`BOUNDARY_FOLDER_BAROCLINIC_WOA`
said `/server/data/WOA` (real path: `/data/WOA`); `TPXO_FOLDER` said
`/server/data/TPXO9` (real path: `/data/TPXO9`);
`BOUNDARY_FOLDER_BAROTROPIC_CMEMS`/`BOUNDARY_FOLDER_BAROCLINIC_CMEMS` said
`/home/kb/source/repos/boundaries.old/boundary_data`, which doesn't exist
on bb-server1 at all -- that was this LOCAL machine's own stale snapshot,
not bb-server1's real, live, continuously-updated CMEMS extraction. All
four now point at their real, verified bb-server1 locations.

**Still genuinely open, not a stale-value problem**:
`BOUNDARY_FOLDER_BAROTROPIC_CMIP6` still points at the raw `/data/CMIP6`
extraction rather than a delta-change output, unlike its baroclinic
counterpart (which was corrected this session) -- but this isn't fixable
by editing a path: barotropic's own delta-change equivalent (zos/uo/vo,
`run-tidal-boundaries`'s job) hasn't been generated yet, so there's
genuinely nothing real to point it at until that run happens.
`HYDROGRAPHY_FOLDER_CMEMS` also wasn't checked against bb-server1's real
layout this session (unlike the boundary CMEMS paths, which were) -- may
need the same kind of fix, not verified either way.

What "generated" actually means for a migration: `CMIP6_FOLDER`/
`RIVER_FOLDER_CMIP6` (4.0T) and `BOUNDARY_FOLDER_BAROCLINIC_CMIP6` (~12G)
are ocean-prep OUTPUT, not source data -- on a new machine these need
either copying wholesale or regenerating from scratch (hours for the
boundaries, considerably longer for the full bias-corrected meteo/river
set), not re-acquiring from a third party. Everything marked "external
ref" needs re-acquiring from its own original source (WOA, TPXO9 atlas,
Copernicus Marine Service, ECMWF, EMORID) if not copied directly.

Not attempted yet: verifying `HYDROGRAPHY_FOLDER_CMEMS`, reconciling
`BOUNDARY_FOLDER_BAROTROPIC_CMIP6` (blocked on a real `run-tidal-boundaries`
run, not a documentation fix), locating real `FABM_ERSEM_FOLDER`/
`BOUNDARY_FOLDER_FABM_WOA` data (blocked on those files existing for NSe at
all, not a path fix -- see FABM's own scaffolding-only status), or writing
an actual "how to re-acquire X" recipe per external source.

**Config convention: nested-by-label choices**. Every `ChoiceSpec` field in a
domain config (`hydrography:`, `boundaries.barotropic:`/`baroclinic:`,
`meteo:`, `simulation.vertical_coordinates:`, ...) keeps EVERY real
alternative present at once, each nested under its own `<label>:` sub-key,
not just the one currently active flattened at the top level — this is
`pygetm_config`'s own convention (the only way a `ChoiceSpec` works, not an
opt-in flag; see pygetm-config's `docs/overview.md`). Switching which one is
active (e.g. `meteo.source: ERA5` → `CMIP6`, or `simulation.
vertical_coordinates.type: GVC` → `Adaptive`) needs no other edit — every
alternative's own fields are already there, with real pygetm defaults for
whichever one isn't currently active. `NSe/config/nse_from_oceanicu.yaml` is
a real example of this shape throughout.

**Inspecting a config, without building or running anything**:
- `--print-config` prints the fully validated/merged config as YAML and
  exits — the way to check a modified config resolves the way you expect
  (e.g. after switching a provider's `source:`) without actually running it.
  Verified this genuinely reflects a switch, not just echoing the raw file:
  flipping `meteo.source: ERA5` → `CMIP6` changes which block's `folder`/
  `model`/`scenario` end up flattened at the top of `meteo:` (the ERA5 block
  stays present too, just no longer active).
- `--log-level DEBUG` (default `INFO`) shows every native pyGETM call this
  driver makes, in order, with its real, resolved arguments: domain
  construction, each strategy object (`radiation`/`vertical_coordinates`/
  ...), `Simulation(...)` itself, every `data_assignments` `.set()`/
  `.type=`/`pygetm.input.from_nc(...)` call with the actual file+variable,
  every output file and its requested fields, `sim.start()`/`finish()`.
  Large arrays are shown as shape/dtype only, never dumped. Rivers are NOT
  part of this (`driver/scripts/rivers.py`'s own `add_rivers` adds them, not
  `pygetm_config.loader`) — see their own per-river placement lines via
  pygetm's `domain.rivers` logger instead, already visible by default.

```bash
python driver/oceanicu_driver.py NSe/config/nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-02T00:00:00 --print-config
python driver/oceanicu_driver.py NSe/config/nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-01T01:00:00 --dry-run --log-level DEBUG
```

**Standalone scripts** (`--dump-python [PATH]`): for when even the logged
native calls aren't enough — a real breakpoint or a stray print inside the
construction sequence, with no `pygetm_config`/driver abstraction in the way
at all. Writes a self-contained, standalone script implementing the exact
resolved config as literal `pygetm`/`pygetm.domain`/`pygetm.simulation`
calls (`import pygetm_config` genuinely absent from the output), structured
into real functions — `create_domain()`, `create_simulation(domain)`,
`configure_output(sim)`, `run(sim)` — mirroring this project's own
`run_model.py`, not one long flat sequence of statements. Also writes a
companion `<stem>_utils.py` (argparse setup, config-loading, embedded
script-hook function bodies — kept separate so the main file stays just the
real pyGETM call sequence) and `<stem>_config.yaml` (the config values any
script-hook function can still read live at the generated script's own
runtime — trimmed to just those, see that file's own header comment for
which fields qualify). All three read each other back via
`Path(__file__).parent`, not the current working directory, so the trio —
copy all three files together — stays runnable regardless of where or on
which machine you invoke it from. The generated script has its own real
`argparse` CLI (`-h` shows it) — `--start`/`--stop`/`--dry-run`/
`--load-restart`/`--save-restart`/`--skip-unavailable-output` are genuine
runtime arguments of THAT script, defaulting to whatever was given to
`--dump-python`, so the same generated file can be rerun with different ones
without regenerating. The one rule that still matters: regenerate whenever
the source config itself changes — don't hand-edit the generated script and
keep using it, or it becomes a second, drifting source of truth (a small,
one-off tweak like a single parameter value, to test something before
touching the source config, is fine — see the generated script's own docstring).
Verified end to end against a real config (`NSe/config/nse_from_oceanicu.yaml`):
the generated script's `sim.start()` reproduces the exact same domain-integral
salt/heat as `oceanicu_driver.py --dry-run` itself, confirming it's a
faithful standalone reproduction, not just syntactically-valid output; also
verified runnable from a scratch directory entirely unrelated to this repo.

```bash
python driver/oceanicu_driver.py NSe/config/nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-01T01:00:00 --dry-run --skip-unavailable-output --dump-python NSe/config/generated_nse.py
python NSe/config/generated_nse.py --dry-run --skip-unavailable-output   # no pygetm_config import, real pygetm calls only
```

**Editing a domain config in the TUI** (`pygetm-config edit`, runs pygetm-free —
see pygetm-config's own `README.md`'s "Two environments" section) needs a
schema *snapshot* (`build_schema()` itself requires a live pygetm import, so
the pygetm-free TUI can't call it directly): `dist/schema.json` in this
directory. It's a build artifact (gitignored, regenerate on demand, don't
commit) and must be dumped WITH `oceanicu_providers.py` registered, or
`hydrography:`/`boundaries:`/`river_discharge:`/`meteo:` validate silently as
unknown-but-ignored top-level keys instead of real, checked fields (`pygetm-config
dump`'s plain core schema has no providers at all — confirmed directly:
16 sections vs. 21 with providers registered, and a different `schema_fingerprint`
than what `oceanicu_driver.py` itself actually builds and validates against):

```bash
conda activate pygetm
PYGETM_CONFIG_PROVIDERS="$(pwd)/oceanicu_providers.py:register_oceanicu_providers" \
  pygetm-config dump -o dist/schema.json

conda activate .venv-tui  # or: source <pygetm-config repo>/.venv-tui/bin/activate
export SCRIPT_FOLDER="$(pwd)/scripts"
export PYGETM_CONFIG_DATA_ASSIGNMENTS_DERIVERS="$(pwd)/oceanicu_providers.py:derive_data_assignments"
pygetm-config edit --schema dist/schema.json ../NSe/config/nse_from_oceanicu.yaml
```

Both exports matter for 'Generate script' inside the TUI specifically, not
just running the dumped schema against a real domain: `pygetm-config edit
--schema ...` NEVER calls `register_oceanicu_providers()` (that's the whole
point of the pre-built JSON snapshot -- no live pygetm/provider-registration
code runs in this pygetm-free process at all), so neither env var gets set
automatically the way `oceanicu_driver.py`'s own `main()` sets them for a
direct/--dump-python run. Without `SCRIPT_FOLDER`, 'Generate script' fails
outright (`river_discharge.script`'s real function is loaded/embedded
eagerly, at generation time, not deferred like a plain data file path).
Without `PYGETM_CONFIG_DATA_ASSIGNMENTS_DERIVERS`, generation SUCCEEDS but
silently omits boundaries.baroclinic's/meteo's own derived data_assignments
(open_boundary.temp/salt.values, simulation.airsea.t2m/d2m/...) -- see
`oceanicu_providers.derive_data_assignments`'s own docstring.

`build_schema()` itself logs its own provenance at `INFO` (pygetm-config's
own `schema.py`) — real question this answers: "how was this schema
obtained?" It never reads a `schema.json` in any of `oceanicu_driver.py`'s
own code paths (`build_schema()` runs live every time); only `pygetm-config
edit` loads a pre-dumped one, since it's the one command that has to run
pygetm-free.

**Data-path portability**: every domain config here has NO hardcoded absolute
paths anywhere — every `domain.path`/`tpxo_folder`/provider `folder`/
`data_assignments` `file` is a `"${VAR}"` reference, resolved lazily by
pygetm-config's own `loader.resolve_data_path` (and, for `.script`/
`.data_script`/`post_data_script` targets, `providers.load_dotted_target`),
only at the point of actual file access — never at `validate`/generation
time, so `--dump-python`/`--print-config`/`pygetm-config edit` all work fine
even with none of these exported. See
`data_roots.yaml.example` above for how to actually supply them.
`SCRIPT_FOLDER`/`GOTM_FOLDER` (used by every domain config's own script hooks
and `simulation.gotm`) get sensible defaults derived from this driver's own
location — every other var is domain/data-specific, see that domain's own
config directory README for its real values.

Everything above is general — applies to any domain config that follows this
same shape. For a specific domain's own real facts (its actual per-variable
data-roots table, verified end-to-end status, known open issues), see that
domain's own config directory README, e.g.
[`NSe/config/README.md`](../NSe/config/README.md).
