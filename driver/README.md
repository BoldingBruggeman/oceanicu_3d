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

Demonstrates/uses [`pygetm-config`](https://github.com/BoldingBruggeman/pygetm-config)
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
  fallbacks, both via the `${SCRIPT_FOLDER}` data-root -- see pygetm-config
  TODO item 34).
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
pygetm-config edit --schema dist/schema.json ../NSe/config/nse_from_oceanicu.yaml
```

`build_schema()` itself logs its own provenance at `INFO` (pygetm-config's
own `schema.py`) — real question this answers: "how was this schema
obtained?" It never reads a `schema.json` in any of `oceanicu_driver.py`'s
own code paths (`build_schema()` runs live every time); only `pygetm-config
edit` loads a pre-dumped one, since it's the one command that has to run
pygetm-free.

**Data-path portability**: every domain config here has NO hardcoded absolute
paths anywhere — every `domain.path`/`tpxo_folder`/provider `folder`/
`data_assignments` `file` is a `"${VAR}"` reference, resolved lazily by
pygetm-config's own `loader.resolve_data_path` (TODO item 15, pygetm-config
repo) and (for `.script`/`.data_script`/`post_data_script` targets, TODO item
34) `providers.load_dotted_target`, only at the point of actual file access —
never at `validate`/generation time, so `--dump-python`/`--print-config`/
`pygetm-config edit` all work fine even with none of these exported. Export
each one directly, pass `--data-root NAME=VALUE` (repeatable), or list them
in a `--data-roots-file` (a flat `NAME: value` YAML, gap-fill only — see
`data_roots.yaml.example` in this directory, and `oceanicu_driver.py -h`).
`SCRIPT_FOLDER`/`GOTM_FOLDER` (used by every domain config's own script hooks
and `simulation.gotm`) get sensible defaults derived from this driver's own
location — every other var is domain/data-specific, see that domain's own
config directory README for its real values.

For the actual command-line invocation, verified end-to-end status, and the
full per-variable table for a real domain config, see
[`NSe/config/README.md`](../NSe/config/README.md).
