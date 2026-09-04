"""Reusable wall-clock-gated MPI memory monitor for pyGETM driver scripts.

TEMPORARY-BY-DESIGN diagnostic (not scientific code) -- exists to track
down a slow multi-year memory growth on HPC (~30 GB/year) that
eventually OOM-kills a run with no trace left behind. Kept OUT of the
pygetm_config-generated driver script itself (which warns against
hand-maintaining it long-term) -- the generated script only imports and
calls this, nothing more. Named `generated_*` on purpose even though
it's hand-written, not codegen output: this rides the existing
`generated*.py` staging/sync convention (`stage` /
`pull_experiment_files.sh`, see EXPERIMENT_TRACKING.md) for free,
without any new deployment plumbing. Remove this file (and its one
import + 4 call-site lines in the driver script) once the leak is found
and fixed.
"""

import os
import sys
import time
import datetime
import pathlib


def setup_memory_monitor(sim, resolve_data_path,
                          output_path_template='${OUTPUT_FOLDER}/memory_monitor.csv',
                          interval_s=60.0):
    """Returns a `sample(force=False)` callable -- call it once per
    timestep (or wherever convenient in the run loop); it only actually
    samples/writes roughly every `interval_s` seconds of real wall-clock
    time, not every call.

    The sample/no-sample decision is made by rank 0's clock alone and
    broadcast to every rank, so the collective `gather()` below is
    always called by every rank together or by none of them -- deciding
    independently per-rank off each rank's own clock would risk a
    classic MPI deadlock if two ranks' clocks disagree about which side
    of the interval boundary they're on in a given call.

    Writes ONE row per sample, from rank 0 only: sum + peak RSS across
    all ranks, and which rank peaked (a uniform per-rank climb points at
    a real leak in shared code; a single outlier rank points at
    something specific to that rank's data, e.g. a boundary/river/meteo
    cache). Flushed and fsync'd after every write -- an OOM-kill by
    definition doesn't give the process a chance to close files cleanly,
    so this must survive on disk before that happens, not after.
    """
    # Use pyGETM's own communicator, not MPI.COMM_WORLD directly -- pyGETM
    # defaults to a Dup() of COMM_WORLD (pygetm.parallel.Tiling.__init__),
    # but both Domain.create_*() and Tiling accept an explicit `comm=`
    # override, so a run could genuinely be using a different, non-world
    # communicator (e.g. a subset of ranks). Piggybacking on whichever one
    # the simulation itself is actually using is the only way this stays
    # correct regardless.
    comm = sim.tiling.comm
    rank = sim.tiling.rank

    try:
        import psutil
        proc = psutil.Process()

        def get_rss_mb():
            return proc.memory_info().rss / (1024 * 1024)
    except ImportError:
        import resource

        def get_rss_mb():
            # ru_maxrss is KB on Linux, and is a monotonic high-water mark
            # (never decreases) rather than live RSS -- still shows a real
            # leak's growth, just can't show any subsequent shrinkage.
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    state = {"last_sample_wall": time.time(), "start_wall": time.time(), "fh": None}
    if rank == 0:
        log_path = pathlib.Path(resolve_data_path(output_path_template))
        is_new = not log_path.exists()
        state["fh"] = open(log_path, 'a', buffering=1)
        if is_new:
            state["fh"].write(
                "wall_time_utc,sim_time,elapsed_s,nranks,sum_rss_mb,max_rss_mb,max_rss_rank,rank0_rss_mb\n"
            )
            state["fh"].flush()
            os.fsync(state["fh"].fileno())
        print(f"memory monitor: logging to {log_path} every ~{interval_s:.0f}s (rank 0 only)",
              file=sys.stderr)

    def sample(force=False):
        if rank == 0:
            should_sample = force or (time.time() - state["last_sample_wall"]) >= interval_s
        else:
            should_sample = None
        should_sample = comm.bcast(should_sample, root=0)
        if not should_sample:
            return
        local_rss = get_rss_mb()
        all_rss = comm.gather(local_rss, root=0)
        if rank == 0:
            total = sum(all_rss)
            peak = max(all_rss)
            peak_rank = all_rss.index(peak)
            now = time.time()
            state["fh"].write(
                f"{datetime.datetime.utcnow().isoformat()},{sim.time},"
                f"{now - state['start_wall']:.1f},{len(all_rss)},{total:.1f},{peak:.1f},"
                f"{peak_rank},{all_rss[0]:.1f}\n"
            )
            state["fh"].flush()
            os.fsync(state["fh"].fileno())
            state["last_sample_wall"] = now

    return sample
