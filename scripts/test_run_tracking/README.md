# Testing oceanicu_runs.py / run_tracking.py

A self-contained test harness: fake, fast-to-set-up simulations registered
against a scratch (or relay-hosted) registry, each chunk taking a real
5-10 minutes (via `sleep`, not a real pyGETM run) so there's something
genuinely "in progress" to run `oceanicu_runs.py` commands against --
`list`, `show`, `pause`, `resume`, `rerun`, `set-priority`,
`set-stop-date`, `chunk-size`, `remove`, `--dry-run`.

Nothing here touches the real production registry, and nothing here is
committed to git yet -- this whole folder is scratch/test tooling.

## Files

| file | what it does |
|---|---|
| `fake_driver.py` | stand-in "driver script" -- accepts the same CLI a real pygetm_config-generated driver does, sleeps 5-10 min instead of simulating anything, writes a placeholder restart file. |
| `setup_fake_runs.py` | creates the registry (local or relay) and registers 5 varied fake runs against it. |
| `run_chunk_local.py` | non-SLURM worker loop (this machine has no SLURM) -- calls `chunk_runner.py` directly instead of `sbatch`-ing `run_chunk.slurm`. |
| `README.md` | this file. |

## Option A -- no SLURM (this machine)

```bash
cd test_run_tracking
python setup_fake_runs.py                # local test_registry.sqlite, launcher=mpiexec
export OCEANICU_RUN_DB=$PWD/test_registry.sqlite

# see what's registered
python ../oceanicu_runs.py list

# start a worker -- this blocks, running real (sleeping) chunks one after
# another; run it in its own terminal/tmux pane
python run_chunk_local.py --db "$OCEANICU_RUN_DB" --run-id fake/quick/run01
```

While that's running, from a **second terminal** (same `OCEANICU_RUN_DB`
exported), try any of:

```bash
python ../oceanicu_runs.py list
python ../oceanicu_runs.py list --status in_progress
python ../oceanicu_runs.py show --run-id fake/quick/run01
python ../oceanicu_runs.py pause --run-id fake/quick/run01
python ../oceanicu_runs.py resume --run-id fake/quick/run01
python ../oceanicu_runs.py set-priority --run-id fake/long/GFDL-ESM4/ssp370 --priority 99
python ../oceanicu_runs.py set-stop-date --run-id fake/long/CNRM-ESM2-1/ssp126 --stop-date 2020-01-01
python ../oceanicu_runs.py chunk-size --run-id fake/long/CNRM-ESM2-1/ssp126 --chunk-multiplier 1
python ../oceanicu_runs.py --dry-run rerun --run-id fake/long/GFDL-ESM4/ssp370 --from-scratch
python ../oceanicu_runs.py remove --run-id fake/notstarted/spare
```

`fake/quick/run01` and `fake/quick/run02` each finish in a single chunk
(one ~1-day step) -- start a worker with no `--run-id` at all to watch it
pick the next queued run up automatically once the current one completes:

```bash
python run_chunk_local.py --db "$OCEANICU_RUN_DB"
```

**Trigger a failure on purpose** (to test `rerun`/`list --status failed`):

```bash
touch runs/fake_long_CNRM-ESM2-1_ssp126/FAIL_NEXT_CHUNK
```

The next chunk `fake_driver.py` runs for that run exits 1 instead of
sleeping+succeeding -- deleted automatically once consumed, so it only
fails the one chunk it was meant to.

**Start over:**

```bash
python setup_fake_runs.py --reset
```

`--reset` refuses if any run currently has a chunk marked `running` --
real incident, 2026-08-28: a worker started in a second terminal (per
this same walkthrough) was still asleep on a chunk when `--reset` ran
elsewhere and deleted the very directory it was about to write its
restart file into (`FileNotFoundError`, not a `fake_driver.py` bug).
Stop that worker first, or pass `--force` once you've actually confirmed
via `ps`/pgrep that nothing is running.

**Faster iteration while developing this harness itself** (not for actually
trying out the CLI, which wants the real 5-10 min feel):

```bash
FAKE_CHUNK_SECONDS=5 python run_chunk_local.py --db test_registry.sqlite --run-id fake/quick/run01
```

**Pace the hand-off between chunks/runs** with `OCEANICU_CHUNK_DELAY_SECONDS`
(default 0 -- same env var and meaning as the real `../run_chunk.slurm`,
see `../RUN_TRACKING.md`'s own "Launch it" section) -- a pause right before
starting the NEXT chunk or the next queued run, never while one is
actually executing. Not a substitute for pause/resume, which stops
resubmission entirely; this just paces it:

```bash
OCEANICU_CHUNK_DELAY_SECONDS=30 python run_chunk_local.py --db test_registry.sqlite --run-id fake/long/CNRM-ESM2-1/ssp126
```

## Option B -- a real SLURM machine

Use the REAL `../run_chunk.slurm` -- no new script needed for this path,
`run_chunk_local.py` is only a stand-in for machines without SLURM.

**Remember: the DB should live on the relay**, not on whichever machine
happens to run `setup_fake_runs.py`, whenever the add-machine and the
SLURM machine can't reach each other directly (see `../RUN_TRACKING.md`'s
"Working across machines"). Point `--db` at the relay explicitly:

```bash
python setup_fake_runs.py --launcher srun \
    --db ssh://oceanicu-relay/abs/path/to/test_registry.sqlite \
    --fake-driver-path /abs/path/on/the/slurm/machine/fake_driver.py
```

(`--fake-driver-path` only needs overriding if the SLURM machine isn't
the same one you ran `setup_fake_runs.py` from -- copy `fake_driver.py`
there first, same as any other driver script normally gets deployed
per-run per `RUN_TRACKING.md`.)

Then, **on the SLURM machine**:

```bash
sbatch --export=RUN_ID='fake/quick/run01',OCEANICU_RUN_DB='ssh://oceanicu-relay/abs/path/to/test_registry.sqlite' \
    /path/to/scripts/run_chunk.slurm
```

Everything else -- `list`/`show`/`pause`/`resume`/`rerun`/etc. -- works
exactly as in Option A, just with `--db`/`OCEANICU_RUN_DB` set to the
same `ssh://` relay path everywhere, from any machine that can reach the
relay.

One thing to sanity-check first if this is a genuinely different SLURM
cluster than the one `run_chunk.slurm` was written for: it unconditionally
does `conda activate pygetm` and loads a handful of specific HPC
environment modules before running anything. None of that is actually
needed by `fake_driver.py` (it's plain Python, no pyGETM/MPI dependency
beyond `mpiexec -n 1`/`srun` themselves launching it), but if those
modules don't exist on the test cluster the job will fail before ever
reaching `chunk_runner.py`. Comment those lines out temporarily for a
pure fake-run test, or run on the same cluster real production jobs
target.
