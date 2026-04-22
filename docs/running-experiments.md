# Running Experiments

## Overview

`run_simulation.py` is the single entry point for launching pyGETM simulations.
It replaces the old `kb.bash` / `run_scenario.sh` shell scripts.

For each experiment it:
1. Creates `<base_outdir>/<experiment>/`
2. Copies `lib/`, `run_model.py`, and the model YAML into that directory
   (exact code version is captured for reproducibility)
3. Symlinks large directories (e.g. `Bathymetry/`) into the experiment directory
4. Applies machine-specific data-folder paths from `machines.yaml`
5. Calls `run_chunks.py` from the experiment directory

---

## Quick start

```bash
# Dry run — see what would happen without executing
python run_simulation.py ns_experiments.yaml --dryrun

# Run all experiments in the file
python run_simulation.py ns_experiments.yaml

# Run one specific experiment
python run_simulation.py ns_experiments.yaml --experiment Baseline

# Override the number of MPI processes
python run_simulation.py ns_experiments.yaml --experiment Baseline --np 8
```

---

## Configuration files

### Model YAML (`ns_model_config.yaml`, `ena4_3d.yaml`, …)

Describes the physical setup: domain, forcing sources, output, switches.
Values can reference environment variables:

```yaml
meteo:
  source: os.getenv("METEO_SOURCE", "ERA5")   # ERA5 | CMIP6
  folder: Path(os.getenv("METEO_FOLDER", "/data/ERA5/NA"))
```

One model YAML covers both validation (ERA5) and scenario (CMIP6) runs —
the meteo source is selected at run time via environment variables.

### Experiment YAML (`ns_experiments.yaml`, `ns_run.yaml`, …)

Controls which experiments to run and when.

**Single run** (`ns_run.yaml`):

```yaml
area:        NS
experiment:  Baseline
model_yaml:  ns_model_config.yaml
np:          15
initial_date: "2015-12-01"
stop_date:    "2024-01-01"
chunks:       annual          # annual | monthly | daily
chunk_multiplier: 1
base_outdir:  "/data/{user}/{area}"
conda_env:    pygetm
copy:
  - gotm.yaml
symlinks:
  - Bathymetry
```

**Multiple runs** (`ns_experiments.yaml`) — shared settings at the top, per-run
overrides under `runs:`:

```yaml
base_outdir: "/data/{user}/{area}"
conda_env: pygetm
initial_date: "2015-12-01"
stop_date:    "2024-01-01"
copy:   [gotm.yaml]
symlinks: [Bathymetry]

runs:
  - area: NS
    experiment: Baseline
    model_yaml: ns_model_config.yaml
    np: 15

  - area: NS
    experiment: ssp585
    model_yaml: ns_model_config.yaml
    np: 15
    env:
      METEO_SOURCE: CMIP6
      METEO_FOLDER: /data/BiasCorrected/MPI-ESM1-2-HR/{experiment}
```

`{user}`, `{area}`, and `{experiment}` are substituted automatically in
`base_outdir` and all `env:` values.

### `machines.yaml`

Maps hostnames to data-folder environment variables.
Add an entry for each machine you run on:

```yaml
orca:
  env:
    HYDROGRAPHY_FOLDER: /server/data/WOA
    TPXO_FOLDER:        /server/data/TPXO9
    METEO_FOLDER:       /data/ERA5/NA
    RIVER_FOLDER:       /data/EMORID
    RIVER_FILE:         EMORID_1990_2024.nc
    FABM_FOLDER:        /data/FABM
    METEO_FOLDER:       /data/BiasCorrected/MPI-ESM1-2-HR
```

Machine env vars are available as template variables in `env:` blocks of the
experiment YAML (see Scenario runs below).

Find your hostname with:
```bash
python -c "import socket; print(socket.gethostname())"
```

---

## Simulation dates

Set in the experiment YAML:

| Key | Meaning |
|-----|---------|
| `initial_date` | Start of the full simulation (used for initial conditions) |
| `stop_date` | End of the full simulation |
| `start_date` | *(optional)* Resume from this date; a matching restart file must exist |

---

## Chunking

Long simulations are split into chunks so they fit within queue-system time limits.

| `chunks` value | Chunk size |
|----------------|-----------|
| `annual` *(default)* | 1 year (or `chunk_multiplier` years) |
| `monthly` | 1 month (or `chunk_multiplier` months) |
| `daily` | 1 day (or `chunk_multiplier` days) |

`run_chunks.py` handles restart files automatically: each chunk saves a restart
that the next chunk loads.  Output and log files are written to
`<base_outdir>/<experiment>/<year>/` so nothing is overwritten.

---

## Output structure

After a completed run:

```
/data/<user>/<area>/<experiment>/
    lib/                  ← exact copy of lib/ used for this run
    run_model.py          ← exact run script used
    run_simulation.py     ← launcher used
    ns_model_config.yaml    ← model config used
    gotm.yaml             ← GOTM config used
    Bathymetry -> ...     ← symlink to bathymetry directory
    2016/                 ← output for year 2016
        *.nc
        *.log
        restart_ns_20170101.nc
    2017/
        ...
```

---

## Scenario runs

Scenario experiments (CMIP6 forcing) use the same model YAML as validation runs.
Set `METEO_SOURCE` and `METEO_FOLDER` in the `env:` block of the run entry:

```yaml
- area: NS
  experiment: ssp585
  model_yaml: ns_model_config.yaml
  np: 15
  env:
    METEO_SOURCE: CMIP6
    METEO_FOLDER: "{BC_METEO_BASE}/{experiment}"
```

`{experiment}` expands to the experiment name (`ssp585`); `{BC_METEO_BASE}` is
resolved from `machines.yaml` so the model path is never hardcoded in the
experiment YAML.  Adding `ssp126` is a one-line change.
