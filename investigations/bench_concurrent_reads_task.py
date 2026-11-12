#!/usr/bin/env python3
"""One SLURM task's worker for the disagg-chunking concurrent-read benchmark.

Launched via `srun --ntasks=N python3 bench_concurrent_reads_task.py ...`
inside bench_concurrent_reads.slurm -- one independent process per task,
each opening its OWN netCDF4.Dataset() and reading its OWN distinct spatial
tile. No MPI calls, no inter-task communication: this deliberately mirrors
pygetm's real per-rank read pattern, confirmed to have no centralised
read+broadcast or collective I/O (pygetm.input.InputManager.update() is a
plain per-rank isel() on the source file). $SLURM_PROCID/$SLURM_NTASKS
(set by srun) identify this task; no mpi4py involved or required.

Each task writes ONE small JSON result file under --out-dir; a separate
step (bench_collect_results.py) aggregates them after srun returns -- srun
itself is the barrier between reading the OLD file and the NEW file when
the two are run as separate srun steps (see the .slurm script), so results
for one file are never contaminated by concurrent reads of the other.
"""
import argparse
import json
import os
import time

import numpy as np
import netCDF4


def make_tiles(lat_size, lon_size, n_lat_tiles, n_lon_tiles):
    lat_edges = np.linspace(0, lat_size, n_lat_tiles + 1).astype(int)
    lon_edges = np.linspace(0, lon_size, n_lon_tiles + 1).astype(int)
    tiles = []
    for i in range(n_lat_tiles):
        for j in range(n_lon_tiles):
            tiles.append((lat_edges[i], lat_edges[i + 1], lon_edges[j], lon_edges[j + 1]))
    return tiles


def time_reads(path, varname, lat0, lat1, lon0, lon1, start_t, n_reads):
    ds = netCDF4.Dataset(path)
    v = ds.variables[varname]
    t0 = time.perf_counter()
    for t in range(start_t, start_t + n_reads):
        _ = v[t, lat0:lat1, lon0:lon1]
    dt = time.perf_counter() - t0
    ds.close()
    return dt / n_reads * 1000.0  # ms/read


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True, help="NetCDF file to read (OLD or NEW)")
    p.add_argument("--var", required=True)
    p.add_argument("--label", required=True, help="OLD or NEW -- goes in the output filename")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--n-lat-tiles", type=int, default=14)
    p.add_argument("--n-lon-tiles", type=int, default=13)
    p.add_argument("--n-reads", type=int, default=10)
    p.add_argument("--start-t", type=int, default=4000)
    args = p.parse_args()

    rank = int(os.environ.get("SLURM_PROCID", "0"))
    size = int(os.environ.get("SLURM_NTASKS", "1"))

    ds = netCDF4.Dataset(args.file)
    shape = ds.variables[args.var].shape
    ds.close()

    tiles = make_tiles(shape[1], shape[2], args.n_lat_tiles, args.n_lon_tiles)
    lat0, lat1, lon0, lon1 = tiles[rank % len(tiles)]

    ms_per_read = time_reads(args.file, args.var, lat0, lat1, lon0, lon1,
                              args.start_t, args.n_reads)

    result = {
        "rank": rank,
        "size": size,
        "label": args.label,
        "tile": [int(lat0), int(lat1), int(lon0), int(lon1)],
        "ms_per_read": ms_per_read,
    }
    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, f"result_{args.label}_{rank:04d}.json")
    with open(out_path, "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
