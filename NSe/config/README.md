# NSe pygetm-config setup

`nse_from_oceanicu.yaml` — `oceanicu_3d/nse_model_config.yaml` converted to
[`pygetm-config`](https://github.com/bolding/pygetm-config)'s
schema-validated format, run via `../../driver/oceanicu_driver.py` (a
general-purpose driver, not NSe-specific, and the place to look for how any
of this actually works — see [`driver/README.md`](../../driver/README.md)
for the config convention, `--print-config`/`--log-level DEBUG`/
`--dump-python` mechanisms, data-path portability, and the TUI editor. This
file only covers what's specific to NSe.)

`old_vs_new_config_comparison.md` (also here) documents the conversion from
`nse_model_config.yaml` to this file in detail.

## Data-path variables

No hardcoded absolute paths anywhere in `nse_from_oceanicu.yaml` — see
[`driver/README.md`](../../driver/README.md)'s "Data-path portability"
section and [`driver/data_roots.yaml.example`](../../driver/data_roots.yaml.example).

## Running

```bash
conda activate pygetm  # needs pygetm AND pygetm-config (pip install -e ".[introspect]" from the pygetm-config repo)
python driver/oceanicu_driver.py NSe/config/nse_from_oceanicu.yaml --start ... --stop ... --dry-run --data-roots-file driver/data_roots.yaml.example
```

`--print-config`, `--log-level DEBUG`, and `--dump-python` all work as
described in `driver/README.md`.

## Current status

Domain construction, open boundaries, hydrography, boundaries, meteo, and
river discharge all build and run end to end with real data. Data
availability (which dates a given machine's own copies of ERA5/EMORID/etc.
actually cover) is a per-machine concern, not a property of this config —
see `driver/data_roots.yaml.example` and export the values that are actually
correct for the machine you're running on.

Known issues will be added here as they turn up.

## Fields — what each section means for NSe specifically

This is *not* pygetm-config's generic schema help (that's available live via
the web/textual frontends' own field descriptions) — it's what each section's
value actually is for NSe, and why, where that's known. Anything below
without a cited source is a structural description of what the section
configures, not a claim about why NSe's particular numbers were chosen.

- **`domain`** — `BathymetryFile` pointing at `bathymetry_nse.nc`,
  `depth_variable: depth_rx0_0p20` — the rx0 (Beckmann–Haidvogel) hydrostatic
  consistency criterion used to smooth this bathymetry was 0.20; other rx0
  thresholds may exist as separate variables in the same file (see
  `ocean-prep`'s `bathymetry-regrid`/`bathymetry-splice` output for how this
  file was built — not re-derived here). `z0: 0.01` m is the (uniform)
  bottom roughness length.

- **`open_boundaries`** — 9 segments (4×WEST, 3×NORTH, 1×EAST, 1×SOUTH), all
  `FLATHER_TRANSPORT`. The exact `l`/`mstart`/`mstop` indices are grid-index
  geometry derived from where NSe's own coastline/domain edge actually falls
  on its grid — they are NOT independently meaningful numbers and will
  differ for any other domain; don't copy them as a template without
  re-deriving from that domain's own bathymetry.

- **`simulation`** — the physically substantive choices, cross-checked
  against the old `ns_3d.yaml`/`run_model.py` system in
  [`old_vs_new_config_comparison.md`](old_vs_new_config_comparison.md):
  - `vertical_coordinates: GVC` (40 layers, `ddu=ddl=0.75`, `Dgamma=10.0`,
    `gamma_surf: true`) — matches the old system exactly (verified, not a
    migration guess).
  - `internal_pressure: ShchepetkinMcwilliams` — also matches the old system.
  - `radiation: TwoBand` using pyGETM's own `Type_II` Jerlov preset
    (`A=0.77, kc1=1.5`), with `kc2` overridden by a real space-varying KD490
    climatology via `data_assignments`. **This is a genuine physical change
    from the old system**, which used fixed hardcoded constants
    (`A=0.7, kc1=0.54, kc2=3.23`) with no climatology at all — not a wiring
    difference, an actual different light-attenuation physics choice. See
    finding #1 in the comparison doc.
  - `airsea: FluxesFromMeteo` (`ROSATI_MIYAKODA` shortwave, `CLARK`
    longwave, dew-point humidity, evaporation on) — this is the standard
    bulk-formula path driven by `meteo:` below, not independently
    NSe-specific.
  - `gotm` is **not set** — falls back to pyGETM's internal k-ε defaults.
    The old system pointed at a real external `gotm.yaml`; whether that file
    encoded non-default tuning that's now silently lost is an open question
    (comparison doc finding #2), not resolved here.

- **`runtime`** — `timestep: 20`, `split_factor: 30` (so a 600 s
  macrotimestep). These are domain-driven, not a policy choice: NSe's own
  `cfl_check()` reports a max stable 2D timestep of ≈30.5 s, so the old
  system's `ns_3d.yaml` value of 60 s (a *different* domain, `ns`) would
  actually be unstable here. `check_finite: false` here — note the
  comparison doc's finding #3 that at least one other NSe-derived config had
  this flipped to `true` as a deliberate safety improvement over the old
  system's default-off; check the value in the specific file you're running,
  don't assume it's off everywhere.

- **`data_assignments`** — each entry says what fills a given field and how.
  For NSe: `simulation.radiation.{A,kc1,kc2}` set constants (the KD490
  override actually lives under `boundaries`/a separate assignment target,
  not shown as a `kind: file` entry in this excerpt — check the live file
  for the current mechanism); `open_boundaries.{z,u,v}` come from TPXO tidal
  prediction (`kind: tpxo`); `open_boundary.{temp,salt}` are `SPONGE`
  boundary-condition-type only (the actual T/S *values* at the sponge come
  from `boundaries.baroclinic` below, not from `data_assignments` — the two
  are separate mechanisms, see `boundaries.fabm`'s own inline comment in the
  YAML for the same pattern spelled out for FABM tracers).

- **`output`** — variable *groups* (named field lists, e.g. `tidal`,
  `barotropic_2d`, `fabm_ersem`) are reusable building blocks; `files`
  assigns groups to actual NetCDF outputs with their own interval/dtype.
  Per the comparison doc's finding #4, the active-output set here (tides,
  barotropic 2D/3D on; no daily surface/bottom baroclinic file) does *not*
  correspond 1:1 to the old system's flags, and the intervals are flagged in
  the file's own history as illustrative placeholders — treat current
  `active`/`interval` values as provisional, not verified against real
  save-frequency requirements.

- **`hydrography`** — `source: WOA` (climatological initial conditions).
  CMEMS is present as a configured-but-inactive alternative (`source:` is
  the switch). No NSe-specific rationale for WOA-over-CMEMS is recorded
  anywhere found in this repo's history — if there is one, it belongs here;
  currently just inherited from the old system (comparison doc: also WOA).

- **`boundaries`** — three independent boundary types (`barotropic`,
  `baroclinic`, `fabm`), each with its own `source` switch across
  TPXO/CMEMS/CMIP6/WOA as applicable. Worth noting: `boundaries.barotropic`
  and `boundaries.baroclinic`'s CMIP6 stanzas both default to
  `MPI-ESM1-2-HR`/`ssp585`, while `meteo.CMIP6` below defaults to
  `GFDL-ESM4`/`ssp126` — **these are inconsistent with each other** in this
  file. Whether that's intentional (independently-chosen defaults, expected
  to be overridden together at run time) or a leftover from incremental
  editing is not established; flagging rather than guessing.

- **`meteo`** — `source: ERA5` (reanalysis forcing) with a `CMIP6` stanza as
  the configured-but-inactive future-scenario alternative, same
  source-switch pattern as `boundaries`. See the model/scenario-mismatch
  note just above.

- **`river_discharge`** — `source: emorid` (EMORID river climatology/
  dataset), `threshold: 0` (no minimum-discharge cutoff). Matches the old
  system exactly (comparison doc).

- **`fabm`** — off by default (`ERSEM.file` commented out); the YAML's own
  inline comment explains this is guarded to `runtype: BAROCLINIC`
  regardless, and is independent of `boundaries.fabm`'s WOA-sourced tracer
  boundaries/ICs (also off by default, separately).
