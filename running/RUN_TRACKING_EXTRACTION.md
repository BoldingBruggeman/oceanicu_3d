# Run-tracking extraction -- status and plan

This branch (`extract-run-tracking`, branched off `claude`) exists to
test a generic replacement for most of this `running/` directory, now
developed as its own package: **[ocean-run](https://github.com/bolding/ocean-run)**
(private repo, `claude` branch is its own working branch there too).

## Why

`running/`'s registry (`experiment_tracking.py`), management CLI
(`oceanicu_experiments.py`), and both self-chaining orchestrators
(`run_chunk.slurm` for SLURM, the new `chunk_chain.py` for plain
mpiexec/no-scheduler hosts) have no OceanICU-specific knowledge --
no area names, no scenario lists, no OceanICU paths baked in. They're a
generic "track and run chunked, long-running simulation experiments"
system that happens to live inside this project's repo. Pulling it out
into its own repo means it can be versioned, tested, and reused
independently of OceanICU's own science/deployment concerns.

## Current status (2026-10-10, updated)

**Step 1 and 2 of the plan below are now done.** `running/bin/{chunk-runner,
oceanicu-experiments,get-commands-and-update-registry,reap-orphaned-chunks,
run-chunks-local}` now forward to `ocean-run`'s installed console scripts
instead of this repo's local `.py` files (PATH-stripping trick so the
wrapper doesn't just call itself -- see any of those 5 files for the exact
mechanism). **The local `.py` scripts themselves are completely untouched**
(`git diff --stat running/` touches only the 5 `bin/*` wrapper files) --
both paths still exist side by side, exactly per the plan. `run_chunk.slurm`
and the remaining deployment-specific `bin/*` scripts
(`push_registry_snapshot.sh`, `sync_*_from_bbserver1.sh`, etc.) are
untouched, as planned.

**Comparison (step 2) result: IDENTICAL on every check run.** All done
against fully isolated scratch registries (never the live
`submission_registry.sqlite`, never this repo's own tracked
`test_experiment_tracking/` fixtures -- separate scratch trees entirely),
comparing the OLD path (`python3 running/*.py` directly) against the NEW
path (`ocean-run`'s installed console scripts, called both directly and
through the actual swapped `bin/*` wrappers):

- `oceanicu-experiments list` / `show` output: byte-identical (after
  normalizing the expected per-tree absolute paths and registration
  timestamps).
- Running one real chunk (`chunk_runner.py` vs `chunk-runner`) on a
  single-chunk experiment: same exit code (0), same resulting registry
  row (`status=complete`, same column values).
- The harder case -- `chunk_chain.py` vs `chunk-chain` self-chaining
  end to end: starts a single-chunk experiment, completes it, **picks up
  the next queued experiment automatically**, advances it through all 17
  of its 5-year chunks to its own stop date, then stops cleanly (no more
  queued). Identical log output and identical final registry state
  (`status=complete` for both experiments) on both sides.
- The actual swapped `bin/oceanicu-experiments` + `bin/chunk-runner`
  wrappers, exercised exactly as production would (PATH-based resolution,
  real `mpiexec -n 1` launch, not mocked): added an experiment, ran it to
  `complete`, confirmed via `oceanicu-experiments list` -- went through
  the real wrapper layer this branch now ships, not just a direct call to
  ocean-run's binaries.

`ocean-run` itself (the new repo) is further along:
- Built, `pip install -e .`'d, all 10 console scripts verified working
  (`oceanicu-experiments`, `chunk-runner`, `chunk-chain`,
  `reap-orphaned-chunks`, `experiment-tracking-server`,
  `get-commands-and-update-registry`, `analyze-logs`, `data-manifest`,
  `health-check`, `run-chunks-local`).
- Its own test suite (12 pytest tests, `chunk_chain.py`'s branching
  logic) passes.
- **Real end-to-end validation already done**, against a scratch
  registry (not this project's live one): registered 5 fake experiments
  via its `tests/integration_harness/` fixture set, ran a real chunk via
  `chunk-runner` to completion, then ran `chunk-chain` against a second
  experiment and watched it genuinely self-chain -- finish one
  experiment, pick up the next-highest-priority queued one
  automatically, advance it through several chunks. This is the exact
  behavior the comparison below needs to confirm, and it already works
  standalone.

See `ocean-run`'s own `README.md` "Status" section for the full file-by-
file breakdown of what moved there verbatim, what was deliberately left
behind (e.g. `check_inputs.py` -- looked generic, actually has real
AMM7-specific logic buried in one function; `run_chunk.slurm` itself,
deployment-specific and out of scope to touch), and open questions not
yet decided (env var naming, where pyGETM-aware code should live
long-term, final naming/family decision for the new repo).

## The plan from here

1. ✅ **Done.** Add `ocean-run` as an installable dependency and swap
   `running/`'s own thin wrapper layer (`bin/*`) to call its console
   scripts instead of the local copies -- without deleting anything
   local (both paths exist side by side).
2. ✅ **Done.** Run the exact same real-experiment workflows through both
   paths and confirm identical registry behaviour. Caveat: this was all
   against isolated scratch registries, not a *copy of the real* registry
   state -- this environment has no access to the live relay
   (`submission_registry.sqlite` lives behind `ssh://oceanicu-relay`, see
   `relay.env`), so the strongest comparison possible from here used the
   same fixture mechanism both repos' own test harnesses already use.
   Still pending: the same comparison against an actual *copy* of real
   production registry state, by whoever has relay access.
3. **Not yet done.** Only once that stronger (real-data) comparison is
   also clean: swap the lowest-risk pieces first (e.g.
   `reap-orphaned-chunks`, `run-chunks-local` -- already swapped above,
   but not yet exercised against anything beyond scratch/synthetic data),
   leave `chunk-runner`/`oceanicu-experiments` (what live chunks and
   `run_chunk.slurm` actually depend on) running through the swap in
   production last, watched closely.
4. **Not yet done.** `run_chunk.slurm`'s two invocation lines (`python
   chunk_runner.py ...` and its self-resubmission `sbatch ... "$SELF"`)
   are the last thing touched -- only after a full chunk-to-chunk cycle
   has run clean through `ocean-run` on real infrastructure (scylla), not
   just this environment's scratch-registry test.
5. **Not yet done.** Local copies in this repo's `running/` are deleted
   only after step 4, never before.

## Hard constraint

This system is handling real, actively-written production state (a
real 2026-08-31 incident is documented in this same directory's
`MIGRATE_REGISTRY_DB.md` -- a live registry briefly went empty via a
cron race before a safety guard existed). **Nothing in this extraction
may stop live runs or risk the live `submission_registry.sqlite`.** Every
step above is additive/comparative until explicitly proven equivalent.
