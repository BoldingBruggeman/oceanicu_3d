# Production experiment tracking

A small SQLite-backed system for running chunked pyGETM production
simulations on SLURM and keeping track of what's been run and what's
next, without hand-editing a spreadsheet.

No packaging -- plain files. This is where they live in the repo; on any
machine that actually runs them (production machine, relay -- see
"Working across machines" below), deploy the ones that machine needs
together in one directory, wherever makes sense there -- `run_chunk.slurm`
finds its own deployed location at runtime (see "Launch it"), so there's
no path to hardcode or tell it about, and no requirement that every
machine use the same directory or even the same username:

| file | what it does |
|---|---|
| `experiment_tracking.py` | the SQLite schema + data-access functions. Not usually called directly. |
| `oceanicu_experiments.py` | the CLI you use day to day: register experiments, check status, pause/resume, rerun. |
| `chunk_runner.py` | runs exactly one chunk. Called by `run_chunk.slurm`; you don't normally invoke it by hand except when testing (see below). |
| `run_chunk.slurm` | the SLURM job. Runs one chunk, then resubmits itself for the next one -- or, once an experiment reaches its `stop_date`, for the next queued experiment (see "The queue" below). |
| `experiment_tracking_server.py` | RPC entrypoint for accessing the registry across machines with no direct network path between them -- see "Working across machines" below. Only needed on the relay machine, and only if you need that at all. |
| `get_commands_and_update_registry.py` | pulls queued commands in from bb-server1 (optional, `--pull-from`) and replays whatever's pending (see "Command queue" below) against the local registry -- only needed wherever the registry actually lives, and only if a live relay to it isn't possible. Renamed from `apply_commands.py` when the pull step was added. |
| `setup_experiment_tracking.sh` | one-shot env/directory setup per machine role (see below) -- standalone, no dependency on anything else here. |
| `bin/push_registry_snapshot.sh` | pushes the registry out to bb-server1 (see "Keeping bb-server1's copy of the registry up to date") -- only needed wherever the registry actually lives. |
| `bin/watch_registry_and_push.sh` | persistent watcher: calls `push_registry_snapshot.sh` the moment a fresh backup snapshot appears, instead of waiting for the next cron interval. Login node only; meant to be kept alive by `restart_registry_watcher.sh`, not run by hand. |
| `bin/restart_registry_watcher.sh` | cron watchdog: (re)starts `watch_registry_and_push.sh` if it's not already running. Login node only. |
| `bin/pull_experiment_files.sh` | pulls freshly staged experiment files (driver script/config) in from bb-server1, filtered the same way `stage` writes them -- see "Command queue". Login node only. |
| `test_compute_to_login_ssh.sbatch` | one-shot test for whether a compute node can reach its own login node -- see "Keeping bb-server1's copy up to date". Not part of normal operation. |

## Use `oceanicu-experiments`, not `python oceanicu_experiments.py`

Every example from here on uses `oceanicu-experiments` -- a thin wrapper
(`running/bin/oceanicu-experiments`) that just forwards to the real script, so
there's no full path to type every time. Put it on `PATH` once, on any
machine (workstation, bb-server1, the HPC -- all the same):

```bash
export PATH="/abs/path/to/oceanicu_3d/running/bin:$PATH"
```

Add that to `~/.bashrc` to make it permanent. `chunk-runner` (for
`chunk_runner.py`) and `get-commands-and-update-registry` come along the
same way, for free -- one `PATH` addition covers everything in
`running/bin`. `watch_registry_and_push.sh`, `restart_registry_watcher.sh`,
`push_registry_snapshot.sh`, and `pull_experiment_files.sh` are plain
executable bash living in the same directory -- no wrapper needed, they
just work by their own name once `running/bin` is on `PATH` too, `.sh`
and all. (cron jobs are the
one exception: cron doesn't source `~/.bashrc`, so a crontab line still
needs either a full path or its own explicit `PATH=` -- see "Keeping
bb-server1's copy of the registry up to date" below.
`get-commands-and-update-registry` and `push_registry_snapshot.sh` are
mainly useful for *manual* invocation -- see "Don't wait for the cron
interval if something's urgent" in "Command queue" and the equivalent
note in "Keeping bb-server1's copy up to date".)

`setup_experiment_tracking.sh` deliberately has **no** `bin/` wrapper -- it's
meant to be copied as a single standalone file to a machine with no
`oceanicu_3d` checkout at all (see its own header comment), so tying it
to `running/bin` being on `PATH`, or to the rest of `running/` being
present one directory up, would work against the one thing it's
designed for.

**`oceanicu-experiments` is never required -- it's purely convenience.**
`python /abs/path/to/oceanicu_3d/running/oceanicu_experiments.py` does exactly
the same thing, always, with no wrapper needed; substitute it freely
anywhere `oceanicu-experiments` appears below if `PATH` isn't set up yet, or
never will be on a given machine. `PATH` is the preferred way once it's
in place, purely because there's less to type.

**First time on a new machine:** `setup_experiment_tracking.sh hpc|relay|workstation ...`
sets up the env vars (`OCEANICU_EXPERIMENT_DB`/`OCEANICU_EXPERIMENT_ROOT_BASE`/
`OCEANICU_HPC_COMMANDS_DIR`/`OCEANICU_HPC`, `hpc` role only -- see "Set up an
experiment" for what that last one actually gates) and directory scaffold
(`hpc_commands/`) for that machine's role in one go -- see the script's own
header for exact usage. The `hpc` role takes **two independent paths**, not
one: the real experiment tree (`OCEANICU_EXPERIMENT_ROOT_BASE`, where the
registry DB and staged/running experiment files live) and `hpc_commands/`
(`OCEANICU_HPC_COMMANDS_DIR`, the small transient command-queue directory)
-- they have different lifecycles and no required relationship to each
other, so neither has to nest inside the other. It's a single, standalone
file with no dependency on this repo, so it's the one thing worth
copying ahead of everything else onto a machine that has no git access
at all (e.g. the HPC).

**A database path is only needed on a machine that can actually reach
the authoritative registry -- there is no default anywhere, not even a
hardcoded production one, and most machines don't need one at all.**
`--queue`/`stage` (see "Command queue" below -- the default, correct way
for almost everybody) never open a real registry connection, so a
workstation that only ever queues commands and stages files needs no
`OCEANICU_EXPERIMENT_DB` at all. `oceanicu-experiments --dry-run` is the same way
(happy to run with no DB configured, see "Dry-run" below). A database
path is required for everything else -- `list`/`show` against a real or
mirrored registry, and any direct write command run where a live path
genuinely exists (the production machine itself, or the `ssh://` relay)
-- either `--db PATH` on the command, or `export OCEANICU_EXPERIMENT_DB=PATH`
in the environment, including on the `sbatch` command line itself for
`run_chunk.slurm` (see "Launch it"). This code runs on whatever
machine/cluster a job lands on, with whatever folder layout that machine
has, so no path is safe to assume there either. Point at a scratch DB
while testing (`OCEANICU_EXPERIMENT_DB=/tmp/test.sqlite`) with zero risk of
touching the real one just because a flag was forgotten. Without a path
where one actually is required, the command fails fast with a clear
error rather than guessing.

## The core idea

- An **experiment** is one continuous simulation (e.g. `CNRM-ESM2-1/ssp126`)
  split into sequential **chunks**. You register the experiment once;
  chunks are executed and recorded automatically as SLURM jobs go.
- **What's next is never stored** -- it's always "wherever the last
  completed chunk left off," computed on the fly. So there's no separate
  chunk plan to keep in sync with reality, and changing chunk size
  mid-experiment (see below) just works.
- Everything for one chunk -- its logs, its 2d/3d output, **and** the
  restart file it saves -- lives together in one directory:
  `<experiment_root>/chunks/<NNN>_<start>_<stop>/`. The next chunk's
  `--load-restart` just points at the previous chunk's own restart file
  in *its* folder.

## Set up an experiment

**Use `--queue` -- this is the default, correct way for almost
everybody, not a fallback for the rare case.** It appends the exact same
command, with the exact same flags and validation, to a local YAML file
instead of touching a registry directly -- no network path to anything
required, nothing to get wrong about which machine you're on:

```bash
oceanicu-experiments --queue ~/hpc_commands/queue_kb.yaml add \
    --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --experiment-root NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --script generated_nse_cmip6.py \
    --config generated_nse_cmip6_config.yaml \
    --data-roots-file bb-server1_data_roots.yaml \
    --initial-date 2015-01-01 --stop-date 2099-12-31 \
    --chunk-kind annual --chunk-multiplier 5 \
    --np 192 --launcher srun
```

That queues the request; it doesn't touch a real registry yet. `rsync`
your queue directory to bb-server1, and it crosses from there to wherever
the authoritative registry actually lives, applied by
`get_commands_and_update_registry.py` -- see "Command queue" further
down for the full flow (staging a new experiment's own files, multiple people's
queue files, how it actually reaches the HPC). The flag reference below
applies identically either way.

<details>
<summary>The direct form -- only if you have an actual, live path to
the <em>authoritative</em> registry (rare; expand if that's you)</summary>

Same command, without `--queue`, run wherever that live path actually
is: on the production machine itself, or anywhere via the `ssh://` relay
(see "Working across machines"). **A copy of the registry sitting
somewhere reachable is not the same thing as a live path to it** -- if
bb-server1 (or anywhere else) only ever holds a read-only mirror (see
"Keeping bb-server1's copy of the registry up to date"), pointing `--db`
straight at that file "succeeds" with no warning, writes into a copy
with no effect on the real thing, and then silently vanishes the next
time a real push overwrites it. **On this project's actual HPC, nobody
ever runs this direct form by hand at all** -- every real write happens
through `get_commands_and_update_registry.py` replaying a queued entry,
which calls this exact same command internally, always against the real
local path.

Because bb-server1's mirror deliberately sits at the exact same path
string as the authoritative registry (so `push_registry_snapshot.sh`
needs no path translation), a path alone can't tell the two apart --
only the machine can. So `add` specifically checks for that machine
directly: it refuses to run unless `OCEANICU_HPC=1` is set (only by
`setup_experiment_tracking.sh`'s `hpc` role) or the target is an obvious `/tmp/`
scratch path (this project's own testing convention) or `--dry-run` is
set (already redirected to a throwaway copy regardless). This guard is
on `add` only for now, not the other direct write commands
(`pause`/`set-*`/etc.) -- same footgun, not yet extended there.

```bash
oceanicu-experiments add \
    --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --experiment-root NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --script generated_nse_cmip6.py \
    --config generated_nse_cmip6_config.yaml \
    --data-roots-file bb-server1_data_roots.yaml \
    --initial-date 2015-01-01 --stop-date 2099-12-31 \
    --chunk-kind annual --chunk-multiplier 5 \
    --np 192 --launcher srun
```

</details>

`experiment-id` is just a label -- use whatever's meaningful, but the
`<experiment>/<source>/<model>/<scenario>/<run-name>` shape (matching the
actual folder layout) is a sane convention. `script`/`config` are the
already-generated pyGETM driver script and its setup YAML (per-experiment, made
in advance -- this tool doesn't generate them) -- **always a bare
filename**, resolved against `experiment-root`, so they just need to live there
(an absolute path works too if one lives somewhere else). `chunk-kind` is
`annual`/`monthly`/`daily`, `chunk-multiplier` is how many of those per
chunk (5 x annual = 5-year chunks).

**`experiment-root` can be relative too**, for the same reason `script`/`config`
can: you often `add` an experiment from a workstation that doesn't know exactly
where its output will actually land on the production machine (it's
often, but not always, the same relative path as `experiment-id`, as above --
`--experiment-root` is always explicit, never inferred from `experiment-id`). A
relative `experiment-root` is resolved against `OCEANICU_EXPERIMENT_ROOT_BASE`, an env
var set independently on each machine that actually touches this experiment's
files (chunk_runner.py, `run_chunk.slurm`, is_paused's own PAUSE-file
check) -- not stored in the DB, same idea as
`OCEANICU_EXPERIMENT_DB`/`OCEANICU_RELAY_DIR`. An absolute `experiment-root` is used
as-is and needs no base path at all; existing experiments registered with one
keep working unchanged. If a relative `experiment-root` is used and
`OCEANICU_EXPERIMENT_ROOT_BASE` isn't set on whichever machine is currently
touching the filesystem, that machine fails loudly rather than guessing.

```bash
# ONLY needed on the production machine (or wherever actually touches
# this experiment's files) -- never on a workstation that only ever queues.
export OCEANICU_EXPERIMENT_ROOT_BASE=/data/OceanICU/oceanicu_3d/experiments
```

**Spelled out, since it's the whole point of `--queue`:** nothing about
a relative path is resolved when you queue it, and nothing is baked in
at any point while it's in transit (`hpc_commands/`, `rsync`, the
`--pull-from` step). `experiment_tracking.py`'s `resolve_experiment_root` does the
resolving -- and only the resolving, never the storing -- fresh, every
single time something needs an actual filesystem path, against
`OCEANICU_EXPERIMENT_ROOT_BASE` **as set on the machine asking at that
instant**. Concretely, for an experiment queued from a workstation that has
never seen the HPC's directory layout: the DB row keeps the relative
`experiment-root` you typed; `oceanicu-experiments stage` (wherever it
was run -- see "Command queue") resolves it once, against THAT
machine's own `OCEANICU_EXPERIMENT_ROOT_BASE`, to know where to write
the experiment's generated files; `get_commands_and_update_registry.py`'s
own `add` handling resolves it again on the HPC, just to check those
files actually arrived there (see "Command queue" -- `bin/pull_experiment_files.sh`
is what gets them there, not this resolution step itself);
`chunk_runner.py`/`run_chunk.slurm` resolve it yet again, independently,
every chunk. Multiple resolutions of the same stored string, each on
its own machine, landing on a coherent path only because every machine
that matters (the HPC, specifically) is reading the same env var --
never the workstation's own (which typically doesn't even have
`OCEANICU_EXPERIMENT_ROOT_BASE` set, and doesn't need to, since it's
just a local staging mirror there, not the real thing). An absolute
`--experiment-root` sidesteps all of this identically everywhere, which
is exactly why it's the escape hatch when an experiment's output needs
to live somewhere outside the normal base path.

`launcher` defaults to `srun` (matches the real production SLURM
scripts); pass `--launcher mpiexec` if a particular setup needs it
instead.

`--fabm` overrides FABM at chunk-run time, without regenerating the
driver script -- real testing need: run the exact same setup with and
without FABM. Mirrors the generated driver script's own `--fabm`/
`--no-fabm` (see `pygetm_config.codegen`'s `_emit_argparse`) one level up:

```bash
--fabm                    # force FABM on, reusing the script's own configured path
--fabm /path/to/fabm.yaml # force FABM on with a specific path
--no-fabm                 # force FABM off, regardless of the script's own setting
# omit entirely           # no override -- run with whatever the script was generated with
```

## Launch it

**This has to run on the production machine itself** -- `sbatch` submits
to whatever SLURM cluster the current shell can reach, so this is never
something a workstation/queue-only user does; on this project's actual
HPC it's the one thing PML does by hand, per the experiment's own deliberate
never-automatic-submission rule (see "Command queue").

```bash
sbatch --export=EXPERIMENT_ID='NSe/CMIP6/CNRM-ESM2-1/ssp126/run01',OCEANICU_EXPERIMENT_DB='/path/to/experiment_registry.sqlite' run_chunk.slurm
```

Both variables are required on this first submission -- `run_chunk.slurm`
refuses to guess either, same as the Python layer, since there's no path
that's safe to assume on a machine whose folder layout isn't known in
advance. SLURM's `--export` replaces the job's entire environment with
just what's listed, so both have to be given here, not just exported in
the submitting shell; every self-resubmission after that (next chunk, or
next experiment) carries both forward automatically (`OCEANICU_RELAY_DIR` too,
if it was set -- see "Working across machines" below for when that
applies).

`run_chunk.slurm` locates chunk_runner.py/experiment_tracking.py at its own
runtime location, not a hardcoded path -- `sbatch path/to/run_chunk.slurm`
works from wherever it's actually deployed on that machine, whoever's
account that is.

That job runs the next chunk (chunk 0, since nothing's run yet -- no
`--load-restart`), then submits itself again for chunk 1, and so on,
until the experiment reaches its `stop_date`, gets paused, or a chunk fails (in
which case it stops resubmitting and leaves the failure for you to look
at -- it never blindly retries).

Every resubmission (next chunk, or next experiment -- see below) is a genuinely
fresh `sbatch` call: new SLURM job ID, fresh walltime, goes through the
normal scheduler queue like any other job. It is not a job array and not
an in-place continuation of the current allocation, so there can be a
real wait between one job finishing and the next one starting if the
cluster is busy.

**Pause the hand-off between jobs for a while, live, on a system that's
already running** -- e.g. "the HPC needs to be used for something else
for a while" -- with:

```bash
oceanicu-experiments delay-all --seconds 3600   # wait 1h before the next submission
oceanicu-experiments delay-all --clear          # cancel early
```

Unlike pause/resume (a separate, existing mechanism -- see below -- which
stops resubmission indefinitely until a human runs `resume`), this is a
TIMED pause: the next self-resubmission (next chunk of the same experiment, or
the next queued experiment -- never while a chunk is actually executing) waits
out the remainder then proceeds automatically, no manual resume needed.
It's genuinely live-adjustable, not just settable-once-at-launch: run
`delay-all --seconds N` again with a new value at any time, including
while a job is already mid-wait because of an earlier call -- the wait is
polled, not one fixed sleep, so a shortened, extended, or cleared delay
takes effect within the poll interval (60s), not only on the next
hand-off. Mechanically this is a `DELAY_ALL` file next to the registry
DB, mirroring the `PAUSE_ALL` sentinel's own convention (see
`experiment_tracking.chunk_delay_sentinel_path` for the raw file if this tool
itself is unreachable) -- content is the delay in seconds, its own mtime
marks when it was set.

(The no-SLURM `test_experiment_tracking/run_chunk_local.py` stand-in honors the
same sentinel, for the same reason, if you're testing this locally
first.)

## The queue

Once an experiment reaches its `stop_date` cleanly (`complete` or
`complete_with_warnings`), its job chain doesn't just stop -- it looks up
the highest-priority `not_started`, unpaused experiment in the registry and
`sbatch`s *that* one next, same mechanism as chunk-to-chunk. So in
practice you only need to manually `sbatch` once (or once per experiment you
want started concurrently); everything after that is picked up
automatically as allocations free up. An experiment that **fails** does *not*
auto-advance to the next queued one -- that stays a deliberate stop, so a
failure doesn't silently vanish under a pile of unrelated experiments.

Priority controls this queue order (higher first, `experiment_id` alphabetical
as a tiebreak) and can be changed at any time, including for an experiment
that's already going:

```bash
oceanicu-experiments set-priority --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --priority 10
```

It only affects which `not_started` experiment gets picked up next -- it has no
effect on an experiment that's already `in_progress`.

## Check status

```bash
oceanicu-experiments list                          # everything
oceanicu-experiments list --status in_progress
oceanicu-experiments list --like CNRM-ESM2-1        # substring match on experiment_id
oceanicu-experiments show --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01   # + full chunk history
```

`status` on an experiment is one of:

| status | meaning |
|---|---|
| `not_started` | registered, no chunks run yet |
| `in_progress` | chunks are running / more are expected |
| `paused` | pause requested or sentinel present, no chunk currently running |
| `complete` | reached `stop_date`, every chunk exited 0, no NaN ever flagged -- a genuinely clean finish |
| `complete_with_warnings` | reached `stop_date`, but a chunk was retried and/or a NaN was flagged along the way -- reached the end, but look at the chunk history before trusting it |
| `failed` | the most recent chunk failed and nothing has re-run it yet |

Only `complete` means "all done and in good shape" without needing to
read further; `complete_with_warnings` is the flag for "finished, but
check it."

## Pause / resume (soft-kill)

Two independent ways, either is enough to pause -- and **both only take
effect between chunks**, never mid-chunk, so a currently-running
simulation always finishes that chunk cleanly first, restart file and
all, before anything stops.

```bash
# the normal way
oceanicu-experiments pause  --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01
oceanicu-experiments resume --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01
oceanicu-experiments pause  --all      # everything
oceanicu-experiments resume --all

# the emergency way -- works even if the Python env is unreachable, no
# tooling required, just `touch`. PAUSE_ALL lives next to whichever DB
# file is actually configured (wherever that is on this machine) -- not a
# fixed path, find it with:
python -c "import experiment_tracking as rt
with rt.connect() as conn: print(rt.pause_all_sentinel_path(conn))"

touch <experiment_root>/PAUSE          # this experiment only
touch <the path printed above>  # everything
rm    <the path printed above>  # resume everything
```

Use the `PAUSE_ALL` sentinel if the HPC is overloaded and you need
everything to stop cleanly without touching the database at all.

## Change chunk size mid-experiment

```bash
oceanicu-experiments chunk-size --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --chunk-multiplier 10
```

Only affects chunks that haven't run yet. Restart files are named by
actual date, not by chunk index, so a size change never conflicts with
what's already on disk.

## Pace one experiment's own chunks

Persistent, per-experiment setting -- wait N seconds before EACH future
resubmission of THIS experiment's own chunks (or before it's picked up as the
next queued experiment), unlike `delay-all` above (a global, one-shot TIMED
pause covering every experiment). Default 0 (no delay) if never set:

```bash
oceanicu-experiments add ... --chunk-delay-seconds 30   # at registration time
oceanicu-experiments set-chunk-delay --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --seconds 30
oceanicu-experiments set-chunk-delay --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --seconds 0  # cancel
```

Same "takes effect on the very next hand-off, never retroactively"
behaviour as `chunk-size`/`set-stop-date` above -- read fresh from the DB
each time, not cached anywhere. Shown in both `list` and `show`.

## Change data-roots-file or np mid-experiment

Same reason `experiment-root` can be relative (see "Set up an experiment" above): the
machine that added an experiment doesn't always know the right `data-roots-file`
for wherever it actually ends up running, and `np` sometimes turns out
wrong for the real target machine's node layout -- both can be changed
after the fact, same next-hand-off-only semantics as `chunk-size`/
`set-stop-date`/`set-chunk-delay`, never affecting a chunk already
running:

```bash
oceanicu-experiments set-data-roots-file --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --path bb-server1_data_roots.yaml
oceanicu-experiments set-np --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --np 192
```

Both shown in `show`'s experiment table.

## Where a chunk was actually submitted from

Each row in `chunks` records `submitted_host` -- the hostname of
whichever machine actually issued that chunk's submission (normally the
production machine's own `sbatch` self-resubmission, but `chunk_runner.py`
can be invoked by hand for testing too, so this records reality per
chunk rather than assuming). Shown in `show`'s chunks table. Distinct
from `history`'s `user` column (which machine vs. which account) and
from `experiment_root`'s own machine-dependence (where an experiment's files live vs.
where a given chunk was submitted from -- normally the same machine, but
not guaranteed to be, e.g. if someone runs `chunk_runner.py` by hand from
a login node different from wherever `sbatch` jobs usually land).

## Run only partway, or change the target mid-experiment

Some experiments only need to run to 2050, not 2100 -- that's just
`--stop-date 2050-12-31` at `add` time, nothing special.

Changing the target while an experiment is already going (e.g. it's currently at
2035 and you decide to extend to 2050, or cut it short) works too:

```bash
oceanicu-experiments set-stop-date --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --stop-date 2050-12-31
```

Nothing about an experiment's dates is cached anywhere -- every chunk and every
status check reads `stop_date` fresh from the registry -- so this takes
effect on the very next chunk, no other bookkeeping needed. Shrinking
`stop_date` below a date already reached just marks the experiment `complete`
towards its (revised) goal; it never deletes or rolls back chunks already
run past the new date.

## Rerun

```bash
oceanicu-experiments rerun --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --from-current
oceanicu-experiments rerun --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --from-chunk 4
oceanicu-experiments rerun --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --from-scratch
```

This only rewinds the *tracked history* (drops the DB record for that
chunk and everything after it) -- it never deletes files. The next
`sbatch` naturally redoes chunk N onward, loading the same restart as
before. If a chunk directory from the earlier attempt is still there,
`chunk_runner.py` archives it aside (`<dir>.attempt-<timestamp>`) rather
than overwriting it, so the old logs are still there if you need to
compare.

`--from-current` (or no flag at all) redoes the last chunk on record.
`--from-scratch` is the same primitive with chunk 0 -- drops everything,
next experiment starts at `initial_date` with no restart.

**A rerun after manually editing the driver script (a real, expected
workflow -- a chunk blows up, you fix a bug in the script, rerun) shows up
in the history log automatically**, not just as a "rerun happened" line:
every chunk records a content hash (sha256) of the script/config it
actually ran with, and the next chunk that starts compares its own hash
against the previous one, logging a `script_changed`/`config_changed`
history entry if they differ. When the chunk being redone is the experiment's
*first* one (nothing earlier to compare against), the dropped chunk's own
hash is instead embedded directly in the `rerun` event's text, so the
before/after is still fully visible -- just read from one line instead of
two. Add `--note "why"` to `rerun` to record the reason alongside it:

```bash
oceanicu-experiments rerun --experiment-id ... --from-current --note "fixed off-by-one in river forcing"
```

`oceanicu-experiments show --experiment-id ...` prints the full history (who did
what, when, including these events) as its own table, and the chunks
table shows each chunk's own script/config hash (first 12 hex chars).

## Remove an experiment from the registry

```bash
oceanicu-experiments remove --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01
```

Only removes the registry/chunk-history rows -- never touches files.
Refuses if the experiment is `in_progress` unless you pass `--force` (pause it
first, normally).

## Dry-run any `oceanicu-experiments` command

```bash
oceanicu-experiments --dry-run chunk-size --experiment-id ... --chunk-multiplier 2
oceanicu-experiments --dry-run pause --all
oceanicu-experiments --dry-run set-stop-date --experiment-id ... --stop-date 2050-12-31
```

`--dry-run` goes before the subcommand and works with any of the direct
(non-`--queue`) commands -- it doesn't combine with `--queue` at all
(queuing never opens a registry connection in the first place, so
there's nothing for `--dry-run`'s registry-diff preview to run against).
It is a real execution, not a simulated one -- it copies the configured
registry to a timestamped file in `/tmp`, runs the actual command against
*that copy only*, and reports:

- the exact before/after diff of every changed experiment row (and chunk count,
  if that changed too)
- what `run_chunk.slurm` would **actually submit next** for the experiment(s)
  involved -- real dates, real chunk directory, real `--load-restart`/
  `--save-restart` paths, the real launch command -- by really invoking
  `chunk_runner.py --dry-run` against that same scratch copy, not by
  re-deriving the logic separately where it could drift out of sync
- where the resulting scratch DB was left, so you can inspect it further
  yourself (`sqlite3 <path>`, or `oceanicu-experiments --db <path> show ...`)

The real registry is never opened for writing. Unlike every other
command, `--dry-run` doesn't need `--db`/`OCEANICU_EXPERIMENT_DB` configured at
all -- with neither set it just starts the scratch copy completely empty
instead of copying anything, so you can try things out with zero setup.

## Testing a script/config without the registry

`chunk_runner.py` has a standalone mode with **zero database
interaction** -- nothing looked up, nothing written -- for trying a
script/config/date-range by hand before it's registered:

```bash
chunk-runner \
    --script generated_nse_cmip6.py \
    --start 2015-01-01T00:00:00 --stop 2015-02-01T00:00:00 \
    --save-restart /tmp/test_restart.nc \
    --chunk-dir /tmp/test_chunk --np 4 --launcher srun \
    --conda-env pygetm    # only needed if you haven't already `conda activate`d it
```

No `--config`: driver scripts generated by `pygetm_config.codegen`'s
`--dump-python` have their config baked in as a "literal, standalone
reproduction" (see the generated script's own docstring) -- they take no
positional arguments at all, only `--start`/`--stop`/`--dry-run`/
`--load-restart`/`--save-restart`/`--data-roots-file`. Passing a config
path used to be accepted here and silently broken every invocation
(fixed 2026-08-24) -- the driver script has no positional CLI arguments
to receive it.

Add `--dry-run` to just print the command without executing it.

## Checking a standalone experiment's status

Standalone mode has nothing to check it *through* -- no SLURM (`squeue`
doesn't exist on most machines this runs on, e.g. bb-server1), no DB row
(zero database interaction, by design), and no `.current_chunk_dir`
pointer file either (that's only written in tracked mode). What you have
instead is the OS process itself and whatever log the driver script
writes -- both entirely local, no tooling required:

```bash
# is it still running, and for how long?
ps aux | grep <driver-script-name>          # e.g. generated_nse_cmip6.py
ps -o etime= -p <pid>

# what's it actually doing right now?
tail -f <chunk-dir>/getm-0000.log
```

`<chunk-dir>` is whatever you passed to `--chunk-dir` (or `cwd` at launch
time if you didn't set it -- standalone mode doesn't record or print it
anywhere else, so keep track of it yourself). On a multi-rank experiment each
MPI rank writes its own `getm-NNNN.log` in that same directory; rank 0's
is the one with the overall experiment log.

## Multi-user note

The DB uses SQLite's WAL mode specifically so multiple SLURM jobs across
*different* experiments can write status concurrently without corrupting
anything.

Two chunks of the *same* experiment are guarded against running concurrently --
`chunk_runner.py` refuses to start a new chunk while one is already
recorded `running` for that `experiment_id` (guards against an accidental
double-submit racing the current chunk). If that recorded chunk's SLURM
job is confirmed gone via `squeue`, or `squeue` is unavailable and the
chunk has been "running" for more than 4 days (longer than any single
chunk should ever take), it's treated as crashed/orphaned: marked
`failed` and left for a human `rerun --from-current` rather than silently
retried -- same as any other failure, never auto-continued past.

## Accidental-deletion protection

The registry doesn't live inside a git repo of its own, and SQLite has no
snapshot/undo concept beyond "the current database" -- WAL checkpointing
just folds pending writes into the main file, it isn't a backup. So
`experiment_tracking.py` keeps its own: every `OCEANICU_DB_BACKUP_EVERY_N_WRITES`
writes (default 5; set to `0` to disable), it takes a WAL-safe snapshot
via SQLite's own backup API (not a raw file copy, which can catch the
main file mid-checkpoint and miss pending WAL content) and commits it
into a small local git repo living right next to the DB:
`<db-path>.backups/`. Read-only commands (`list`, `show`, dry-run
previews, ...) never count towards the threshold -- only a connection
that actually changed something does.

This is best-effort and silent by design: a backup failure prints a
warning but never breaks the real command that triggered it, and never
retroactively resets the write counter on failure (so a transient
failure just retries at the next write instead of going quiet for a
whole N-write cycle). To recover a deleted/corrupted registry:

```bash
cd /abs/path/to/experiment_registry.sqlite.backups
git log --oneline                      # find the snapshot you want
git show <sha>:experiment_registry.sqlite > /abs/path/to/experiment_registry.sqlite
```

The backup repo has no automatic pruning -- for a DB this small, unlimited
history is cheap; squash/prune by hand later if it ever matters.

## Working across machines with no direct network path

Experiments are often ADDED from one machine (wherever you're planning from) and
RUN on another (the SLURM/production machine) -- and those two often
can't reach each other directly at all. There's no multi-writer sync for
this (SQLite doesn't do that safely -- periodically copying the DB file
back and forth risks one side's writes silently clobbering the other's).

Two different answers to that, depending on what's actually reachable:

- **A live, two-way network path exists** (this section): the registry
  lives in exactly ONE place, and a **third machine that both sides CAN
  reach acts as a relay** -- both the add-machine and the production
  machine operate on the exact same database, remotely, over SSH to that
  relay.
- **No live path exists at all** (e.g. a fully network-isolated HPC
  reachable only via a human's own terminal login, whose login node can
  at best only ever *initiate* an outbound connection): see "Command
  queue" further down instead -- commands and new-experiment files cross the
  boundary as plain files via whatever transport actually exists
  (`rsync`, a human carrying a file), not a live connection.

The rest of this section is about the relay case:

**One-time setup on the relay:** copy `experiment_tracking.py` and
`experiment_tracking_server.py` there together, in the same directory (nothing
else needed -- no packaging, same as everywhere else in this system).

**Then, from any machine that can SSH to the relay** (add-machine or
production machine alike), point at the registry with an `ssh://` DB path
instead of a local one:

```bash
export OCEANICU_EXPERIMENT_DB=ssh://oceanicu-relay/abs/path/to/experiment_registry.sqlite
export OCEANICU_RELAY_DIR=/abs/path/to/running   # where experiment_tracking_server.py lives, ON the relay
oceanicu-experiments add --experiment-id ...          # exactly the same as local use from here on
```

A copy-pasteable starting point for both lines lives in
`relay.env.example` -- copy it to `relay.env` (gitignored) and source it,
same content on every machine. It also sets `PATH` to include
`running/bin` (see "Use `oceanicu-experiments`" at the top) as a convenience,
same as anywhere else.

**`oceanicu-relay` is a `~/.ssh/config` `Host` alias, not a raw
hostname -- this is the recommended way to point at the relay**,
specifically because the add-machine and the production machine will
usually need different usernames (sometimes a different `IdentityFile`,
occasionally a `ProxyJump`) to reach the very same relay, and `~/.ssh/config`
is where that kind of per-machine connection detail is already supposed
to live -- not leaked into an application-level environment variable that
would then have to differ per machine too. Set up per machine:

```
# ~/.ssh/config, on EACH machine that talks to the relay --
# same alias name everywhere, details filled in per machine
Host oceanicu-relay
    HostName bb-server1
    User alice              # whatever THIS machine's account on the relay is
    # IdentityFile ~/.ssh/id_relay   # if it needs its own key
    # ProxyJump some-gateway        # if this machine can't reach it directly
```

With that in place, `OCEANICU_EXPERIMENT_DB`/`OCEANICU_RELAY_DIR` (and
`relay.env`) become byte-for-byte identical across every machine -- only
the `~/.ssh/config` stanza varies, which is one-time setup per machine,
not something anyone has to remember on every command.

Skipping the alias and writing `ssh://user@relay-host/...` directly also
works (RemoteConn just runs plain `ssh <whatever's in the URI>`, so
anything `ssh` itself accepts as a target is valid here) -- only use this
if you have a real reason not to want an alias; the alias form is
strictly less to get wrong day to day.

`OCEANICU_RELAY_DIR` is a property of the relay's own deployment, not of
any one database, so it's always separate from the DB path itself. Every
command works exactly as documented everywhere else in this file --
`add`/`list`/`pause`/`rerun`/`--dry-run`/`chunk_runner.py --dry-run`, all
of it -- the `ssh://` prefix is the only thing that changes. Each call is
one `ssh relay-host` round trip; given how infrequently these actually
happen (chunk boundaries are hours-to-days apart, not a tight loop),
that's a total non-issue even though the production machine's `chunk_runner.py`
still runs the actual simulation fully locally, only the bookkeeping goes
over the relay.

For `run_chunk.slurm`, put both on the `sbatch` command line together --
SLURM's `--export` replaces the whole job environment with just what's
listed, so both have to be given on the first submission (every
self-resubmission after that carries both forward for you):

```bash
sbatch --export=EXPERIMENT_ID='...',OCEANICU_EXPERIMENT_DB='ssh://...',OCEANICU_RELAY_DIR='...' run_chunk.slurm
```

**One real limitation:** the per-experiment `<experiment_root>/PAUSE` sentinel file
(see "Pause / resume" above) always lives on whichever machine actually
has that experiment's output -- normally the production machine, never the
relay -- so it's checked locally by whichever machine is asking, not
relayed. Concretely: `oceanicu-experiments --dry-run` run from the
add-machine can preview everything else correctly, but can't see a
per-experiment PAUSE file that only exists on the production machine's
filesystem (it'll just read as absent). The DB `control` column and the
`PAUSE_ALL` sentinel (which lives next to the DB, i.e. on the relay) both
work correctly from anywhere, including in that preview.

Same category, second instance: a **relative `experiment_root`** resolves
against `OCEANICU_EXPERIMENT_ROOT_BASE` (see above), which is also set
per-machine and normally only on the production machine. The
`--dry-run` "what run_chunk.slurm would do next" preview runs
`chunk_runner.py --dry-run` for real, right there on the add-machine, so
it needs a real resolved path -- if that machine has no
`OCEANICU_EXPERIMENT_ROOT_BASE` of its own, that one preview step can't render
and says so explicitly instead of showing a raw error. Not a sign of a
real problem: the actual `add` still registers correctly either way, and
resolution happens for real once the chunk actually runs on the
production machine, which does have its own `OCEANICU_EXPERIMENT_ROOT_BASE` set.

## Command queue: registering experiments with no network path to the registry

The relay above (`ssh://` + `experiment_tracking_server.py`) needs a live,
two-way network path -- it doesn't work when the registry's own machine
can't be reached from outside AT ALL (a fully network-isolated HPC,
reachable only via a human with terminal access to it, that in turn only
ever talks to ONE other machine -- e.g. this project's own PML HPC,
which only ever interacts with bb-server1, never GitHub directly). For
that case, use a command queue instead: a plain directory of small files
that carries *requests* across the boundary via `rsync`, at every hop.
A new experiment's actual driver-script/config files travel separately
(see below) -- same `rsync`-at-every-hop principle, just a different,
independently-configured directory, since the two have very different
lifecycles (the queue goes void once applied; an experiment's files
persist for as long as the experiment does).

**This is deliberately NOT part of the `oceanicu_3d` git repo.**
`hpc_commands/` is a plain data directory -- same category as
`experiments/` itself, not source -- living at a fixed, agreed location
that every machine in the chain can reach one hop of:
`bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands/`. The
HPC never needs to `git pull`/clone anything from GitHub to run --
`oceanicu_3d`'s `running/` are deployed there as plain files (however
that already happens), and `hpc_commands/` moves the exact same way, via
`rsync`, never git. Keeping it out of git also sidesteps the earlier
worry about it looking like a second, git-tracked copy of the real
(large, definitely-not-in-git) `experiments/` output tree -- it isn't
one; it's a small, disposable relay directory, not a repo.

**On your own workstation**, compose commands and stage files into a
local staging directory (anywhere -- outside the git repo):

```bash
oceanicu-experiments --queue ~/hpc_commands/queue_kb.yaml add \
    --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run02 --experiment-root NSe/CMIP6/CNRM-ESM2-1/ssp126/run02 \
    --script generated_nse_cmip6.py --config generated_nse_cmip6_config.yaml \
    --initial-date 2015-01-01 --stop-date 2099-12-31 --chunk-kind annual --chunk-multiplier 5 --np 192
```

No registry access needed at all -- this never touches a real DB. Any
write subcommand works this way (`add`, `set-stop-date`, `set-priority`,
`pause`, `rerun`, ...) -- `list`/`show`/`stage` don't touch a registry
and refuse to queue, since there's nothing to apply later. `--queue`
isn't an `add`-only special case; it's a flag on every write subcommand
(`_add_common` wires it up once, generically), so this works exactly the
same way for, say, `chunk-size` on an experiment that's already registered:

```bash
oceanicu-experiments --queue ~/hpc_commands/queue_kb.yaml chunk-size \
    --experiment-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run02 --chunk-kind monthly --chunk-multiplier 3
```

Each call appends one entry to the same YAML file, e.g. (this one from
the `add` above):

```yaml
commands:
  - id: cmd-20260829T072716Z-46d3
    action: add
    args: {experiment_id: ..., experiment_root: ..., script: ..., ...}
    queued_at: "2026-08-29T07:27:16+00:00"
    queued_by: kb
    status: pending          # pending -> applied | failed
    applied_at: null
    note: null                # command's own stdout on success, or the error on failure
```

A fuller worked example -- three entries covering the whole lifecycle
(one applied, one that "applied" but actually hit the `set-priority`
typo gap documented further down, one still pending) -- lives in
`queue_example.yaml`,
right alongside this file. Reference/illustration only, not something to
copy into place: your own real queue file is created and appended to
automatically by `oceanicu-experiments --queue ...`, never hand-edited.

**Multiple people can queue commands from different places** (e.g. you
and someone else, both able to `rsync` into bb-server1's
`experiments/hpc_commands/`). Rather than all writing to one shared
file, **each person gets their own**, `queue_<name>.yaml` -- avoids any
risk of one person's `rsync` clobbering another's in-flight edit to the
same file.

**A brand-new experiment also needs its actual driver script/config
physically present** at `experiment_root` before any chunk can start --
the queue entry alone only carries the DB row. `stage` writes exactly
the right files there directly (never a whole directory verbatim -- a
real generated-output folder commonly has `__pycache__/`, logs, etc.
alongside the 2-3 files an experiment actually needs) -- and, once an
experiment is actually running, real chunk output (logs, restarts,
`.nc` results) too:

```bash
oceanicu-experiments stage --experiment-root NSe/CMIP6/CNRM-ESM2-1/ssp126/run02 \
    --source-dir /wherever/you/generated/the/driver/script
    # --include defaults to generated*.py, generated*.yaml; --exclude-dir
    # defaults to __pycache__; --exclude defaults to *.nc
```

This `rsync`s (filtered by `--include`/`--exclude-dir`/`--exclude`, so
it's real rsync include/exclude syntax, not a reimplementation) directly
into the experiment's REAL, resolved location --
`resolve_experiment_root(experiment_root)`, i.e. `$OCEANICU_EXPERIMENT_ROOT_BASE/NSe/CMIP6/CNRM-ESM2-1/ssp126/run02/`
on whichever machine ran `stage` -- exactly the same path
`chunk_runner.py`/`is_paused` resolve against, on whichever machine
actually runs the chunks. `stage` is **not** a separate staging area
nested inside `hpc_commands/` -- `hpc_commands/` stays queue-YAML-only
(see the intro above), and the experiment tree it writes into has no
required relationship to it at all; the two are configured completely
independently (`--queue-dir`/`hpc_commands/`'s own location is
essentially fixed/trivial, while `OCEANICU_EXPERIMENT_ROOT_BASE` is the
one that's genuinely per-machine and configurable).

**This makes same-named generated files across different experiments a
non-issue, on purpose.** Every experiment's generated driver script is
commonly named the same thing regardless of model/scenario
(`generated_nse_cmip6.py`, same filename for CNRM-ESM2-1, GFDL-ESM4,
MPI-ESM1-2-HR alike -- see this project's own real
`experiments/NSe/CMIP6/<model>/<scenario>/<run>/` layout). `stage`'s
destination is keyed by the full, resolved `experiment_root`, not just
the filename -- so three experiments staged this way land in three
separate subdirectories, never colliding, however identical their
filenames are. Confirmed by testing: three source directories with
identically-named `generated_nse_cmip6.py`/`_config.yaml` staged for
three different `experiment_root`s produced three distinct files with
distinct contents, no overwrite.

**`--exclude` (default `*.nc`) is a belt-and-braces guard, not the
actual mechanism keeping real output data out of what gets synced
between machines.** The default `--include` list is already a whitelist
(only `generated*.py`/`generated*.yaml` ever match), so a stray NetCDF
output file sitting next to a driver script -- a near-certainty here,
since `stage`'s destination IS the real experiment directory, which
fills up with exactly that once the experiment is actually running --
is already excluded by the trailing catch-all regardless. `--exclude`
exists for when `--include` is later widened for some other reason
(e.g. `--include '*'` to grab everything): rsync evaluates filter rules
in order and stops at the first match, and `--exclude` is placed BEFORE
the `--include` patterns in the actual command built, so `*.nc` keeps
losing to that rule no matter how broad `--include` gets. Confirmed by
testing, including the `--include '*'` case specifically. The exact
same filter is reused by `bin/pull_experiment_files.sh` (below) for the
same reason.

**Then `rsync` your local `hpc_commands/` to bb-server1** (queue files
only -- the experiment-files tree is a completely separate sync, same
filter as `stage`'s own, shown just below):

```bash
rsync -a ~/hpc_commands/ bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands/
rsync -a --include 'generated*.py' --include 'generated*.yaml' \
    --include '*/' --exclude '*' ~/experiments/ \
    bb-server1:/data/OceanICU/oceanicu_3d/experiments/
```

**From bb-server1 to the HPC**, both trees are automated, symmetric to
`push_registry_snapshot.sh` going the other direction, both run on the
HPC's **login node** specifically (the only place with outbound reach;
see "Keeping bb-server1's copy of the registry up to date"):

```bash
python get_commands_and_update_registry.py --db /local/path/experiment_registry.sqlite \
    --queue-dir /local/path/hpc_commands/ \
    --pull-from bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands
    # rsyncs bb-server1's hpc_commands/ in first (reports what changed,
    # or "up to date, nothing new"), THEN processes every queue_*.yaml
    # found there, combined and applied in queued_at order across all of
    # them, regardless of whose file an entry lives in. (--queue PATH
    # still works too, for a single exact file -- but never combined
    # with --pull-from, which syncs a whole directory.)

OCEANICU_EXPERIMENT_ROOT_BASE=/local/path/experiments \
    bin/pull_experiment_files.sh bb-server1:/data/OceanICU/oceanicu_3d/experiments
    # separate script, separate cron line -- same generated*.py/
    # generated*.yaml filter as `stage` itself, no --delete, so nothing
    # already present (including the registry DB and real chunk output
    # sitting in this same tree) is ever touched beyond what it pulls
    # in. This is what makes get_commands_and_update_registry.py's own
    # presence-check on `add` (below) actually reliable here.
```

Omit `--pull-from` to fall back to the older behavior (`apply_commands.py`'s
original, before this script was renamed): apply whatever's already
local, however it got there -- a human running `rsync` by hand, for
instance, if that's ever preferred over the automated pull.

**Don't wait for the cron interval if something's urgent.** The cron
line is just this exact same command run on a schedule -- nothing about
it is special or exclusive. If a batch of commands needs to land right
away (several urgent `pause`s, a `set-stop-date` that has to beat an
already-running chunk's next resubmission, whatever), `rsync` the queue
up to bb-server1 as usual and then, from an interactive shell on the
HPC's login node, just run the same command by hand:

```bash
# on the login node, any time -- not just when cron happens to fire
# (get-commands-and-update-registry once running/bin is on PATH, see
# "Use `oceanicu-experiments`" at the top -- or the full path, same as cron uses)
get-commands-and-update-registry --db /local/path/experiment_registry.sqlite \
    --queue-dir /local/path/hpc_commands/ \
    --pull-from bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands

# --db/--queue-dir both fall back to an env var if omitted
# (OCEANICU_EXPERIMENT_DB/OCEANICU_HPC_COMMANDS_DIR) -- if those are
# already exported (they usually are, in an interactive login-node
# shell), the equivalent shortcut is just:
get-commands-and-update-registry --pull-from bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands
```

Fine to run this even with the cron job also active -- applying is
idempotent (an already-`applied`/`failed` entry is never touched again),
so running it twice in reasonably quick succession just means the
second run reports "up to date, nothing new" / "nothing pending", no
double-apply. The one thing NOT covered by that idempotency: two
instances writing to the exact same queue file at the *literal same
instant* (this manual run landing mid-write of a cron-triggered one)
could in principle corrupt that file -- each write is "open, truncate,
write the whole thing," not an atomic rename, and nothing else backs
`hpc_commands/` up the way `push_registry_snapshot.sh` backs up the
registry. Vanishingly unlikely for a one-off manual run (write-back is
near-instant), and never a risk to the registry itself either way
(SQLite's own locking handles that) -- just don't make a habit of firing
this from two different terminals at once as a matter of course.

For each `pending` entry: an `add` first checks that `script`/`config`
are actually present at the real (resolved) `experiment_root` --
`stage` (wherever it ran) and `bin/pull_experiment_files.sh` (above) are
what put them there, not this step, which never copies anything itself,
only verifies. A missing file marks the command `failed` and never
registers a file-less experiment. Then every action replays through
`oceanicu_experiments.py`'s own CLI (reconstructed from the stored args
as real `--flag` values), so applying a queued command goes through
identical validation to running it directly. Already-`applied`/`failed`
entries are skipped, so re-running this script on a queue that hasn't
changed is always a safe no-op. The file is rewritten after *each*
command, not just at the end, so a crash partway through never loses
already-applied statuses.

**Known limitation of `--pull-from`:** it's a plain directory rsync
(`-au`), not a merge. If a queue file gets a genuinely new entry
appended upstream (someone's workstation) after the HPC already applied
and status-stamped some of that same file's earlier entries, the next
pull overwrites the whole file, reverting those earlier entries' status
back to `pending` -- there's no channel carrying status back upstream,
only new commands flowing down. Confirmed by testing: reapplying an
already-applied entry is usually harmless (most actions are idempotent
-- `set-stop-date`, `pause`, ...) except `add`, which fails loudly
(`experiment_id` already exists, a real but noisy `IntegrityError`) rather than
silently double-registering anything. No data corruption either way,
just a confusing-looking `failed` entry for an experiment that's actually fine
-- check `oceanicu-experiments show <experiment_id>` if one shows up unexpectedly.

**`get_commands_and_update_registry.py` never calls `sbatch`, for any
experiment, new or resubmitting.** Submitting a job is always a deliberate
manual action on whoever's machine actually runs SLURM -- this only
ever touches the registry's `experiments`/`history` rows. Once an experiment is
registered (and its files are in place), starting it is the same manual
`sbatch --export=EXPERIMENT_ID=...,OCEANICU_EXPERIMENT_DB=... run_chunk.slurm` as
always; after that, self-resubmission for future chunks is unaffected
and automatic as already documented above.

`get_commands_and_update_registry.py` refuses an `ssh://` `--db`
outright -- it only ever makes sense run locally, on the machine that
actually holds the registry.

**Known gap, not introduced by this mechanism:** `set-priority`/
`set-chunk-delay`/`set-stop-date`/etc. silently succeed even for a
nonexistent `experiment_id` (the underlying `UPDATE ... WHERE experiment_id = ?` just
matches zero rows, no error) -- a typo'd `experiment_id` in a queued command
will show as `applied`, not `failed`. Worth knowing when composing
queue entries; not yet fixed.

## Keeping bb-server1's copy of the registry up to date

The command queue above covers requests flowing IN to the HPC (add an
experiment, change a setting). The other direction -- status flowing OUT, so
you can actually see progress from bb-server1/your workstation -- is a
separate mechanism, `push_registry_snapshot.sh`. It doesn't take a new
snapshot itself; it ships out whatever `experiment_tracking.py`'s own
git-backup mechanism ("Accidental-deletion protection" above) already
committed, `rsync`'d to bb-server1 with `--chmod=a-w` (read-only on
arrival, so an accidental write against that copy fails loudly instead
of silently diverging the mirror).

**On this project's actual HPC, use the login-node cron -- it's the only
mechanism that works here, not one option among several:**

```bash
*/10 * * * * OCEANICU_EXPERIMENT_DB=/path/experiment_registry.sqlite /path/to/oceanicu_3d/running/bin/push_registry_snapshot.sh
```

(needs outbound reach, which the login node has and compute nodes don't).

**Same "don't wait for the interval" note as the command-queue side:**
this is just the same command on a schedule -- nothing stops you running
it by hand (`push_registry_snapshot.sh`, once `running/bin` is on `PATH`,
or the full path, same as cron uses) right after a chunk finishes if you
want bb-server1 updated immediately rather than within the next 10
minutes. Idempotent either way -- `push_registry_snapshot.sh` only ever
ships whatever the latest git-backup snapshot already is, so running it
twice in a row just re-sends (or, with `--checksum`, effectively no-ops
on) the same file.

There's also a best-effort, event-driven path built into `run_chunk.slurm`
itself -- right before each self-resubmission, it tries to `ssh` a
compute node to `$OCEANICU_LOGIN_NODE` (if that env var is set, e.g. via
`setup_experiment_tracking.sh`'s optional 4th `hpc` argument) and run
`push_registry_snapshot.sh` there. **Confirmed 2026-08-29, directly by
PML: compute nodes on this project's HPC cannot `ssh` to their own login
node at all** -- `test_compute_to_login_ssh.sbatch` was built to check
exactly this. So on THIS cluster, never set `OCEANICU_LOGIN_NODE` -- it
would just silently never fire (harmless, but pointless). The mechanism
stays in the code because it might work on a different cluster this
tooling gets deployed to someday, not because it's a live option here.

Since `$HOME`/`/work` are shared between login and compute nodes here
(confirmed 2026-08-29), any env var set once in `~/.bashrc` (via
`setup_experiment_tracking.sh`) is automatically visible on every compute node
too, via `run_chunk.slurm`'s own `source ~/.bashrc` -- no per-node setup,
no threading anything through `sbatch --export=`. That's what makes
`OCEANICU_EXPERIMENT_DB`/`OCEANICU_EXPERIMENT_ROOT_BASE`/`OCEANICU_HPC` work with a
single one-time setup call; it just doesn't help the event-driven push
specifically, since that needs actual network reach, not merely a
shared filesystem.

For a chunk finishing specifically (the moment most worth syncing
promptly): set `OCEANICU_DB_BACKUP_EVERY_N_WRITES=1` on the HPC so a
fresh snapshot commit exists after every single write, including every
`finish_chunk()` call -- otherwise the default (5) means a real snapshot
might lag a few writes behind the latest chunk completion.

**A third option, for near-immediate pushes without the compute-node-ssh
dependency:** `watch_registry_and_push.sh` -- a persistent `inotifywait`
watcher, run on the LOGIN NODE, that pushes the moment a new snapshot
commit actually exists (watches the `.backups/` snapshot file itself,
not the live registry, since that file changes exactly when there's
something new worth pushing). A persistent process on a shared login
node risks getting killed by idle/session-limit policies (the same
concern that ruled out a bare background polling loop for
`get_commands_and_update_registry.py`), so it's meant to be kept alive by its own cron
watchdog, `restart_registry_watcher.sh`, rather than started once by
hand and left unsupervised. Both live in `running/bin` (see "Use
`oceanicu-experiments`" at the top) -- handy for a one-off manual restart from
anywhere once that's on `PATH`, by their own name (e.g.
`restart_registry_watcher.sh`) from an interactive login-node shell.
**cron itself doesn't source `~/.bashrc`**, though (same caveat as the
`get_commands_and_update_registry.py` cron above), so PATH isn't
populated there unless the crontab line sets it explicitly -- the
crontab entry itself uses the full path instead:

```bash
# on the login node
0 * * * * OCEANICU_EXPERIMENT_DB=/path/experiment_registry.sqlite /path/to/oceanicu_3d/running/bin/restart_registry_watcher.sh
```

The watchdog checks a pidfile (`kill -0` on the recorded PID), not
`pgrep -f` on the script name -- `pgrep -f` matches a process's *entire*
command line, and testing this during development confirmed it
false-positives on anything else that happens to mention the filename
(a wrapping shell's own echoed command text was enough to fool it).
Verified end-to-end with a stand-in process: starts when nothing's
running, is a correct no-op when the watcher is alive, and correctly
restarts with a fresh PID after the watcher is killed and its pidfile
cleaned up by its own `trap EXIT`.

This needs `inotify-tools` (`inotifywait`) installed on the login node --
a standard package, but not guaranteed present, and this project's HPC
has no root access to install one the normal way (`apt`/`yum`). It's
also available via conda-forge (confirmed 2026-08-30: `conda search -c
conda-forge inotify-tools` finds it, versions up to 3.20.2) --
installable into an existing env with no root needed at all. That env's
own `bin/` isn't on `PATH` in a cron context, though (cron doesn't
activate conda, same reason it doesn't source `~/.bashrc`), so both
scripts accept an optional override pointing at the exact binary
instead of relying on `PATH`:

```bash
OCEANICU_INOTIFYWAIT=/path/to/envs/pygetm/bin/inotifywait
```

Set once in the crontab line for `restart_registry_watcher.sh`; the
watcher it starts inherits it automatically (plain env-var inheritance,
nothing extra needed). Falls back to a bare `inotifywait` search on
`PATH` if unset -- for a system install, or an interactive shell where
the right conda env is already active.

If `inotifywait` (with or without the override) is missing entirely,
the watchdog doesn't retry every interval forever -- it logs one clear
message the first time (a sentinel file suppresses repeats) and exits
without attempting to start anything, self-healing automatically if it
becomes available later. Verified: the message prints exactly once
across repeated runs while missing, and disappears (silently, no
special handling needed) once the binary -- real or overridden -- is
found.

Run this *alongside*, not instead of, the plain cron above -- if the
watcher or `inotifywait` itself turns out not to work as expected, the
periodic push still covers you.

## What's not built yet

- **No folder scanning.** `add`/`remove` are the only way experiments enter or
  leave the registry -- nothing walks the experiments tree looking for
  new `<model>/<scenario>/<run-name>` folders to register automatically.
- A **web status page** is planned as part of `ocean-post` -- not built
  here; this registry is the data source for it whenever that's ready.
- No `--output-dir` flag on the driver scripts themselves (that would
  need a change in `pygetm-config`'s codegen, deliberately left alone) --
  `chunk_runner.py` gets the same effect today by running the driver with
  its working directory set to the chunk folder.
