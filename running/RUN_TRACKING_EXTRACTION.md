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

## Current status (2026-10-10)

**Nothing in this branch is different from `claude` yet, except this
file.** `running/` here is still byte-identical to what's actually
driving live experiments. This is deliberate -- see the constraint
below.

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

## The plan from here (not yet done)

1. On this branch: add `ocean-run` as an installable dependency and
   swap `running/`'s own thin wrapper layer (`bin/*`) to call its
   console scripts instead of the local copies -- **without deleting
   anything local yet**, so both paths exist side by side.
2. Run the exact same real-experiment workflows through both paths and
   confirm identical registry behaviour (same CLI output, same chunk
   exit-code decisions, same registry writes) -- ideally against a
   *copy* of the real registry state, never the live
   `submission_registry.sqlite` itself.
3. Only once that comparison is clean: swap the lowest-risk pieces
   first (e.g. `reap-orphaned-chunks`, `run-chunks-local`), leave
   `chunk-runner`/`oceanicu-experiments` (what live chunks and
   `run_chunk.slurm` actually depend on) for last.
4. `run_chunk.slurm`'s two invocation lines (`python chunk_runner.py
   ...` and its self-resubmission `sbatch ... "$SELF"`) are the last
   thing touched -- only after a full chunk-to-chunk cycle has run
   clean through `ocean-run` on real infrastructure (scylla), not just
   this environment's scratch-registry test.
5. Local copies in this repo's `running/` are deleted only after step 4,
   never before.

## Hard constraint

This system is handling real, actively-written production state (a
real 2026-08-31 incident is documented in this same directory's
`MIGRATE_REGISTRY_DB.md` -- a live registry briefly went empty via a
cron race before a safety guard existed). **Nothing in this extraction
may stop live runs or risk the live `submission_registry.sqlite`.** Every
step above is additive/comparative until explicitly proven equivalent.
