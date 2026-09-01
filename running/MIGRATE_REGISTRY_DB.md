# Migrating the registry DB off `scyllapfs`

**Status: planned, not yet executed.** Waiting on one decision (the new
DB path, see "Open questions" below) before running any of this for
real. Nothing here is time-sensitive in the sense of blocking other
work -- the [push-safety guard](bin/push_registry_snapshot.sh) already
stops the current empty-DB state from spreading further, so this can
happen whenever it's convenient.

## Why

`OCEANICU_EXPERIMENT_DB` on the HPC (scylla) currently resolves through
a symlink -- `/work/rict/run/oceanicu_3d/experiments/submission_registry.sqlite`
-- to `/scyllapfs/scratch/rict/run/oceanicu_3d/experiments/submission_registry.sqlite`.
`scyllapfs` is a parallel filesystem (Lustre/GPFS-style). SQLite's own
documentation explicitly warns it is not reliable there: POSIX advisory
locking, which its WAL/rollback-journal mechanism depends on for safe
concurrent access, is frequently broken or inconsistent on parallel
filesystems -- especially under access from *multiple nodes*, which is
exactly this system's pattern (the login node's cron writes to it, and
compute nodes' SLURM jobs write to it too, via `chunk_runner.py`).

Confirmed real incident, 2026-08-31: the live registry was found with 0
experiments (`SELECT COUNT(*) FROM experiments` → 0). All three copies on
disk at the time (`experiments/submission_registry.sqlite`,
`experiments/bkup/submission_registry.sqlite`,
`experiments/submission_registry.sqlite.backups/submission_registry.sqlite`)
were exactly 40960 bytes -- the size of a freshly-created *empty* schema,
not a corrupted/truncated real one. The routine push cron then copied
that empty state out to bb-server1 too, before the safety guard existed.

**The fix is architectural, not a workaround**: the registry DB (small,
needs reliable local-filesystem locking) should never have been nested
inside the experiment tree (large, wants fast parallel I/O for real
simulation output). Decoupling them mirrors the earlier
`hpc_commands/`-vs-`experiment_root` decoupling from this same session --
same principle, same reasoning.

**What does NOT move**: `OCEANICU_EXPERIMENT_ROOT_BASE`
(`/work/rict/run/oceanicu_3d/experiments`) stays exactly where it is.
Staged driver/config files and real chunk output (logs, restarts, `.nc`
results) genuinely belong on the fast parallel filesystem -- that's what
it's for. Only the small SQLite file (and its `.backups/` git repo)
moves.

## Open questions (answer before executing)

1. **New DB path.** Proposed default:
   `/users/modellers/rict/oceanicu_3d/registry/submission_registry.sqlite`
   (home directory -- presumably NFS-home or local disk, not the
   parallel scratch mount; a dedicated `registry/` folder, not mixed into
   the `running/` checkout itself). Confirm or override.
2. **What is `experiments/bkup/submission_registry.sqlite`?** A third
   copy found during diagnosis, not created by anything in this
   session's own tooling. Investigate before the migration -- if it's a
   manual backup someone made, it might be worth checking for real data
   before it's abandoned in place.

## Step 0 -- Recovery check (do this FIRST, before touching anything)

The `.backups/` directory is a git repo -- even though its current
working-tree file is empty, `git log` may still have history with real
data from before whatever emptied things.

```bash
cd /work/rict/run/oceanicu_3d/experiments/submission_registry.sqlite.backups
git log --oneline
```

If there's history: pick the last commit that looks like it has real
data (`git show <sha>:submission_registry.sqlite > /tmp/recovered.sqlite`,
then `sqlite3 /tmp/recovered.sqlite "select * from experiments;"` to
inspect) before deciding whether/how to restore it into the new
location. Don't skip this even if `NSe/WOA/run01` is easy to
re-register by hand -- better to know what was actually lost.

Also check `experiments/bkup/submission_registry.sqlite` (open question
2 above) the same way.

## Step 1 -- Set up the new location

```bash
mkdir -p /users/modellers/rict/oceanicu_3d/registry
```

If Step 0 recovered real data, restore it here now (either the raw
recovered file, or re-run the registrations by hand via
`oceanicu-experiments add`). If nothing was recoverable, a fresh
`connect()` against the new path will auto-create an empty schema on
first use -- nothing extra needed.

## Step 2 -- Update every place `OCEANICU_EXPERIMENT_DB` is set

**`.bashrc`** (the guarded export from `setup_experiment_tracking.sh
hpc`):
```bash
# change:
[ -z "${OCEANICU_EXPERIMENT_DB:-}" ] && export OCEANICU_EXPERIMENT_DB=/work/rict/run/oceanicu_3d/experiments/submission_registry.sqlite
# to:
[ -z "${OCEANICU_EXPERIMENT_DB:-}" ] && export OCEANICU_EXPERIMENT_DB=/users/modellers/rict/oceanicu_3d/registry/submission_registry.sqlite
```
(`OCEANICU_EXPERIMENT_ROOT_BASE` stays unchanged.)

**Crontab** (`crontab -e`) -- all three lines reference
`OCEANICU_EXPERIMENT_DB=/work/.../submission_registry.sqlite` inline;
replace with the new path in all three:
- FIRST cron (pull files + apply queued commands)
- SECOND cron (`push_registry_snapshot.sh`)
- THIRD cron (`restart_registry_watcher.sh` watchdog)

**Any manual `sbatch --export=...OCEANICU_EXPERIMENT_DB=...` invocation**
-- update to the new path too (this is the one place a stale path could
silently create ANOTHER fresh empty DB if missed, so double-check
carefully -- exactly the failure mode this whole migration exists to
prevent).

## Step 3 -- Verify before trusting it

```bash
echo $OCEANICU_EXPERIMENT_DB   # new path, in a FRESH shell (re-source ~/.bashrc first)
oceanicu-experiments list      # should show real data (recovered, or freshly re-registered)
crontab -l                     # confirm all three lines show the new path, no old ones left
```

Let at least one `*/15` and one `*/10` cron cycle pass, then check their
log files (`hpc_commands/get_commands_and_update_registry.log`,
`hpc_commands/push_registry_snapshot.log`) for a clean run against the
new path.

## Step 4 -- Clean up the old location

Once confirmed stable (give it a day or so of real cron cycles, not just
one manual check): remove the old `.sqlite`/`.backups/`/`.sqlite.backup_write_count`
files from `/work/rict/run/oceanicu_3d/experiments/` -- but only once
Step 0's recovery data (if any) has been safely copied out, and only the
DB-related files, never anything else under `experiments/` (staged files
and real chunk output stay exactly where they are).
