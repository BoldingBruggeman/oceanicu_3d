#!/usr/bin/env python3
"""Launch one or more pyGETM simulations, capturing all configuration.

Replaces kb.bash / run_scenario.sh.

Each run:
  1. Creates  <base_outdir>/<experiment>/
  2. Copies   lib/  into that directory   (exact code version captured)
  3. Copies   run_model.py + model YAML   (exact scripts captured)
  4. Copies   any extra files listed under ``copy:``
  5. Creates  symlinks for large dirs     (e.g. Bathymetry/)
  6. Applies  machine-specific env vars   from machines.yaml
  7. Runs     run_chunks.py  with cwd = the experiment directory

Machine-specific data-folder paths are looked up by hostname in
machines.yaml (or the file given by ``machines_file:`` in the sim config).

Usage
-----
    run_simulation.py <sim_yaml> [--area AREA] [--experiment EXP]
                                  [--base-outdir DIR] [--np N] [--dryrun]

    # single-run config with CLI overrides
    run_simulation.py ns_run.yaml --experiment Sensitivity1
    run_simulation.py ns_run.yaml --base-outdir /scratch/{user}/{area}

    # multi-run config (runs: list in YAML)
    run_simulation.py campaign.yaml --dryrun

Sim YAML format
---------------
Single run:

    area: NS
    experiment: Baseline
    model_yaml: ns_model_config.yaml
    np: 15
    initial_date: "2015-12-01"
    stop_date:    "2024-01-01"
    # start_date: "2020-01-01"   # resume from a different date
    chunks: annual               # annual | monthly | daily
    chunk_multiplier: 1
    base_outdir: "/data/{user}/{area}"
    conda_env: pygetm            # omit to run in the active environment
    copy:                        # files to copy into outdir
      - gotm.yaml
    symlinks:                    # dirs/files to symlink into outdir
      - name: Bathymetry         # link name in outdir
        target: "{BATHYMETRY_FOLDER}"  # resolved from machines.yaml

Multiple runs (shared settings + per-run overrides):

    base_outdir: "/data/{user}/{area}"
    conda_env: pygetm
    initial_date: "2015-12-01"
    stop_date:    "2024-01-01"
    copy:
      - gotm.yaml
    symlinks:
      - Bathymetry

    runs:
      - area: NS
        experiment: Baseline
        model_yaml: ns_model_config.yaml
        np: 15
      - area: ENA4
        experiment: ObsKd
        model_yaml: ena4_3d.yaml
        np: 25
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def _load_machine_env(machines_file: Path) -> dict:
    """Return env-var dict for the current hostname, or {} if not found."""
    if not machines_file.is_file():
        return {}
    data = _load_yaml(machines_file)
    hostname = socket.gethostname()
    # Try exact match first, then prefix match (useful for cluster nodes)
    machine_cfg = data.get(hostname)
    if machine_cfg is None:
        for key in data:
            if hostname.startswith(key):
                machine_cfg = data[key]
                break
    if machine_cfg is None:
        print(f"  [machines] no entry for {hostname!r} in {machines_file}")
        return {}
    return machine_cfg.get('env', {})


def _resolve_outdir(base_outdir: str, area: str) -> Path:
    user = os.getenv('USER', os.getenv('LOGNAME', 'user'))
    return Path(base_outdir.format(user=user, area=area))


def _setup_outdir(outdir: Path, source_dir: Path,
                  model_yaml: str,
                  copy_files: list,
                  symlinks: list,
                  dry_run: bool) -> None:
    """Create outdir, copy lib/ and named files, create symlinks."""
    print(f"\n  Setting up: {outdir}")

    if outdir.exists() and not dry_run:
        print(f"ERROR: {outdir} already exists — move or delete it first")
        sys.exit(1)
    if not dry_run:
        outdir.mkdir(parents=True, exist_ok=True)

    # Always copy lib/
    lib_src = source_dir / 'lib'
    lib_dst = outdir / 'lib'
    if dry_run:
        print(f"  copy  lib/  →  {lib_dst}/")
    else:
        if lib_dst.exists():
            shutil.rmtree(lib_dst)
        shutil.copytree(lib_src, lib_dst)
        print(f"  copied lib/ ({len(list(lib_dst.rglob('*.py')))} .py files)")

    # Always copy run_model.py
    _copy_file(source_dir / 'run_model.py', outdir / 'run_model.py', dry_run)

    # Copy model YAML
    model_yaml_src = source_dir / model_yaml
    _copy_file(model_yaml_src, outdir / model_yaml, dry_run)

    # Copy extra files
    for name in copy_files:
        src = source_dir / name
        if src.exists():
            _copy_file(src, outdir / name, dry_run)
        else:
            print(f"  SKIP  {name} (not found)")

    # Create symlinks — symlink_pairs is a list of (link_name, target_path)
    for link_name, target in symlinks:
        dst = outdir / link_name
        if dry_run:
            print(f"  symlink  {link_name}  →  {target}")
        else:
            if dst.is_symlink() or dst.exists():
                if dst.is_symlink():
                    dst.unlink()
                else:
                    dst.unlink() if dst.is_file() else shutil.rmtree(dst)
            dst.symlink_to(target)
            if target.exists():
                print(f"  symlinked {link_name} → {target}")
            else:
                print(f"  symlinked {link_name} → {target}  (target not mounted)")


def _copy_file(src: Path, dst: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"  copy  {src.name}  →  {dst}")
    else:
        shutil.copy2(src, dst)
        print(f"  copied {src.name}")


def _conda_env_bin(conda_env: str) -> str | None:
    """Return the bin/ directory of a named conda environment, or None."""
    try:
        result = subprocess.run(
            ['conda', 'info', '--base'],
            capture_output=True, text=True, check=True,
        )
        base = result.stdout.strip()
        return str(Path(base) / 'envs' / conda_env / 'bin')
    except Exception:
        return None


def _build_run_chunks_cmd(
    source_dir: Path,
    outdir: Path,
    model_yaml: str,
    np: int,
    chunks: str,
    chunk_multiplier: int,
    conda_env: str | None,
    dry_run: bool,
) -> tuple[list[str], dict]:
    """Return (command, extra_env) for run_chunks.py.

    When conda_env is set the env's bin/ is prepended to PATH so that
    both python and mpiexec resolve from the right environment — avoids
    the 'pop_var_context' bash error that conda run triggers in subprocesses.
    """
    extra_env: dict = {}
    if conda_env:
        bin_dir = _conda_env_bin(conda_env)
        if bin_dir:
            extra_env['PATH'] = bin_dir + ':' + os.environ.get('PATH', '')
        else:
            print(f"  WARNING: could not locate conda env {conda_env!r}; using current PATH")

    cmd: list[str] = [
        'python',
        str(source_dir / 'run_chunks.py'),
        str(outdir / 'run_model.py'),   # script arg — inside outdir
        str(outdir / model_yaml),        # setup arg  — inside outdir
        str(outdir),                     # base_outdir — IS the experiment dir (no --exp)
        str(np),
    ]

    chunk_flag = {
        'monthly': '--monthly_chunks',
        'daily':   '--daily_chunks',
    }.get(chunks)   # annual = default (no flag)
    if chunk_flag:
        cmd.append(chunk_flag)
    if chunk_multiplier != 1:
        cmd += ['--chunk_multiplier', str(chunk_multiplier)]
    if dry_run:
        cmd.append('--dryrun')

    return cmd, extra_env


# ---------------------------------------------------------------------------
# Single-run execution
# ---------------------------------------------------------------------------

def run_one(run_cfg: dict, global_cfg: dict, source_dir: Path,
            cli_overrides: dict, machines_file: Path) -> None:
    """Execute one simulation run."""
    # Merge: global → run-level → CLI overrides
    cfg = {**global_cfg, **run_cfg, **cli_overrides}

    required = ('area', 'experiment', 'model_yaml', 'np',
                 'initial_date', 'stop_date', 'base_outdir')
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        print(f"ERROR: missing required fields: {missing}", file=sys.stderr)
        sys.exit(1)

    area         = cfg['area']
    experiment   = cfg['experiment']
    model_yaml   = cfg['model_yaml']
    np_          = int(cfg['np'])
    initial_date = cfg['initial_date']
    stop_date    = cfg['stop_date']
    start_date   = cfg.get('start_date')
    chunks       = cfg.get('chunks', 'annual')
    chunk_mult   = int(cfg.get('chunk_multiplier', 1))
    base_outdir  = cfg['base_outdir']
    conda_env    = cfg.get('conda_env')
    copy_files   = cfg.get('copy', [])
    symlink_cfg  = cfg.get('symlinks', [])
    dry_run      = cfg.get('dryrun', False)

    outdir = _resolve_outdir(base_outdir, area) / experiment

    # Build env first — needed for symlink target expansion.
    user = os.getenv('USER', os.getenv('LOGNAME', 'user'))
    _tmpl = dict(user=user, area=area, experiment=experiment)
    machine_env = _load_machine_env(machines_file)
    # Unknown keys are left as-is so dry-runs work on machines that lack some vars.
    class _SafeDict(dict):
        def __missing__(self, key): return '{' + key + '}'
    _expand = _SafeDict({**_tmpl, **machine_env})

    # Resolve symlink targets.
    # Each entry can be a bare name (resolved relative to source_dir) or a
    # dict {name: X, target: Y}.  Targets support {TEMPLATE} expansion so
    # machine-specific paths (e.g. {BATHYMETRY_FOLDER}) work correctly.
    symlink_pairs: list[tuple[str, Path]] = []
    for entry in symlink_cfg:
        if isinstance(entry, dict):
            lname  = str(entry['name'])
            target = Path(str(entry['target']).format_map(_expand))
        else:
            expanded = str(entry).format_map(_expand)
            target   = Path(expanded)
            lname    = target.name if target.is_absolute() else expanded
            if not target.is_absolute():
                target = source_dir / expanded
        symlink_pairs.append((lname, target))

    print(f"\n{'='*60}")
    print(f"  area:       {area}")
    print(f"  experiment: {experiment}")
    print(f"  model:      {model_yaml}")
    print(f"  outdir:     {outdir}")
    print(f"  period:     {initial_date} → {stop_date}")
    print(f"  np:         {np_}  chunks: {chunks} ×{chunk_mult}")
    if dry_run:
        print("  [DRY RUN]")
    print(f"{'='*60}")

    # Set up the experiment directory (skip if folder already prepared)
    if not cfg.get('skip_setup'):
        _setup_outdir(outdir, source_dir, model_yaml, copy_files, symlink_pairs, dry_run)
    else:
        print(f"\n  Skipping setup — using existing: {outdir}")

    # Build remaining env for subprocess.
    run_env = {k: str(v).format_map(_expand) for k, v in (cfg.get('env') or {}).items()}
    env = os.environ.copy()
    env.update(machine_env)
    env.update(run_env)
    all_extra = {**machine_env, **run_env}
    if all_extra:
        print(f"\n  Env ({socket.gethostname()}):")
        for k, v in all_extra.items():
            src = ' [run]' if k in run_env else ''
            print(f"    {k} = {v}{src}")

    # Set simulation dates via env vars (read by run_chunks.py)
    env['SIMULATION_INITIAL_DATE'] = str(initial_date)
    env['SIMULATION_STOP_DATE']    = str(stop_date)
    if start_date:
        env['SIMULATION_START_DATE'] = str(start_date)

    cmd, extra_env = _build_run_chunks_cmd(
        source_dir=source_dir,
        outdir=outdir,
        model_yaml=model_yaml,
        np=np_,
        chunks=chunks,
        chunk_multiplier=chunk_mult,
        conda_env=conda_env,
        dry_run=dry_run,
    )
    env.update(extra_env)

    if extra_env.get('PATH'):
        print(f"  PATH prefix: {extra_env['PATH'].split(':')[0]}")
    print(f"\n  Command:\n    {' '.join(cmd)}\n")

    if dry_run or cfg.get('prepare_only'):
        return

    if not cfg.get('skip_setup'):
        shutil.copy2(source_dir / 'run_simulation.py', outdir / 'run_simulation.py')

    result = subprocess.run(cmd, cwd=outdir, env=env)
    if result.returncode != 0:
        print(f"\nERROR: run_chunks.py exited with code {result.returncode}")
        sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog='run_simulation.py',
        description='Launch one or more pyGETM simulations.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument('sim_yaml', type=Path,
                   help='Simulation configuration YAML file')
    p.add_argument('--area',         help='Override area from YAML')
    p.add_argument('--experiment',   help='Override experiment name from YAML')
    p.add_argument('--base-outdir',  dest='base_outdir',
                   help='Override base_outdir from YAML (supports {user}/{area} templates)')
    p.add_argument('--np',           type=int, help='Override number of MPI processes')
    p.add_argument('--dryrun',     action='store_true',
                   help='Print commands without executing')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--prepare',    action='store_true',
                   help='Set up outdir (copy/symlink) but do not run')
    g.add_argument('--skip-setup', action='store_true',
                   help='Skip setup and run in an already-prepared outdir')
    p.add_argument('--machines',   type=Path,
                   help='Path to machines.yaml (default: machines.yaml next to this script)')
    return p.parse_args()


def main() -> None:
    args    = parse_args()
    source_dir = Path(__file__).parent.resolve()

    sim_cfg  = _load_yaml(args.sim_yaml)
    machines_file = args.machines or source_dir / 'machines.yaml'

    # CLI overrides apply to every run
    cli_overrides: dict = {}
    if args.area:
        cli_overrides['area']        = args.area
    if args.experiment:
        cli_overrides['experiment']  = args.experiment
    if args.base_outdir:
        cli_overrides['base_outdir'] = args.base_outdir
    if args.np:
        cli_overrides['np']         = args.np
    if args.dryrun:
        cli_overrides['dryrun']       = True
    if args.prepare:
        cli_overrides['prepare_only'] = True
    if args.skip_setup:
        cli_overrides['skip_setup']   = True

    # Separate global (shared) config from per-run list
    runs = sim_cfg.pop('runs', None)
    global_cfg = sim_cfg   # everything at the top level is shared

    if runs:
        # --experiment filters the list when used with a runs: config
        filter_exp = args.experiment
        if filter_exp:
            runs = [r for r in runs if r.get('experiment') == filter_exp]
            if not runs:
                print(f"ERROR: no run with experiment={filter_exp!r}", file=sys.stderr)
                sys.exit(1)
        print(f"Running {len(runs)} simulation(s) from {args.sim_yaml}")
        for i, run_cfg in enumerate(runs):
            print(f"\n[Run {i+1}/{len(runs)}]")
            run_one(run_cfg, global_cfg, source_dir, cli_overrides, machines_file)
    else:
        run_one({}, global_cfg, source_dir, cli_overrides, machines_file)

    print("\nAll simulations completed.")


if __name__ == '__main__':
    main()
