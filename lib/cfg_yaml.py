#!/usr/bin/env python3
"""
demo_load_yaml.py – simple demonstration of loading a YAML config,
merging with defaults, and using attribute‑style access.
"""

import yaml
from pathlib import Path
from types import SimpleNamespace
import sys
import os


# ----------------------------------------------------------------------
# 1️⃣  Helper functions
# ----------------------------------------------------------------------
def load_yaml(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"YAML file not found: {path}")
    with path.open("rt", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise RuntimeError(f"Failed to parse YAML: {exc}") from exc
    return data or {}


def dict_to_namespace(d: dict) -> SimpleNamespace:
    ns = SimpleNamespace()
    for k, v in d.items():
        setattr(ns, k, dict_to_namespace(v) if isinstance(v, dict) else v)
    return ns


def merge_dicts(base: dict, overrides: dict) -> dict:
    for k, v in overrides.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            base[k] = merge_dicts(base[k], v)
        else:
            base[k] = v
    return base


# ----------------------------------------------------------------------
# 2️⃣  Default configuration (you can adjust as needed)
# ----------------------------------------------------------------------
DEFAULT_CONFIG = {
    # "app": {"name": "Unnamed", "version": "0.0"},
    "bathymetry": {"path": None, "name": None},
    "tides": {"folder": None},
    "meteo": {"source": None, "folder": None, "evaporation": False},
    # "thresholds": {"mean_flow_min": 0.0, "map_mean_min": 0.0},
    # "tasks": [],
}


def print_cfg(cfg):
    print(f"Setup name:       {cfg.setup}")
    print(f"Domain:")
    print(f"  Path:           {cfg.domain.path}")
    print(f"  Name:           {cfg.domain.name}")
    print(f"  Boundaries:     {cfg.domain.boundaries}")
    print(f"  Rivers:         {cfg.domain.rivers}")
    print(f"  z0:             {cfg.domain.z0}")
    # tpxo_dir = eval(cfg.tides.folder)
    # print(tpxo_dir)
    print(f"Momentum:")
    print(f"  An:             {cfg.momentum.An}")
    print(f"  Am:             {cfg.momentum.Am}")
    # print(f"  Advection:      {cfg.momentum.advection}")
    # print(f"  Coriolis:       {cfg.momentum.coriolis}")
    print(f"  avmodl:         {cfg.momentum.avmol}")
    print(f"Vertical coordinates:")
    print(f"  Type:           {cfg.vertical_coordinates.type}")
    print(f"  # of layers:    {cfg.vertical_coordinates.nz}")
    print(f"  ddu:            {cfg.vertical_coordinates.ddu}")
    print(f"  ddl:            {cfg.vertical_coordinates.ddu}")
    print(f"  Dgamma:         {cfg.vertical_coordinates.Dgamma}")
    print(f"  gamma_surf:     {cfg.vertical_coordinates.gamma_surf}")
    print(f"Internal pressure:")
    print(f"  Type:           {cfg.internal_pressure.type}")
    print(f"Initial conditions:")
    print(f"  Source:         {cfg.hydrography.source}")
    print(f"  Folder:         {cfg.hydrography.folder}")
    print(f"  Temperature:    {cfg.hydrography.temp}")
    print(f"  Salinity:       {cfg.hydrography.salt}")
    print(f"Tidal boundaries: {cfg.tides.folder}")
    print(f"Meteo:")
    print(f"  Source:         {cfg.meteo.source}")
    print(f"  Folder:         {cfg.meteo.folder}")
    print(f"  Evaporation:    {cfg.meteo.evaporation}")
    print(f"Rivers")
    print(f"  Source:         {cfg.rivers.source}")
    print(f"  Folder:         {cfg.rivers.folder}")
    print(f"  File:           {cfg.rivers.file}")
    print(f"  Threshold:      {cfg.rivers.threshold}")
    print(f"FABM:")
    print(f"  File:           {cfg.fabm.file}")
    print(f"  Folder:         {cfg.fabm.folder}")
    print(f"  config:             {cfg.fabm.config}")
    print(f"Simulation:")
    print(f"  GOTM:           {cfg.simulation.gotm}")
    print(f"  Calendar:       {cfg.simulation.calendar}")
    print(f"  runtype:        {cfg.simulation.runtype}")
    print(f"  Dcrit:          {cfg.simulation.Dcrit}")
    print(f"  Dmin:           {cfg.simulation.Dmin}")
    print(f"Runtime:")
    print(f"  timestep:       {cfg.runtime.timestep}")
    print(f"  split_factor:   {cfg.runtime.split_factor}")
    print(f"  Output:         {cfg.runtime.output}")
    print(f"  Output (debug): {cfg.runtime.debug_output}")
    print(f"  Output folder:  {cfg.runtime.output_folder}")
    print(f"Switches:")
    print(f"  dump_on_error:  {cfg.switches.dump_on_error}")
    print(f"  check_finite:   {cfg.switches.check_finite}")
    print(f"  profile:         {cfg.switches.profile}")


# ----------------------------------------------------------------------
# 3️⃣  Main execution
# ----------------------------------------------------------------------
def main(argv=None):
    argv = argv or sys.argv[1:]

    if not argv:
        print("Usage: demo_load_yaml.py <config.yml>")
        return 1

    cfg_path = Path(argv[0])
    user_cfg = load_yaml(cfg_path)

    # Merge user config on top of defaults
    full_cfg_dict = merge_dicts(DEFAULT_CONFIG.copy(), user_cfg)

    # Optional: turn into attribute‑style namespace
    cfg = dict_to_namespace(full_cfg_dict)

    print_cfg(cfg)

    return 0


if __name__ == "__main__":
    sys.exit(main())
