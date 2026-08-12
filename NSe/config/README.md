# NSe pygetm-config setup

`nse_from_oceanicu.yaml` — `oceanicu_3d/nse_model_config.yaml` converted to
[`pygetm-config`](https://github.com/BoldingBruggeman/pygetm-config)'s
schema-validated format, run via `../../driver/oceanicu_driver.py` (a
general-purpose driver, not NSe-specific — see `driver/README.md`).

Includes a real `data_assignments:` block feeding meteo/radiation/boundary
forcing into the simulation (verified working, see below), a `domain:`
section (`method: BathymetryFile`) pointing at the real bathymetry file, and
schema-validated `hydrography:`/`boundaries:`/`meteo:`/`river_discharge:`
sections with EVERY real data-source alternative present — not just the one
this setup actually uses. This is `pygetm_config`'s nested-by-label
convention (the ONLY way every `ChoiceSpec` works, not an opt-in flag — see
pygetm-config's `docs/overview.md`): switching e.g. `meteo.source: ERA5` to
`CMIP6` for a different run-folder, or `simulation.vertical_coordinates.
type: GVC` to `Adaptive`, needs no other edit — every alternative's fields
are already present, nested under its own `<label>:` sub-key, with real
pygetm defaults for whichever alternative isn't currently active. Matches how
`nse_model_config.yaml`'s own `hydrography`/`boundaries`/`meteo`/`rivers`
blocks already worked (this is a close, validated port of those, values taken
directly from that file); the `simulation.*` strategy sections
(`vertical_coordinates`/`internal_pressure`/`radiation`) got the same
treatment later — see the file's own comments on those sections for exactly
which values are real/tuned vs. untouched schema defaults.

`old_vs_new_config_comparison.md` (also here) documents the conversion from
`nse_model_config.yaml` to this file in detail.

## Data-path variables

No hardcoded absolute paths anywhere in `nse_from_oceanicu.yaml` — every
`domain.path`/`tpxo_folder`/provider `folder`/`data_assignments` `file` is a
`"${VAR}"` reference, resolved lazily (see `driver/README.md`'s "Data-path
portability" section). Export each one directly, pass `--data-root
NAME=VALUE` (repeatable), or list them in a `--data-roots-file` (see
`driver/data_roots.yaml.example`, and `oceanicu_driver.py -h`). Currently
referenced by this config:

| Variable | Used for |
|---|---|
| `BATHYMETRY_FOLDER` | `domain.path` |
| `TPXO_FOLDER` | tidal harmonics — genuinely the same TPXO9 atlas regardless of role, so NOT role-suffixed (`data_assignments` `kind: tpxo`, `boundaries.barotropic.TPXO`) |
| `HYDROGRAPHY_FOLDER_CMEMS` | `hydrography.CMEMS` initial-condition folder |
| `HYDROGRAPHY_FOLDER_WOA` | `hydrography.WOA` initial-condition folder |
| `BOUNDARY_FOLDER_BAROTROPIC_CMEMS` | `boundaries.barotropic.CMEMS` |
| `BOUNDARY_FOLDER_BAROTROPIC_CMIP6` | `boundaries.barotropic.CMIP6` |
| `BOUNDARY_FOLDER_BAROCLINIC_WOA` | `boundaries.baroclinic.WOA` — its own real folder now (a `woa_t.nc`/`woa_s.nc` climatology, same real files as `HYDROGRAPHY_FOLDER_WOA` typically points at, but independently settable — reversed from an earlier version of this setup, which silently reused `HYDROGRAPHY_FOLDER`/hydrography.WOA.folder instead, matching upstream `cfg_boundaries.py`'s own convention; see `driver/oceanicu_providers.py`'s own comment on the WOA choice for why this project went the other way) |
| `BOUNDARY_FOLDER_BAROCLINIC_CMEMS` | `boundaries.baroclinic.CMEMS` |
| `BOUNDARY_FOLDER_BAROCLINIC_CMIP6` | `boundaries.baroclinic.CMIP6` |
| `ERA5_FOLDER` | `meteo.ERA5` — the PARENT of the real per-extraction subfolder; `meteo.ERA5.folder_template: "{model}"` / `model: "kaj"` resolves it to `${ERA5_FOLDER}/kaj` (mirrors `meteo.CMIP6`'s own `folder_template`/`{model}`/`{scenario}` shape — "model" here names the extraction/processing run, not a climate model). Only has 2025 data on disk today — see the date-mismatch note below |
| `CMIP6_FOLDER` | `meteo.CMIP6` (the bias-corrected dataset) |
| `RIVER_FOLDER` | `river_discharge.emorid` |
| `KD490_FOLDER` | radiation `kc2` climatology file |
| `OUTPUT_FOLDER` | `output.folder` |

`machines.yaml` already has some of these per hostname (e.g. `BATHYMETRY_FOLDER`,
verified stale for `orca` — `/data/Bathymetry/NS` doesn't exist there) — not
consumed here at all; export the correct value directly instead of relying on
that file.

## Running

All commands below assume the repo root as the current directory (verified
working from an arbitrary directory too, adjusting paths accordingly).

To run:

```bash
conda activate pygetm  # needs pygetm AND pygetm-config (pip install -e ".[introspect]" from the pygetm-config repo)
export BATHYMETRY_FOLDER=... TPXO_FOLDER=... # ...and the rest of the table above
python driver/oceanicu_driver.py NSe/config/nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-02T00:00:00 --dry-run
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
python driver/oceanicu_driver.py NSe/config/nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-02T00:00:00 --print-config
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
dumped. `DEBUG` additionally shows one line per `open_boundaries` entry
(`driver/scripts/rivers.py`'s own `add_rivers`, not `pygetm_config.loader`'s,
is what actually adds rivers here, so they're not part of this — see that
function's own per-river logging via pygetm's `domain.rivers` logger
instead, already visible by default).

```bash
python driver/oceanicu_driver.py NSe/config/nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-01T01:00:00 --dry-run --log-level DEBUG
```

Sometimes even the logged native calls aren't enough — you actually want a real
breakpoint or a stray print inside the construction sequence, with no
`pygetm_config`/driver abstraction in the way at all. `--dump-python [PATH]`
writes a self-contained, standalone script implementing this exact config as
literal `pygetm`/`pygetm.domain`/`pygetm.simulation` calls (`import
pygetm_config` genuinely absent from the output — verified), plus (pygetm-config
TODO item 35) a companion `<stem>_utils.py` holding argparse setup/config-loading/
embedded script-hook function bodies, so the main generated file stays just
the real pyGETM call sequence. Exits without building/running anything here;
see pygetm-config's `pygetm_config/codegen.py` module docstring for the
"regenerate, don't hand-maintain" scoping. Verified end to end against this
exact config: the generated script's `sim.start()` reproduces the same
domain-integral salt/heat as `oceanicu_driver.py --dry-run` itself (34.999
g/kg, 5.06°C), confirming it's a faithful standalone reproduction, not just
syntactically-valid output.

**Rivers are included, with real discharge data** (`river_discharge.emorid.script`
points at `driver/scripts/rivers.py`'s `add_rivers` for POSITION;
`river_discharge.emorid.data_script` points at that same file's
`set_river_data` for the actual discharge time series — two distinct
functions on the same role, mirroring OceanICU's own `cfg_rivers.py`'s real
create()/data() split. pygetm-config's `codegen.py` embeds both functions'
real source text into the generated `_utils.py` companion, not a reference;
see that repo's `loader.run_river_discharge_script`/
`run_river_discharge_data_script` and
`codegen._emit_river_discharge_script`/`_emit_river_discharge_data_script`
for the mechanism, and `pygetm-config/TODO` item 9 for the design). Verified
with real per-river placement log lines matching `oceanicu_driver.py`'s own
real execution exactly (262 real rivers, all given a real
`TemporalInterpolation(Q from EMORID_1990_2024.nc)`).

```bash
python driver/oceanicu_driver.py NSe/config/nse_from_oceanicu.yaml --start 2025-03-01T00:00:00 --stop 2025-03-01T01:00:00 --dry-run --skip-unavailable-output --dump-python NSe/config/generated_nse.py
python NSe/config/generated_nse.py --dry-run --skip-unavailable-output   # no pygetm_config import, real pygetm calls only
```

`generated_nse.py` has its own real `argparse` CLI — `python generated_nse.py -h`
shows it. `--start`/`--stop`/`--dry-run`/`--load-restart`/`--save-restart`/
`--skip-unavailable-output` are genuine runtime arguments of the generated
script itself, defaulting to whatever was given to `--dump-python`, so the
SAME generated file can be rerun with different ones without regenerating
from the YAML. The one rule that still matters: regenerate whenever
`nse_from_oceanicu.yaml` itself changes — don't hand-edit `generated_nse.py`
and keep using it, or it becomes a second, drifting source of truth.

`generated_nse.py` is structured into real functions — `create_domain()`,
`create_simulation(domain)`, `configure_output(sim)`, `run(sim)` — mirroring
this project's own `run_model.py`, not one long flat sequence of statements.
`--dump-python` also writes a companion `generated_nse_config.yaml` (the full
validated config, readable/diffable on its own) and `generated_nse_utils.py`
(argparse/config-loading/script-hook bodies, TODO item 35). All three read
each other back via `Path(__file__).parent`, not the current working
directory, so the trio — copy all three files together — stays runnable
regardless of where or on which machine you invoke it from (verified: run
standalone from a scratch directory unrelated to this repo, `EXIT: 0`).

**The `sst = airsea.t2m` gap is closed too**: `set_sst_proxy`
(`driver/scripts/meteo.py`, needed for `BAROTROPIC_2D`/`3D` — pygetm's
`FluxesFromMeteo` requires `sst` set but there's no baroclinic temperature to
derive it from) is registered via `post_data_script`, a second, distinct
hook from `river_discharge.script` — this one runs *after*
`data_assignments`, since the real fix needs `sim.airsea.t2m` to already
hold a real value (verified directly against `cfg_airsea.py`: that line
lives inside its own `data()` function, right after the meteo assignments,
not in `run_model.py`'s top-level sequence at all). Generated against a
`BAROTROPIC_2D` copy of this exact config and run standalone: `sim.start()`
succeeds completely, real domain integrals reported, no traceback —
`generated_nse.py` is a genuinely complete, working, standalone replacement
for `oceanicu_driver.py`'s own real execution.

Note the `2025` start date, not the `2024-03` this setup is nominally for:
`meteo.ERA5`'s real folder (`${ERA5_FOLDER}/kaj`) only has 2025 data on this
machine right now (confirmed real 2024 ERA5 data exists at `/data/ERA5/NA/`
instead, but that folder wasn't confirmed to be the intended spatial/QC
choice for this setup — swap it in once that's confirmed, via
`meteo.ERA5.model`).

**Current status**: `sim.start()` (and `--dry-run`) succeeds completely —
domain-building, river-loading, meteo forcing, radiation (`simulation.
radiation.jerlov_type: Type_II` -- `run_model.py` itself has
`set_jerlov_type(pygetm.Jerlov.Type_II)` commented out, real evidence this
was the intended/considered water type -- with `kc2` further overridden from
the real KD490 climatology, `/data/Kd490/KD490_clim.nc`'s `KD490_filled`
variable, `climatology: true`, mirroring `run_model.py`'s own `ObsKd: True`
branch exactly; confirmed via the InputManager's own log line that `kc2` is
registered for dynamic per-timestep updates from that real file, same
mechanism already proven for the ERA5 meteo fields), and open-boundary
temp/salt (real CMEMS data,
`/home/kb/source/repos/boundaries/boundary_data/nse/daily/daily_2024-03-01_to_2026-01-01.nc`
— 311 boundary points, matching this setup's real boundary count exactly;
`thetao`/`so`, `on_grid: true`, boundary type forced to `SPONGE` via
pygetm-config's `data_assignments` `kind: boundary_type`, mirroring
`cfg_boundaries.py::data_3d`'s real CMEMS branch) all verified working end to
end. Real signal this is actually taking effect, not just running: the
domain-integral salt/heat after `sim.start()` are no longer exactly
35.000/5.000 (the pre-boundary-data constants) — 34.999 g/kg /
5.06°C once real CMEMS values are read at the boundaries.

Actually advancing (no `--dry-run`) gets 9 timesteps in (`istep=9`,
`2025-03-01 00:03:00`) before a NEW, different kind of problem: `Exception:
Non-finite values found`, localized to momentum advection (`advU`/`advV`/
`ru`/`rv`, ~31 cells out of ~29000 unmasked — a small, specific blowup, not
everything). This is no longer a "missing config" gap — those are all closed
— but a genuine numerical-stability question (bathymetry data quality near a
boundary, timestep/CFL, or a boundary-geometry issue) needing real
diagnostic work (inspecting `getm-dump.nc`, the 261-field dump this failure
writes out, for where exactly the blowup starts). Not attempted in this pass.
