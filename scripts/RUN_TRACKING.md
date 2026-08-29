# Production run tracking

A small SQLite-backed system for running chunked pyGETM production
simulations on SLURM and keeping track of what's been run and what's
next, without hand-editing a spreadsheet.

Five files, no packaging. This is where they live in the repo; on any
machine that actually runs them (production machine, relay -- see
"Working across machines" below), deploy the ones that machine needs
together in one directory, wherever makes sense there -- `run_chunk.slurm`
finds its own deployed location at runtime (see "Launch it"), so there's
no path to hardcode or tell it about, and no requirement that every
machine use the same directory or even the same username:

| file | what it does |
|---|---|
| `run_tracking.py` | the SQLite schema + data-access functions. Not usually called directly. |
| `oceanicu_runs.py` | the CLI you use day to day: register runs, check status, pause/resume, rerun. |
| `chunk_runner.py` | runs exactly one chunk. Called by `run_chunk.slurm`; you don't normally invoke it by hand except when testing (see below). |
| `run_chunk.slurm` | the SLURM job. Runs one chunk, then resubmits itself for the next one -- or, once a run reaches its `stop_date`, for the next queued run (see "The queue" below). |
| `run_tracking_server.py` | RPC entrypoint for accessing the registry across machines with no direct network path between them -- see "Working across machines" below. Only needed on the relay machine, and only if you need that at all. |
| `apply_commands.py` | replays queued commands (see "Command queue" below) against the local registry -- only needed wherever the registry actually lives, and only if a live relay to it isn't possible. |

**First time on a new machine:** `setup_run_tracking.sh hpc|relay|workstation PATH`
sets up the env vars (`OCEANICU_RUN_DB`/`OCEANICU_RUN_ROOT_BASE`) and
directory scaffold (`hpc_commands/`) for that machine's role in one go --
see the script's own header for exact usage. It's a single, standalone
file with no dependency on this repo, so it's the one thing worth
copying ahead of everything else onto a machine that has no git access
at all (e.g. the HPC).

**Every command needs a database path -- there is no default anywhere,
not even a hardcoded production one.** This code runs on whatever
machine/cluster a job lands on, with whatever folder layout that machine
has, so no path is safe to assume. Either `--db PATH` on the command, or
`export OCEANICU_RUN_DB=PATH` in the environment -- including on the
`sbatch` command line itself for `run_chunk.slurm` (see "Launch it").
Point at a scratch DB while testing (`OCEANICU_RUN_DB=/tmp/test.sqlite`)
with zero risk of touching the real one just because a flag was
forgotten. Without either, every command fails fast with a clear error
rather than guessing -- **except** `oceanicu_runs.py --dry-run` (happy to
run with no DB configured at all, see "Dry-run" below), and **except**
`--queue`/`stage` (see "Command queue" below), which never open a real
registry connection at all -- no `OCEANICU_RUN_DB` needed on a
workstation that only ever queues commands and stages files; that
variable only matters on whichever machine actually holds the registry.

## The core idea

- A **run** is one continuous simulation (e.g. `CNRM-ESM2-1/ssp126`) split
  into sequential **chunks**. You register the run once; chunks are
  executed and recorded automatically as SLURM jobs go.
- **What's next is never stored** -- it's always "wherever the last
  completed chunk left off," computed on the fly. So there's no separate
  chunk plan to keep in sync with reality, and changing chunk size
  mid-run (see below) just works.
- Everything for one chunk -- its logs, its 2d/3d output, **and** the
  restart file it saves -- lives together in one directory:
  `<run_root>/chunks/<NNN>_<start>_<stop>/`. The next chunk's
  `--load-restart` just points at the previous chunk's own restart file
  in *its* folder.

## Set up a run

This is the direct form -- run it wherever you have an actual, live
path to the registry (on the production machine itself, or from
anywhere via the `ssh://` relay, see "Working across machines" below).
If there's no live path at all (a network-isolated HPC), use
`oceanicu_runs.py --queue ... add ...` instead -- see "Command queue"
further down; same flags, same validation, it just gets there via files
instead of a connection.

```bash
python oceanicu_runs.py add \
    --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --run-root NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --script generated_nse_cmip6.py \
    --config generated_nse_cmip6_config.yaml \
    --data-roots-file bb-server1_data_roots.yaml \
    --initial-date 2015-01-01 --stop-date 2099-12-31 \
    --chunk-kind annual --chunk-multiplier 5 \
    --np 192 --launcher srun
```

`run-id` is just a label -- use whatever's meaningful, but the
`<experiment>/<source>/<model>/<scenario>/<run-name>` shape (matching the
actual folder layout) is a sane convention. `script`/`config` are the
already-generated pyGETM driver script and its setup YAML (per-run, made
in advance -- this tool doesn't generate them) -- **always a bare
filename**, resolved against `run-root`, so they just need to live there
(an absolute path works too if one lives somewhere else). `chunk-kind` is
`annual`/`monthly`/`daily`, `chunk-multiplier` is how many of those per
chunk (5 x annual = 5-year chunks).

**`run-root` can be relative too**, for the same reason `script`/`config`
can: you often `add` a run from a workstation that doesn't know exactly
where its output will actually land on the production machine (it's
often, but not always, the same relative path as `run-id`, as above --
`--run-root` is always explicit, never inferred from `run-id`). A
relative `run-root` is resolved against `OCEANICU_RUN_ROOT_BASE`, an env
var set independently on each machine that actually touches this run's
files (chunk_runner.py, `run_chunk.slurm`, is_paused's own PAUSE-file
check) -- not stored in the DB, same idea as
`OCEANICU_RUN_DB`/`OCEANICU_RELAY_DIR`. An absolute `run-root` is used
as-is and needs no base path at all; existing runs registered with one
keep working unchanged. If a relative `run-root` is used and
`OCEANICU_RUN_ROOT_BASE` isn't set on whichever machine is currently
touching the filesystem, that machine fails loudly rather than guessing.

```bash
export OCEANICU_RUN_ROOT_BASE=/data/OceanICU/oceanicu_3d/experiments   # on the production machine
```

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

```bash
sbatch --export=RUN_ID='NSe/CMIP6/CNRM-ESM2-1/ssp126/run01',OCEANICU_RUN_DB='/path/to/run_registry.sqlite' run_chunk.slurm
```

Both variables are required on this first submission -- `run_chunk.slurm`
refuses to guess either, same as the Python layer, since there's no path
that's safe to assume on a machine whose folder layout isn't known in
advance. SLURM's `--export` replaces the job's entire environment with
just what's listed, so both have to be given here, not just exported in
the submitting shell; every self-resubmission after that (next chunk, or
next run) carries both forward automatically (`OCEANICU_RELAY_DIR` too,
if it was set -- see "Working across machines" below for when that
applies).

`run_chunk.slurm` locates chunk_runner.py/run_tracking.py at its own
runtime location, not a hardcoded path -- `sbatch path/to/run_chunk.slurm`
works from wherever it's actually deployed on that machine, whoever's
account that is.

That job runs the next chunk (chunk 0, since nothing's run yet -- no
`--load-restart`), then submits itself again for chunk 1, and so on,
until the run reaches its `stop_date`, gets paused, or a chunk fails (in
which case it stops resubmitting and leaves the failure for you to look
at -- it never blindly retries).

Every resubmission (next chunk, or next run -- see below) is a genuinely
fresh `sbatch` call: new SLURM job ID, fresh walltime, goes through the
normal scheduler queue like any other job. It is not a job array and not
an in-place continuation of the current allocation, so there can be a
real wait between one job finishing and the next one starting if the
cluster is busy.

**Pause the hand-off between jobs for a while, live, on a system that's
already running** -- e.g. "the HPC needs to be used for something else
for a while" -- with:

```bash
python oceanicu_runs.py delay-all --seconds 3600   # wait 1h before the next submission
python oceanicu_runs.py delay-all --clear          # cancel early
```

Unlike pause/resume (a separate, existing mechanism -- see below -- which
stops resubmission indefinitely until a human runs `resume`), this is a
TIMED pause: the next self-resubmission (next chunk of the same run, or
the next queued run -- never while a chunk is actually executing) waits
out the remainder then proceeds automatically, no manual resume needed.
It's genuinely live-adjustable, not just settable-once-at-launch: run
`delay-all --seconds N` again with a new value at any time, including
while a job is already mid-wait because of an earlier call -- the wait is
polled, not one fixed sleep, so a shortened, extended, or cleared delay
takes effect within the poll interval (60s), not only on the next
hand-off. Mechanically this is a `DELAY_ALL` file next to the registry
DB, mirroring the `PAUSE_ALL` sentinel's own convention (see
`run_tracking.chunk_delay_sentinel_path` for the raw file if this tool
itself is unreachable) -- content is the delay in seconds, its own mtime
marks when it was set.

(The no-SLURM `test_run_tracking/run_chunk_local.py` stand-in honors the
same sentinel, for the same reason, if you're testing this locally
first.)

## The queue

Once a run reaches its `stop_date` cleanly (`complete` or
`complete_with_warnings`), its job chain doesn't just stop -- it looks up
the highest-priority `not_started`, unpaused run in the registry and
`sbatch`s *that* one next, same mechanism as chunk-to-chunk. So in
practice you only need to manually `sbatch` once (or once per run you
want started concurrently); everything after that is picked up
automatically as allocations free up. A run that **fails** does *not*
auto-advance to the next queued one -- that stays a deliberate stop, so a
failure doesn't silently vanish under a pile of unrelated runs.

Priority controls this queue order (higher first, `run_id` alphabetical
as a tiebreak) and can be changed at any time, including for a run
that's already going:

```bash
python oceanicu_runs.py set-priority --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --priority 10
```

It only affects which `not_started` run gets picked up next -- it has no
effect on a run that's already `in_progress`.

## Check status

```bash
python oceanicu_runs.py list                          # everything
python oceanicu_runs.py list --status in_progress
python oceanicu_runs.py list --like CNRM-ESM2-1        # substring match on run_id
python oceanicu_runs.py show --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01   # + full chunk history
```

`status` on a run is one of:

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
python oceanicu_runs.py pause  --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01
python oceanicu_runs.py resume --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01
python oceanicu_runs.py pause  --all      # everything
python oceanicu_runs.py resume --all

# the emergency way -- works even if the Python env is unreachable, no
# tooling required, just `touch`. PAUSE_ALL lives next to whichever DB
# file is actually configured (wherever that is on this machine) -- not a
# fixed path, find it with:
python -c "import run_tracking as rt
with rt.connect() as conn: print(rt.pause_all_sentinel_path(conn))"

touch <run_root>/PAUSE          # this run only
touch <the path printed above>  # everything
rm    <the path printed above>  # resume everything
```

Use the `PAUSE_ALL` sentinel if the HPC is overloaded and you need
everything to stop cleanly without touching the database at all.

## Change chunk size mid-run

```bash
python oceanicu_runs.py chunk-size --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --chunk-multiplier 10
```

Only affects chunks that haven't run yet. Restart files are named by
actual date, not by chunk index, so a size change never conflicts with
what's already on disk.

## Pace one run's own chunks

Persistent, per-run setting -- wait N seconds before EACH future
resubmission of THIS run's own chunks (or before it's picked up as the
next queued run), unlike `delay-all` above (a global, one-shot TIMED
pause covering every run). Default 0 (no delay) if never set:

```bash
python oceanicu_runs.py add ... --chunk-delay-seconds 30   # at registration time
python oceanicu_runs.py set-chunk-delay --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --seconds 30
python oceanicu_runs.py set-chunk-delay --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --seconds 0  # cancel
```

Same "takes effect on the very next hand-off, never retroactively"
behaviour as `chunk-size`/`set-stop-date` above -- read fresh from the DB
each time, not cached anywhere. Shown in both `list` and `show`.

## Change data-roots-file or np mid-run

Same reason `run-root` can be relative (see "Set up a run" above): the
machine that added a run doesn't always know the right `data-roots-file`
for wherever it actually ends up running, and `np` sometimes turns out
wrong for the real target machine's node layout -- both can be changed
after the fact, same next-hand-off-only semantics as `chunk-size`/
`set-stop-date`/`set-chunk-delay`, never affecting a chunk already
running:

```bash
python oceanicu_runs.py set-data-roots-file --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --path bb-server1_data_roots.yaml
python oceanicu_runs.py set-np --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --np 192
```

Both shown in `show`'s run table.

## Where a chunk was actually submitted from

Each row in `chunks` records `submitted_host` -- the hostname of
whichever machine actually issued that chunk's submission (normally the
production machine's own `sbatch` self-resubmission, but `chunk_runner.py`
can be invoked by hand for testing too, so this records reality per
chunk rather than assuming). Shown in `show`'s chunks table. Distinct
from `history`'s `user` column (which machine vs. which account) and
from `run_root`'s own machine-dependence (where a run's files live vs.
where a given chunk was submitted from -- normally the same machine, but
not guaranteed to be, e.g. if someone runs `chunk_runner.py` by hand from
a login node different from wherever `sbatch` jobs usually land).

## Run only partway, or change the target mid-run

Some experiments only need to run to 2050, not 2100 -- that's just
`--stop-date 2050-12-31` at `add` time, nothing special.

Changing the target while a run is already going (e.g. it's currently at
2035 and you decide to extend to 2050, or cut it short) works too:

```bash
python oceanicu_runs.py set-stop-date --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 \
    --stop-date 2050-12-31
```

Nothing about a run's dates is cached anywhere -- every chunk and every
status check reads `stop_date` fresh from the registry -- so this takes
effect on the very next chunk, no other bookkeeping needed. Shrinking
`stop_date` below a date already reached just marks the run `complete`
towards its (revised) goal; it never deletes or rolls back chunks already
run past the new date.

## Rerun

```bash
python oceanicu_runs.py rerun --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --from-current
python oceanicu_runs.py rerun --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --from-chunk 4
python oceanicu_runs.py rerun --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01 --from-scratch
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
next run starts at `initial_date` with no restart.

**A rerun after manually editing the driver script (a real, expected
workflow -- a chunk blows up, you fix a bug in the script, rerun) shows up
in the history log automatically**, not just as a "rerun happened" line:
every chunk records a content hash (sha256) of the script/config it
actually ran with, and the next chunk that starts compares its own hash
against the previous one, logging a `script_changed`/`config_changed`
history entry if they differ. When the chunk being redone is the run's
*first* one (nothing earlier to compare against), the dropped chunk's own
hash is instead embedded directly in the `rerun` event's text, so the
before/after is still fully visible -- just read from one line instead of
two. Add `--note "why"` to `rerun` to record the reason alongside it:

```bash
python oceanicu_runs.py rerun --run-id ... --from-current --note "fixed off-by-one in river forcing"
```

`oceanicu_runs.py show --run-id ...` prints the full history (who did
what, when, including these events) as its own table, and the chunks
table shows each chunk's own script/config hash (first 12 hex chars).

## Remove a run from the registry

```bash
python oceanicu_runs.py remove --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run01
```

Only removes the registry/chunk-history rows -- never touches files.
Refuses if the run is `in_progress` unless you pass `--force` (pause it
first, normally).

## Dry-run any `oceanicu_runs.py` command

```bash
python oceanicu_runs.py --dry-run add --run-id ... [...]
python oceanicu_runs.py --dry-run chunk-size --run-id ... --chunk-multiplier 2
python oceanicu_runs.py --dry-run pause --all
```

`--dry-run` goes before the subcommand and works with **any** of them.
It is a real execution, not a simulated one -- it copies the configured
registry to a timestamped file in `/tmp`, runs the actual command against
*that copy only*, and reports:

- the exact before/after diff of every changed run row (and chunk count,
  if that changed too)
- what `run_chunk.slurm` would **actually submit next** for the run(s)
  involved -- real dates, real chunk directory, real `--load-restart`/
  `--save-restart` paths, the real launch command -- by really invoking
  `chunk_runner.py --dry-run` against that same scratch copy, not by
  re-deriving the logic separately where it could drift out of sync
- where the resulting scratch DB was left, so you can inspect it further
  yourself (`sqlite3 <path>`, or `oceanicu_runs.py --db <path> show ...`)

The real registry is never opened for writing. Unlike every other
command, `--dry-run` doesn't need `--db`/`OCEANICU_RUN_DB` configured at
all -- with neither set it just starts the scratch copy completely empty
instead of copying anything, so you can try things out with zero setup.

## Testing a script/config without the registry

`chunk_runner.py` has a standalone mode with **zero database
interaction** -- nothing looked up, nothing written -- for trying a
script/config/date-range by hand before it's registered:

```bash
python chunk_runner.py \
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

## Checking a standalone run's status

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
anywhere else, so keep track of it yourself). On a multi-rank run each
MPI rank writes its own `getm-NNNN.log` in that same directory; rank 0's
is the one with the overall run log.

## Multi-user note

The DB uses SQLite's WAL mode specifically so multiple SLURM jobs across
*different* runs can write status concurrently without corrupting
anything.

Two chunks of the *same* run are guarded against running concurrently --
`chunk_runner.py` refuses to start a new chunk while one is already
recorded `running` for that `run_id` (guards against an accidental
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
`run_tracking.py` keeps its own: every `OCEANICU_DB_BACKUP_EVERY_N_WRITES`
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
cd /abs/path/to/run_registry.sqlite.backups
git log --oneline                      # find the snapshot you want
git show <sha>:run_registry.sqlite > /abs/path/to/run_registry.sqlite
```

The backup repo has no automatic pruning -- for a DB this small, unlimited
history is cheap; squash/prune by hand later if it ever matters.

## Working across machines with no direct network path

Runs are often ADDED from one machine (wherever you're planning from) and
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
  queue" further down instead -- commands and new-run files cross the
  boundary as plain files via whatever transport actually exists
  (`rsync`, a human carrying a file), not a live connection.

The rest of this section is about the relay case:

**One-time setup on the relay:** copy `run_tracking.py` and
`run_tracking_server.py` there together, in the same directory (nothing
else needed -- no packaging, same as everywhere else in this system).

**Then, from any machine that can SSH to the relay** (add-machine or
production machine alike), point at the registry with an `ssh://` DB path
instead of a local one:

```bash
export OCEANICU_RUN_DB=ssh://oceanicu-relay/abs/path/to/run_registry.sqlite
export OCEANICU_RELAY_DIR=/abs/path/to/scripts   # where run_tracking_server.py lives, ON the relay
python oceanicu_runs.py add --run-id ...          # exactly the same as local use from here on
```

A copy-pasteable starting point for both lines lives in
`relay.env.example` -- copy it to `relay.env` (gitignored) and source it,
same content on every machine. It also sets `PATH` to include
`scripts/bin`, which gives you `oceanicu-runs`/`chunk-runner` as short
commands usable from anywhere (e.g. from inside a run's own chunk
directory) instead of the full `python .../scripts/oceanicu_runs.py`
every time -- these are thin wrappers around the real scripts, safe to
use in place of `python oceanicu_runs.py`/`python chunk_runner.py`
throughout this whole document.

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

With that in place, `OCEANICU_RUN_DB`/`OCEANICU_RELAY_DIR` (and
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
sbatch --export=RUN_ID='...',OCEANICU_RUN_DB='ssh://...',OCEANICU_RELAY_DIR='...' run_chunk.slurm
```

**One real limitation:** the per-run `<run_root>/PAUSE` sentinel file
(see "Pause / resume" above) always lives on whichever machine actually
has that run's output -- normally the production machine, never the
relay -- so it's checked locally by whichever machine is asking, not
relayed. Concretely: `oceanicu_runs.py --dry-run` run from the
add-machine can preview everything else correctly, but can't see a
per-run PAUSE file that only exists on the production machine's
filesystem (it'll just read as absent). The DB `control` column and the
`PAUSE_ALL` sentinel (which lives next to the DB, i.e. on the relay) both
work correctly from anywhere, including in that preview.

Same category, second instance: a **relative `run_root`** resolves
against `OCEANICU_RUN_ROOT_BASE` (see above), which is also set
per-machine and normally only on the production machine. The
`--dry-run` "what run_chunk.slurm would do next" preview runs
`chunk_runner.py --dry-run` for real, right there on the add-machine, so
it needs a real resolved path -- if that machine has no
`OCEANICU_RUN_ROOT_BASE` of its own, that one preview step can't render
and says so explicitly instead of showing a raw error. Not a sign of a
real problem: the actual `add` still registers correctly either way, and
resolution happens for real once the chunk actually runs on the
production machine, which does have its own `OCEANICU_RUN_ROOT_BASE` set.

## Command queue: registering runs with no network path to the registry

The relay above (`ssh://` + `run_tracking_server.py`) needs a live,
two-way network path -- it doesn't work when the registry's own machine
can't be reached from outside AT ALL (a fully network-isolated HPC,
reachable only via a human with terminal access to it, that in turn only
ever talks to ONE other machine -- e.g. this project's own PML HPC,
which only ever interacts with bb-server1, never GitHub directly). For
that case, use a command queue instead: a plain directory of small files
that carries *requests* (and the tiny driver-script/config files a new
run needs) across the boundary via `rsync`, at every hop.

**This is deliberately NOT part of the `oceanicu_3d` git repo.**
`hpc_commands/` is a plain data directory -- same category as
`experiments/` itself, not source -- living at a fixed, agreed location
that every machine in the chain can reach one hop of:
`bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands/`. The
HPC never needs to `git pull`/clone anything from GitHub to run --
`oceanicu_3d`'s `scripts/` are deployed there as plain files (however
that already happens), and `hpc_commands/` moves the exact same way, via
`rsync`, never git. Keeping it out of git also sidesteps the earlier
worry about it looking like a second, git-tracked copy of the real
(large, definitely-not-in-git) `experiments/` output tree -- it isn't
one; it's a small, disposable relay directory, not a repo.

**On your own workstation**, compose commands and stage files into a
local staging directory (anywhere -- outside the git repo):

```bash
python oceanicu_runs.py --queue ~/hpc_commands/queue_kb.yaml add \
    --run-id NSe/CMIP6/CNRM-ESM2-1/ssp126/run02 --run-root NSe/CMIP6/CNRM-ESM2-1/ssp126/run02 \
    --script generated_nse_cmip6.py --config generated_nse_cmip6_config.yaml \
    --initial-date 2015-01-01 --stop-date 2099-12-31 --chunk-kind annual --chunk-multiplier 5 --np 192
```

No registry access needed at all -- this never touches a real DB. Any
write subcommand works this way (`add`, `set-stop-date`, `set-priority`,
`pause`, `rerun`, ...) -- `list`/`show`/`stage` don't touch a registry
and refuse to queue, since there's nothing to apply later. Each call
appends one entry to the YAML file, e.g.:

```yaml
commands:
  - id: cmd-20260829T072716Z-46d3
    action: add
    args: {run_id: ..., run_root: ..., script: ..., ...}
    queued_at: "2026-08-29T07:27:16+00:00"
    queued_by: kb
    status: pending          # pending -> applied | failed
    applied_at: null
    note: null                # command's own stdout on success, or the error on failure
```

**Multiple people can queue commands from different places** (e.g. you
and someone else, both able to `rsync` into bb-server1's
`experiments/hpc_commands/`). Rather than all writing to one shared
file, **each person gets their own**, `queue_<name>.yaml` -- avoids any
risk of one person's `rsync` clobbering another's in-flight edit to the
same file.

**A brand-new run also needs its actual driver script/config physically
present** at `run_root` before any chunk can start -- the queue entry
alone only carries the DB row. `stage` gets exactly the right files
there (never a whole directory verbatim -- a real generated-output
folder commonly has `__pycache__/`, logs, etc. alongside the 2-3 files a
run actually needs):

```bash
python oceanicu_runs.py stage --run-root NSe/CMIP6/CNRM-ESM2-1/ssp126/run02 \
    --source-dir /wherever/you/generated/the/driver/script \
    --run-files-dir ~/hpc_commands/run_files
    # --include defaults to *.py, *.yaml, *.yml; --exclude-dir defaults to __pycache__
```

This `rsync`s (filtered by `--include`/`--exclude-dir`, so it's real
rsync include/exclude syntax, not a reimplementation) into
`~/hpc_commands/run_files/NSe/CMIP6/CNRM-ESM2-1/ssp126/run02/` --
mirroring the run's own `run_root` path.

**Then `rsync` your whole local staging directory to bb-server1**
(every `queue_*.yaml` *and* `run_files/`):

```bash
rsync -a ~/hpc_commands/ bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands/
```

**From bb-server1 to the HPC and back** is the one remaining hop that
needs whatever transport actually exists there (a human -- PML for this
project -- running `rsync` by hand, or a cron job if outbound access
from that side makes one possible -- see the topology discussion this
was designed around). Same directory, same files, `rsync` both ways --
nothing about `hpc_commands/` itself changes depending on how that hop
is actually carried out.

**On the machine where the registry actually lives** (the HPC, using
its own *local* copy of `hpc_commands/` after the last hop above), run
`apply_commands.py` against local copies of both:

```bash
python apply_commands.py --db /local/path/run_registry.sqlite \
    --queue-dir /local/path/hpc_commands/
    # processes every queue_*.yaml found there, combined and applied in
    # queued_at order across all of them, regardless of whose file an
    # entry lives in. --run-files-dir defaults to <queue-dir>/run_files.
    # (--queue PATH still works too, for a single exact file.)
```

For each `pending` entry: an `add` first copies whatever's staged for
that `run_root` into the real (resolved) `run_root` -- a copy failure
marks the command `failed` and never registers a file-less run -- then
every action replays through `oceanicu_runs.py`'s own CLI (reconstructed
from the stored args as real `--flag` values), so applying a queued
command goes through identical validation to running it directly.
Already-`applied`/`failed` entries are skipped, so re-running
`apply_commands.py` on a queue that hasn't changed is always a safe
no-op. The file is rewritten after *each* command, not just at the end,
so a crash partway through never loses already-applied statuses.

**`apply_commands.py` never calls `sbatch`, for any run, new or
resubmitting.** Submitting a job is always a deliberate manual action on
whoever's machine actually runs SLURM -- this only ever touches the
registry's `runs`/`history` rows. Once a run is registered (and its
files are in place), starting it is the same manual `sbatch
--export=RUN_ID=...,OCEANICU_RUN_DB=... run_chunk.slurm` as always;
after that, self-resubmission for future chunks is unaffected and
automatic as already documented above.

`apply_commands.py` refuses an `ssh://` `--db` outright -- it only ever
makes sense run locally, on the machine that actually holds the
registry.

**Known gap, not introduced by this mechanism:** `set-priority`/
`set-chunk-delay`/`set-stop-date`/etc. silently succeed even for a
nonexistent `run_id` (the underlying `UPDATE ... WHERE run_id = ?` just
matches zero rows, no error) -- a typo'd `run_id` in a queued command
will show as `applied`, not `failed`. Worth knowing when composing
queue entries; not yet fixed.

## What's not built yet

- **No folder scanning.** `add`/`remove` are the only way runs enter or
  leave the registry -- nothing walks the experiments tree looking for
  new `<model>/<scenario>/<run-name>` folders to register automatically.
- A **web status page** is planned as part of `ocean-post` -- not built
  here; this registry is the data source for it whenever that's ready.
- No `--output-dir` flag on the driver scripts themselves (that would
  need a change in `pygetm-config`'s codegen, deliberately left alone) --
  `chunk_runner.py` gets the same effect today by running the driver with
  its working directory set to the chunk folder.
