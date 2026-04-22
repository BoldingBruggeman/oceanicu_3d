#!/usr/bin/env python

"""Run a pyGETM setup for a specified time

This script handles all book keeping when running a series of
sequential simulations to make a full simulation.

The mandatory command line arguments are:
    1) A YAML-formatted configuration file
    2) The simulation start time - "yyyy-mm-dd hh:mi:ss"
    3) The simulation stop time - "yyyy-mm-dd hh:mi:ss"

The are many optional arguments falling in two groups:
    1) options to specify folders and files - if specified they will
    take precedens over values from the YAML-configuration file
    2) various run time switches - many used for convenience - e.g.
    --no_rivers will switch off configurations of rivers alltogether

All configurations are internally kept in a dictionary - cfg.

The handling of all commanline configurations takes about 60% of the script.
The actual setup is done within the top 160 lines.

The script can be called from with the run_chunks.py script for
making multiple sequential simulations making a full simulation

"""

import sys
import os
from pathlib import Path
import datetime
from typing import Optional, Union

import xarray as xr
import cftime

import pygetm

# Configuration of some components have been moved outside the main
# script
from lib import cfg_airsea
from lib import cfg_boundaries
from lib import cfg_fabm
from lib import cfg_ic
from lib import cfg_ip
from lib import cfg_output
from lib import cfg_rivers
from lib import cfg_vc


class MySimulation(pygetm.Simulation):
    def __init__(self, *args, initial, **kwargs):
        self.initial = initial
        super().__init__(*args, **kwargs)

    def _update_forcing_and_diagnostics(self, macro_active: bool):
        if self.initial:
            ramp = 1.0
            # ramp = min(sim.istep / 10800.0, 1)
            # sim.open_boundaries.z.all_values *= ramp
            # sim.open_boundaries.u.all_values *= ramp
            # sim.open_boundaries.v.all_values *= ramp
        super()._update_forcing_and_diagnostics(macro_active)


def _create_domain_amm7(cfg):
    import numpy as np
    import netCDF4

    print(cfg.domain.path)
    print(cfg.domain.name)
    with netCDF4.Dataset(cfg.domain.path) as nc:
        nc.set_auto_mask(False)
        domain = pygetm.domain.create_spherical(
            lon=nc["lon"][:],
            lat=nc["lat"][:],
            H=np.ma.masked_equal(nc["bathymetry"][...], -10.0),
            z0=0.01,
            #            **final_kwargs,
        )
    domain.mask_indices(1, 2, 353, 354)

    return domain


def create_domain(cfg) -> pygetm.domain.Domain:
    if cfg.setup != "amm7":
        domain = pygetm.domain.from_xarray(xr.open_dataset(cfg.domain.path))
    else:
        domain = _create_domain_amm7(cfg)

    cfg_boundaries.create(domain, cfg)

    # as suggested by Jorn
    # domain.limit_velocity_depth()
    domain.cfl_check()

    if domain.z0 is not None:
        domain.z0[...] = cfg.domain.z0

    cfg_rivers.create(domain, cfg)

    return domain


def create_simulation(
    domain: pygetm.domain.Domain,
    cfg,
    load_restart,
    imonth: int,
) -> MySimulation:
    _vc = cfg_vc.create(cfg)
    _ip = cfg_ip.create(cfg)
    _airsea = cfg_airsea.create(cfg)

    final_kwargs = {}
    final_kwargs = dict(
        # momentum=momentum,
        runtype=cfg.simulation.runtype,
        advection_scheme=pygetm.AdvectionScheme.SUPERBEE,
        fabm=cfg.fabm.file,
        gotm=Path("gotm.yaml"),
        airsea=_airsea,
        internal_pressure=_ip,
        vertical_coordinates=_vc,
        Dcrit=cfg.simulation.Dcrit,
        Dmin=cfg.simulation.Dmin,
        delay_slow_ip=False,
    )

    initial = not load_restart
    sim = MySimulation(
        domain,
        initial=initial,
        **final_kwargs,
    )

    sim.momentum.An.set(cfg.momentum.An)

    if initial and cfg.simulation.runtype > 1:
        cfg_ic.create(sim, cfg, imonth)

    cfg_boundaries.data_2d(sim, cfg)
    if cfg.simulation.runtype > 1:
        cfg_boundaries.data_3d(sim, cfg)

    cfg_rivers.data(sim, cfg)

    cfg_airsea.data(sim, cfg)

    if sim.runtype == pygetm.pygetm.RunType.BAROCLINIC:
        # sim.radiation.set_jerlov_type(pygetm.Jerlov.Type_II)
        sim.radiation.A.set(0.7)
        sim.radiation.kc1.set(0.54)  # 1/g1 in gotm
        if not cfg.simulation.ObsKd:
            sim.radiation.kc2.set(3.23)
        else:
            attenuation_path = "/data/Kd490/KD490_clim.nc"
            sim.radiation.kc2.set(pygetm.input.from_nc(attenuation_path, "KD490_filled"),climatology=True)

    cfg_fabm.configure(sim, cfg, imonth)

    return sim


def run(
    # sim: pygetm.simulation.MySimulation,
    sim: MySimulation,
    simstart: cftime.datetime,
    simstop: cftime.datetime,
    cfg,
    dryrun=False,
    # profile = False,
    **kwargs,
):
    if dryrun:
        print("")
        print("Making a dryrun - skipping sim.advance()")
        print("")
    else:
        sim.start(
            simstart,
            timestep=cfg.runtime.timestep,
            split_factor=cfg.runtime.split_factor,
            dump_on_error=cfg.switches.dump_on_error,
            **kwargs,
        )
        while sim.time < simstop:
            sim.advance(check_finite=cfg.switches.check_finite)

        sim.finish()


def parse_args():
    import argparse
    from lib import yaml_loader

    if len(sys.argv) < 2:
        print(f"The first argument to {sys.argv[0]} must be a YAML-configuration file")
        sys.exit(0)
    _cfg_file = Path(sys.argv[1])
    if not _cfg_file.exists():
        print(f"{_cfg_file} is not a valid file")
        sys.exit(0)
    user_cfg = yaml_loader.load_yaml(Path(_cfg_file))
    full_cfg_dict = yaml_loader.merge_dicts(yaml_loader.DEFAULT_CONFIG.copy(), user_cfg)
    cfg = yaml_loader.dict_to_namespace(full_cfg_dict)

    p = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("config", type=Path, help="YAML configuration file")
    p.add_argument("start", help="simulation start time - yyyy-mm-dd hh:mi:ss")
    p.add_argument("stop", help="simulation stop time - yyyy-mm-dd hh:mi:ss")

    p.add_argument(
        "--bathymetry_file",
        type=Path,
        help="Path to the bathymetry file",
        # default=Path({cfg.bathymetry.path}),
    )
    p.add_argument(
        "--bathymetry_name",
        type=str,
        help="Name of bathymetry variable",
        default=f"{cfg.domain.name}",
    )
    p.add_argument(
        "--tpxo_dir",
        type=Path,
        help="Path to TPXO configuration files - v9 and v10 supported",
        default=Path(eval(f"{cfg.tides.folder}")),
    )
    p.add_argument(
        "--woa_dir",
        type=Path,
        help="Path to World Ocean Atlas files",
        default=Path(eval(f"{cfg.hydrography.folder}")),
    )
    p.add_argument(
        "--meteo_dir",
        type=Path,
        help="Folder with meteo files",
        default=eval(f"{cfg.meteo.folder}"),
    )
    p.add_argument(
        "--river_source",
        type=str,
        help="The river data source",
        default={cfg.rivers.source},
    )
    p.add_argument(
        "--river_dir",
        type=Path,
        help="Folder with river data file",
        default=Path(eval(f"{cfg.rivers.folder}")),
    )
    p.add_argument(
        "--river_file",
        type=Path,
        help="Name of river data file",
        default=eval(f"{cfg.rivers.file}"),
    )
    p.add_argument(
        "--river_threshold",
        type=float,
        help="Only use rivers with a menaflow above the threshold",
        default=0.0,
    )
    p.add_argument(
        "--gotm_yaml_file",
        type=Path,
        help="Full path of a GOTM yaml file",
        # default={cfg.fabm.file},
    )
    p.add_argument(
        "--fabm_yaml_file",
        type=Path,
        help="Full path of a FABM yaml file - will enable FABM",
        # default={cfg.fabm.file},
    )
    p.add_argument(
        "--fabm_dir",
        type=Path,
        help="Path to FABM specific files",
        default=Path(eval(f"{cfg.fabm.folder}")),
    )
    p.add_argument(
        "--runtype",
        type=int,
        choices=(pygetm.BAROTROPIC_2D, pygetm.BAROTROPIC_3D, pygetm.BAROCLINIC),
        help="Model run type: 1 -> 2D barotropic, 2 -> 3D baroropic, 4 -> baroclinic",
        #default=pygetm.BAROCLINIC,
    )
    p.add_argument(
        "--output_dir",
        type=Path,
        help="Path to save output files",
        # default=f"{setup_dir}",
        default=".",
    )
    p.add_argument("--load_restart", help="File to load restart from")
    p.add_argument("--save_restart", help="File to save restart to")
    p.add_argument(
        "--no_output",
        action="store_false",
        dest="output",
        help="Do not save any results to NetCDF",
    )
    p.add_argument(
        "--debug_output",
        action="store_true",
        help="Save additional variables for debugging",
    )
    p.add_argument(
        "--no_boundaries",
        action="store_false",
        dest="boundaries",
        help="No open boundaries",
    )
    p.add_argument(
        "--no_rivers", action="store_true", dest="rivers", help="No river input"
    )
    p.add_argument(
        "--no_meteo",
        action="store_true",
        dest="meteo",
        help="No meteo forcing",
        # default=no_meteo,
    )
    p.add_argument("--dryrun", action="store_true", help="Do a dry run")
    p.add_argument(
        "--profile", action="store_true", help="Do a profiling of the simulation"
    )
    p.add_argument(
        "--plot_domain", action="store_true", help="Plot the calculation domain"
    )
    p.add_argument(
        "--print_config",
        action="store_true",
        help="Print the final configuration and exit",
    )
    p.add_argument(
        "--list_output", action="store_true", help="Write a list of all possible output"
    )

    args = p.parse_args()

    # Switch off configs depending on command line arguments
    cfg.domain.boundaries = args.boundaries
    #KBcfg.domain.rivers = not args.rivers
    if args.runtype:
        cfg.simulation.runtype = args.runtype

    if not cfg.domain.rivers:
        cfg.rivers.source = None
    if args.meteo:
        cfg.meteo.source = None
    cfg.runtime.output = args.output

    if args.profile:
        cfg.switches.profile = args.profile

    if args.bathymetry_file:
        cfg.domain.path = Path(args.bathymetry_file)
    else:
        cfg.domain.path = eval(cfg.domain.path)
    if args.bathymetry_name:
        cfg.domain.name = args.bathymetry_name

    if cfg.domain.boundaries:
        if args.tpxo_dir:
            cfg.tides.folder = Path(args.tpxo_dir)
        else:
            cfg.tides.folder = eval(cfg.tides.folder)

    if cfg.hydrography.source is not None:
        if args.woa_dir:
            cfg.hydrography.folder = Path(args.woa_dir)
        else:
            cfg.hydrography.folder = eval(cfg.hydrography.folder)

    if cfg.meteo.source is not None:
        if isinstance(cfg.meteo.source, str) and '(' in cfg.meteo.source:
            cfg.meteo.source = eval(cfg.meteo.source)
        if cfg.meteo.source == "CMIP6":
            cfg.simulation.calendar = "noleap"
        if args.meteo_dir:
            cfg.meteo.folder = Path(args.meteo_dir)
        else:
            cfg.meteo.folder = eval(cfg.meteo.folder)

    if cfg.rivers.source is not None:
        if args.river_dir:
            cfg.rivers.folder = Path(args.river_dir)
        else:
            cfg.rivers.folder = eval(cfg.rivers.folder)
        if args.river_file:
            cfg.rivers.file = Path(args.river_file)
        else:
            cfg.rivers.file = eval(cfg.rivers.file)
        if args.river_threshold:
            cfg.rivers.threhold = args.threshold

    if args.gotm_yaml_file:
        cfg.simulation.gotm = Path(args.gotm_yaml_file)
    else:
        _ = eval(cfg.simulation.gotm)
        cfg.simulation.gotm = None if not _ else Path(_)

    if args.fabm_yaml_file:
        cfg.fabm.file = Path(args.fabm_yaml_file)
    else:
        _ = eval(cfg.fabm.file)
        cfg.fabm.file = None if not _ else Path(_)

    if args.fabm_dir:
        cfg.fabm.folder = Path(args.fabm_dir)
    else:
        cfg.fabm.folder = Path(cfg.fabm.path)

    if cfg.runtime.output:
        if args.output_dir:
            cfg.output.folder = Path(args.output_dir)
        else:
            cfg.output.folder = eval(cfg.output.folder)

    if args.list_output:
        print("Full output list")
        print(f"name{' ':16} long_name{' ':71} units")
        print("-" * 120)
        # a very terse version print(sim.output_manager.fields.keys())
        for n, v in sim.output_manager.fields.items():
            print(f"{n:<20} {v.long_name:<80} [{v.units}]")
        sys.exit(0)

    if args.print_config:
        yaml_loader.print_cfg(cfg)
        sys.exit(0)

    if args.output_dir != ".":
        p = Path(args.output_dir)
        if not p.is_dir():
            print(f"Folder {args.output_dir} does not exist - create and run again")
            sys.exit(1)

    # All configuration is done ready to create and run the simulation

    return args, cfg


def main():
    args, cfg = parse_args()

    domain = create_domain(cfg)

    save_restart = args.save_restart if args.save_restart else None
    load_restart = args.load_restart if args.load_restart else None

    simstart = cftime.datetime.strptime(
        args.start, "%Y-%m-%d %H:%M:%S", calendar=cfg.simulation.calendar
    )
    simstop = cftime.datetime.strptime(
        args.stop, "%Y-%m-%d %H:%M:%S", calendar=cfg.simulation.calendar
    )

    sim = create_simulation(
        domain,
        cfg,
        load_restart,
        simstart.month - 1,
    )

    if args.plot_domain:
        f = domain.plot(show_mesh=True, show_subdomains=True, tiling=sim.tiling)
        if f is not None:
            f.savefig(f"domain_{cfg.setup}_mesh.png")
        f = domain.plot(show_mesh=False, show_mask=True)
        if f is not None:
            f.savefig(f"domain_{cfg.setup}_mask.png")
        sys.exit(0)

    if args.save_restart and not args.dryrun:
        sim.output_manager.add_restart(args.save_restart)
    if args.load_restart and not args.dryrun:
        simstart = sim.load_restart(args.load_restart)

    if cfg.runtime.output and not args.dryrun:
        cfg_output.create(sim, cfg, save_initial=load_restart is None)

    profile = cfg.setup if cfg.switches.profile else None
    run(
        sim,
        simstart,
        simstop,
        cfg,
        dryrun=args.dryrun,
        report=datetime.timedelta(days=1),
        report_totals=datetime.timedelta(days=7),
        profile=profile,
    )


if __name__ == "__main__":
    main()
