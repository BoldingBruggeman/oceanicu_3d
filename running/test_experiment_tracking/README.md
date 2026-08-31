# Testing oceanicu_experiments.py / experiment_tracking.py

A self-contained test harness: fake, fast-to-set-up simulations registered
against a scratch (or relay-hosted) registry, each chunk taking a real
5-10 minutes (via `sleep`, not a real pyGETM run) so there's something
genuinely "in progress" to run `oceanicu_experiments.py` commands against --
`list`, `show`, `pause`, `resume`, `rerun`, `set-priority`,
`set-stop-date`, `chunk-size`, `remove`, `--dry-run`.

Nothing here touches the real production registry, though this folder
IS committed to git (unlike its own generated `test_registry.sqlite`/
`experiments/`, which are gitignored) -- it's test tooling, not test data.

## Files

| file | what it does |
|---|---|
| `fake_driver.py` | stand-in "driver script" -- accepts the same CLI a real pygetm_config-generated driver does, sleeps 5-10 min instead of simulating anything, writes a placeholder restart file. |
| `setup_fake_experiments.py` | creates the registry (local or relay) and registers 5 varied fake experiments against it. |
| `run_chunk_local.py` | non-SLURM worker loop (this machine has no SLURM) -- calls `chunk_runner.py` directly instead of `sbatch`-ing `run_chunk.slurm`. |
| `README.md` | this file. |

## Option A -- no SLURM (this machine)

```bash
cd test_experiment_tracking
python setup_fake_experiments.py                # local test_registry.sqlite, launcher=mpiexec
export OCEANICU_EXPERIMENT_DB=$PWD/test_registry.sqlite

# see what's registered
oceanicu-experiments list

# start a worker -- this blocks, running real (sleeping) chunks one after
# another; run it in its own terminal/tmux pane
python run_chunk_local.py --db "$OCEANICU_EXPERIMENT_DB" --experiment-id fake/quick/run01
```

While that's running, from a **second terminal** (same `OCEANICU_EXPERIMENT_DB`
exported), try any of:

```bash
oceanicu-experiments list
oceanicu-experiments list --status in_progress
oceanicu-experiments show --experiment-id fake/quick/run01
oceanicu-experiments pause --experiment-id fake/quick/run01
oceanicu-experiments resume --experiment-id fake/quick/run01
oceanicu-experiments set-priority --experiment-id fake/long/GFDL-ESM4/ssp370 --priority 99
oceanicu-experiments set-stop-date --experiment-id fake/long/CNRM-ESM2-1/ssp126 --stop-date 2020-01-01
oceanicu-experiments set-chunk-delay --experiment-id fake/long/GFDL-ESM4/ssp370 --seconds 30
oceanicu-experiments set-np --experiment-id fake/long/GFDL-ESM4/ssp370 --np 4
oceanicu-experiments set-data-roots-file --experiment-id fake/long/GFDL-ESM4/ssp370 --path some_other_data_roots.yaml
oceanicu-experiments chunk-size --experiment-id fake/long/CNRM-ESM2-1/ssp126 --chunk-multiplier 1
oceanicu-experiments --dry-run rerun --experiment-id fake/long/GFDL-ESM4/ssp370 --from-scratch
oceanicu-experiments remove --experiment-id fake/notstarted/spare
```

`fake/quick/run01` and `fake/quick/run02` each finish in a single chunk
(one ~1-day step) -- start a worker with no `--experiment-id` at all to watch it
pick the next queued experiment up automatically once the current one completes:

```bash
python run_chunk_local.py --db "$OCEANICU_EXPERIMENT_DB"
```

**Trigger a failure on purpose** (to test `rerun`/`list --status failed`):

```bash
touch experiments/fake_long_CNRM-ESM2-1_ssp126/FAIL_NEXT_CHUNK
```

The next chunk `fake_driver.py` runs for that experiment exits 1 instead of
sleeping+succeeding -- deleted automatically once consumed, so it only
fails the one chunk it was meant to.

**Start over:**

```bash
python setup_fake_experiments.py --reset
```

`--reset` refuses if any experiment currently has a chunk marked `running` --
real incident, 2026-08-28: a worker started in a second terminal (per
this same walkthrough) was still asleep on a chunk when `--reset` ran
elsewhere and deleted the very directory it was about to write its
restart file into (`FileNotFoundError`, not a `fake_driver.py` bug).
Stop that worker first, or pass `--force` once you've actually confirmed
via `ps`/pgrep that nothing is running.

**Faster iteration while developing this harness itself** (not for actually
trying out the CLI, which wants the real 5-10 min feel):

```bash
FAKE_CHUNK_SECONDS=5 python run_chunk_local.py --db test_registry.sqlite --experiment-id fake/quick/run01
```

**Pause the hand-off between chunks/experiments for a while, live** (same
mechanism and meaning as the real `../run_chunk.slurm`, see
`../EXPERIMENT_TRACKING.md`'s own "Launch it" section) -- from a second
terminal, while a worker loop is already running:

```bash
oceanicu-experiments delay-all --db test_registry.sqlite --seconds 300
oceanicu-experiments delay-all --db test_registry.sqlite --clear
```

Not a substitute for pause/resume (stops resubmission indefinitely until
a human resumes); this waits out the given number of seconds then
proceeds automatically, and is genuinely live-adjustable -- run it again
with a new value even while the loop is already mid-wait from an earlier
call, and it picks up the change within its poll interval (60s).

## Trying the command queue risk-free

The queue/`get_commands_and_update_registry.py` mechanism (see
`../EXPERIMENT_TRACKING.md` "Command queue") works against this same scratch
registry, entirely locally, no second machine or real network isolation
needed to see it work end to end:

```bash
mkdir -p hpc_commands
oceanicu-experiments --queue hpc_commands/queue_kb.yaml set-priority \
    --experiment-id fake/notstarted/spare --priority 42
# nothing applied yet -- inspect hpc_commands/queue_kb.yaml, it's just a YAML file

python ../get_commands_and_update_registry.py --db "$OCEANICU_EXPERIMENT_DB" --queue-dir hpc_commands
# no --pull-from here -- that only matters once a real remote (bb-server1)
# is involved; omitting it just applies whatever's already local, same as
# the old apply_commands.py always did. OCEANICU_HPC=1 not needed either --
# this script doesn't gate itself, only oceanicu_experiments.py's own direct
# `add` does (see EXPERIMENT_TRACKING.md "Set up an experiment")

oceanicu-experiments show --experiment-id fake/notstarted/spare   # priority is now 42
```

## Option B -- a real SLURM machine

Use the REAL `../run_chunk.slurm` -- no new script needed for this path,
`run_chunk_local.py` is only a stand-in for machines without SLURM.

**Remember: the DB should live on the relay**, not on whichever machine
happens to run `setup_fake_experiments.py`, whenever the add-machine and the
SLURM machine can't reach each other directly (see `../EXPERIMENT_TRACKING.md`'s
"Working across machines"). Point `--db` at the relay explicitly:

```bash
python setup_fake_experiments.py --launcher srun \
    --db ssh://oceanicu-relay/abs/path/to/test_registry.sqlite \
    --fake-driver-path /abs/path/on/the/slurm/machine/fake_driver.py
```

(`--fake-driver-path` only needs overriding if the SLURM machine isn't
the same one you ran `setup_fake_experiments.py` from -- copy `fake_driver.py`
there first, same as any other driver script normally gets deployed
per-experiment per `EXPERIMENT_TRACKING.md`.)

Then, **on the SLURM machine**:

```bash
cd /path/to/running && sbatch --export=ALL,EXPERIMENT_ID='fake/quick/run01',OCEANICU_EXPERIMENT_DB='ssh://oceanicu-relay/abs/path/to/test_registry.sqlite' \
    run_chunk.slurm
```

(`cd` first and the leading `ALL,` are both load-bearing, not style -- see
`run_chunk.slurm`'s own header comment / `../EXPERIMENT_TRACKING.md` "Launch
it" for why.)

Everything else -- `list`/`show`/`pause`/`resume`/`rerun`/etc. -- works
exactly as in Option A, just with `--db`/`OCEANICU_EXPERIMENT_DB` set to the
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
pure fake-experiment test, or run on the same cluster real production jobs
target.
