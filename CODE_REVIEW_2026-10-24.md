# Cross-repo code review -- 2026-10-24

Nine-repo review (one agent per repo): oceanicu_3d, pygetm-config, ocean-prep,
ocean-post, ocean-stack, ocean-run, ocean-plot, ocean-data, GETM/pygetm.
Scope: status, improvements/refactoring, and Windows portability.

## Per-repo findings

**oceanicu_3d** (branch `claude`, driver configs + HPC orchestration) --
Active, disciplined recent work. `running/` is ~4,800 lines of
SQLite-registry/SLURM orchestration with only one tested file
(`test_chunk_chain.py`); everything else -- the registry, the CLI,
chunk_runner, health_check -- is untested despite having broken in
production before (disk-full, OOM, orphan sentinels). DB-path resolution
is reimplemented independently in **6 different files**, and one of them
(`run_chunks_local.py`) has already drifted by adding a `/tmp`-only
restriction nowhere else enforced. A couple of NSe config files hardcode
machine-specific absolute paths instead of using the project's own
`data_roots.yaml` indirection.

**pygetm-config** (branch `devel`, schema -> driver-script codegen) --
Healthy: 175 passing tests, 0 failures, clean lint modulo cosmetics.
Architecture risk: `loader.py` and `codegen.py` are two independent
~2,000-line implementations of the same config semantics, kept in sync
only by comments saying "mirrors X exactly" -- no test actually runs a
config through both paths and diffs the result. The documented "script
hooks must be self-contained" rule has no static check enforcing it,
just two regression tests that happen to catch one failure mode.

**ocean-prep** (branch `claude`, boundary/forcing data prep) -- Clean,
well-tested (23 files, genuine offline regression tests against
synthetic fixtures, not network-dependent). Minor duplication: a CMIP6
local-file-finder is implemented twice (`cmip6_processor.py` vs
`tidal_boundary.py`) instead of shared. No retry/backoff on network
calls, though the ESGF path already has a 3-tier fallback.

**ocean-post** (branch `claude`, 946 commits, analysis/plotting/
reporting) -- Active and well-maintained, but its own `CODE_REVIEW.md`
is stale -- its 3 "critical" findings are already fixed, risking a
future reviewer re-fixing already-fixed code. The Theil-Sen
scenario-analysis migration (823 lines) has zero dedicated tests. The
tqdm/tty-gating pattern is duplicated **6 times inside this repo alone**.

**ocean-stack** (branch `claude`) -- No code at all: a shared conda
environment + docs repo tying the others together as sibling checkouts.
README and `cli-reference.md` have drifted on command counts (66 vs 67),
and there's no `.gitignore` despite ops artifacts routinely landing in
the repo root. The documented install path (sibling checkouts) isn't
even what's actually deployed on bb-server1 (which uses `/opt/ocean-src`
+ manual `pip install -e .`).

**ocean-run** (branch `claude`, 1 commit, new extraction) -- Honest
about its own incompleteness (README says so). Currently a copy-paste
relocation, not a generalization: `OCEANICU_*`-prefixed env vars and a
SLURM/MPI-only schema are still baked in, so a non-OceanICU consumer has
no real extension point yet. Only 12 tests, all on branching logic via
fakes -- the actual registry has zero coverage.

**ocean-plot** (branch `claude`, 30 commits) -- Clean and the most
Windows-safe of the bunch. One real bug: `--show` is silently a no-op
because `matplotlib.use('Agg')` is forced unconditionally while
`cli/ocean_plot.py` still calls `plt.show()`. `animations.py`
(2,894 lines) has zero dedicated tests and a mutable-default-argument
footgun (`cli_overrides: Dict = {}`).

**ocean-data** (branch `claude`) -- Much bigger than expected: a full
10-source data-loader library (~15k LOC), not just host config. Clean,
no committed secrets, good test coverage on the host/path-resolution
modules. Same doc-drift pattern as elsewhere (`host_config.md` chasing
the code across three feature additions).

**GETM/pygetm** (branch `devel`) -- The headline finding of the whole
review: **Windows is already a first-class, CI-gated platform here**,
not a hypothetical. `windows-latest` runs the full build + real MPI
parallel tests via MinGW-w64 gfortran + Ninja, sourced from conda-forge
-- deliberately avoiding an MSVC/Unix-make lock-in. Refactor-wise,
`Simulation.__init__` is a 514-line god-constructor, and
`open_boundaries.py` (1,295 lines, 14 classes) is a candidate for
splitting into a package.

## Cross-cutting patterns

1. The tqdm tty-gating fix is pasted independently in 9+ places across
   ocean-plot, ocean-post (x6), and ocean-prep. No shared low-level
   utils package exists in the ocean-* family to hold it.
2. Doc/code drift is a recurring, not isolated, failure mode:
   ocean-stack's command counts, ocean-data's `host_config.md`,
   ocean-post's stale `CODE_REVIEW.md`.
3. Test coverage is systematically thin on orchestration/registry code
   specifically (oceanicu_3d's `running/`, ocean-run's registry,
   ocean-post's scenario_analysis) while the data-processing libraries
   (ocean-prep, pygetm-config, GETM/pygetm) are comparatively
   well-tested. The riskiest, most-production-incident-prone code is
   the least covered.
4. DB-path/env-var resolution logic is duplicated 6x in oceanicu_3d and
   hasn't been generalized at all in ocean-run -- same underlying
   config-resolution problem in both repos.

## Windows verdict

- **GETM/pygetm (the physics core) already builds and runs real MPI
  tests on Windows in CI.** Not a blocker at all.
- pygetm-config, ocean-prep, ocean-plot, ocean-post, ocean-data all look
  Windows-clean in their own code (pathlib throughout, no POSIX-only
  syscalls, no `shell=True`, no symlinks).
- The real, structural blockers are two, and both are already
  known/scoped, not bugs to fix:
  - `oceanicu_3d/running/` and `ocean-run` are irreducibly
    SLURM/ssh/rsync-shaped by design (sstat/squeue/scancel/sbatch,
    bash-sourcing relay commands) -- correctly scoped to Linux cluster
    nodes.
  - `xesmf`/`esmpy` (regridding in the prep pipeline) has no real
    Windows conda-forge build -- already documented in ocean-stack with
    a scipy.interpolate fallback.
- Small fixable items (not actual blockers): hardcoded `/tmp` instead of
  `tempfile.gettempdir()` in oceanicu_3d; ocean-stack's `rebuild-env.sh`
  missing a `.bat` counterpart.

## My suggestions, prioritized

### Cheap, high-value, do first
1. **Delete or archive `ocean-post/CODE_REVIEW.md`.** It's actively
   misleading right now -- its "critical" findings are already fixed,
   so a future reviewer could waste time re-fixing fixed code.
2. **Fix ocean-plot's `--show` no-op.** `matplotlib.use('Agg')` is
   forced unconditionally while `cli/ocean_plot.py` still calls
   `plt.show()` -- a real, user-visible bug (the flag does nothing),
   cheap to fix (gate the `Agg` call on whether `--show`/an interactive
   backend was requested).
3. **Fix ocean-plot's mutable default argument** (`cli_overrides: Dict
   = {}`) -- classic footgun, one-line fix (`None` + `or {}`).
4. **Add a `.gitignore` to ocean-stack**, and reconcile the 66-vs-67
   command-count drift between its README and `cli-reference.md`.
5. **Update ocean-stack's install docs to match reality** -- document
   bb-server1's actual `/opt/ocean-src` + `pip install -e .` deployment
   instead of (or alongside) the sibling-checkout method nobody
   actually uses.

### Medium effort, real payoff
6. **Centralize DB-path/env-var resolution in oceanicu_3d.** It's
   reimplemented in 6 files with at least one already-drifted variant
   (`run_chunks_local.py`'s unenforced `/tmp`-only restriction). One
   shared resolver would remove a recurring source of silent
   inconsistency -- and `ocean-run`'s registry has the same unsolved
   problem, so this is worth designing once for both.
7. **Give the tqdm/tty-gating pattern one shared home** instead of 9+
   independent pastes across ocean-plot/ocean-post/ocean-prep -- a
   small `ocean-common` module, or just living inside `ocean-data`
   (the one repo in the family that's already a shared library, not
   app-specific).
8. **Deduplicate ocean-prep's CMIP6 local-file-finder** (currently
   separately implemented in `cmip6_processor.py` and
   `tidal_boundary.py`).
9. **Add one cross-path regression test in pygetm-config** that runs
   the same config through both `loader.py` and `codegen.py` and diffs
   the result. Right now their only sync mechanism is a comment saying
   "mirrors X exactly" -- a single automated diff test would catch the
   next silent divergence instead of relying on someone noticing.

### Bigger investment, risk-driven -- the orchestration/registry test gap
10. **Test oceanicu_3d's `running/` beyond `test_chunk_chain.py`.** This
    is the code that has actually broken in production (disk-full, OOM,
    orphan sentinels) and it's the least-tested code in the review.
    Start with the registry and `chunk_runner`/`health_check`'s failure
    paths, since those are what broke before.
11. **Add real tests for ocean-run's registry** (currently 0% covered --
    only branching logic via fakes is tested).
12. **Add dedicated tests for the two large, zero-coverage modules**:
    ocean-post's Theil-Sen scenario-analysis migration (823 lines) and
    ocean-plot's `animations.py` (2,894 lines).
13. **Generalize ocean-run past copy-paste** -- strip the
    `OCEANICU_*`-prefixed env vars and the SLURM/MPI-only assumption
    baked into its schema, so it has a real extension point for a
    non-OceanICU consumer, matching what its own README already admits
    is missing.

### Leave alone -- correctly scoped, not bugs
- `oceanicu_3d/running/` and `ocean-run`'s SLURM/ssh-shaped code: this
  is the HPC-orchestration layer, correctly Linux-only by design.
- `xesmf`/`esmpy`'s Windows gap: already documented with a working
  `scipy.interpolate` fallback.

### Lower priority, worth considering later
- A lightweight automated doc/code-consistency check somewhere in the
  family (even just a CI grep comparing a README's claimed command
  count against the actual CLI) -- the doc-drift pattern recurred in
  three unrelated repos, suggesting a process gap rather than three
  separate one-off mistakes.
