# Code review: oceanicu_3d (branch `claude`, HEAD f05e0c82), 2026-11-12

Read-only review (Fable). Read in full: `driver/`, core of `running/` (experiment_tracking, oceanicu_experiments, chunk_runner, chunk_chain, run_chunk.slurm, get_commands_and_update_registry, check_inputs, health_check, reap_orphaned_chunks, run_chunks_local.py), `regenerate_hugo.py`, `regen_and_deploy.sh`. Only skimmed: analyze_logs.py, data_manifest.py, extended_dry_run_all_combos.py, nc_nan_scan.py, `bin/*.sh`, NSe bathymetry scripts.

Findings 1–3 reproduced; 1, 2, 3 and 6 re-checked against the code afterwards.

## P1: correctness bugs

1. **FABM restart seeding zero-fills layers outside the source depth range** — `driver/scripts/hydrography.py:203-243` (`_conservative_remap_column`). Docstring promises constant extrapolation; the code clamps the cumulative integral, so target layers outside the source range get 0 and partly covered layers are diluted. Reproduced: uniform 10, source at zt=-0.5 m, target at 0 → top layers `[0,0,0,10,10]`. Hits `fabm.ERSEM.restart_file` (nse_cmip6.yaml) whenever the restart was saved at negative elevation. Fix: clip target interfaces into the source range, divide by overlap thickness, copy nearest source layer where there is no overlap.
2. **chunk_runner exit 1 ("nothing to do") = Python's uncaught-exception status** — `running/chunk_runner.py:525-530`; consumers `run_chunk.slurm:300`, `chunk_chain.py:129`, `run_chunks_local.py:131`. Only RuntimeError is caught; any other exception exits 1 and the wrappers stop quietly (SLURM job exits 0, no FAIL mail). Reproduced with `set-stop-date --stop-date 2017-01-01T00:00:00` → ValueError, rc=1. Also locked DB, disk full, missing srun (after start_chunk → row stuck `running`). Possibly the recurring chunk-0 exit_code=1. Fix: catch Exception → traceback, return 2, finish_chunk as failed if inserted; or a dedicated code for "nothing to do". Validate dates at the CLI (`oceanicu_experiments.py:1271-1272, 1422, 1426`).
3. **chunk_runner --dry-run (tracked) mutates the experiment dir, then crashes** — `chunk_runner.py:374-400` vs `422-431`. Archive-aside, mkdir and copies run before the dry-run branch; `rmdir()` then fails on a non-empty dir. `oceanicu_experiments.py --dry-run rerun --from-current` goes through this (`_preview_chunk_runner`) and renames the real chunk dir to `.attempt-<stamp>`. Same rmdir in the IntegrityError path (452), after renaming the winner's live dir aside. Fix: return from dry-run before any filesystem action; archive/mkdir/copy only after start_chunk succeeds.
4. **`oceanicu_experiments.py --dry-run` performs real side effects** — `oceanicu_experiments.py:1526-1572`. Only submit-chunk is refused: `kill` runs scancel, `rerun --force`/`reset --force` scancel via rerun_from, `pull-code` runs git pull, `stage` rsyncs, `delay-all` writes a sentinel. Fix: refusal set + dry_run flag into cmd_kill/rerun_from.
5. **check_inputs.py has drifted from the hooks it mirrors** — GHG filenames (336-342 vs fabm.py:231-233, 281-283); ignores `fabm.ghg_scenario` fallback and fabm.py:202's error (336); N-dep variables `N3_flux` vs `N3_flux_ff` (360-361); EMORID historical file and nutrient climatology not checked, emorid folder/file mismatch (211-254 vs rivers.py:286-313); FABM IC check uses wrong keys and KeyErrors on constant/ZERO_GRADIENT tracers (408-413, uncaught at 790-793); `_expand_year_glob` copy lacks the January pre-year buffer (69-108). Fix: update mirrors, add a drift test.
6. **Default `dic_ta_historical_method` raises for CMEMS even without dissic/talk tracers** — `oceanicu_providers.py:670, 1470-1490`. Fix: resolve only when a tracer needs the bridge; per-source default. Also skip the bridge for CMEMS `climatology: true` (1397, 1575-1576).
7. **Pending `--restart-time` leaks to the wrong chunk** — `experiment_tracking.py:1353-1359, 1710-1714`, `chunk_runner.py:404-416`, `oceanicu_experiments.py:1054-1064`. Rerun from chunk 0 with a restart time is stored and applied to chunk 1. Fix: reject for chunk 0; start_chunk clears the column unconditionally.

## P2: stale/contradictory code and docs, dead code, duplication

8. `convert_ts` documented as called but commented out — `hydrography.py:111` vs `27-31`, `oceanicu_providers.py:134, 152` (disabled in a2a65018). Decide: restore (in_situ=True for WOA) or delete and fix docs.
9. NaN-monitor leftovers in `run_chunk.slurm:226-233, 251-258` (unused LOGFILE, 60 s pointer wait on early exit, `.current_chunk_dir` in chunk_runner.py:455-466); stale lines 18 and 202.
10. `health_check.py` uses `sstat --format=TotalCPU`, which its own docstring says is invalid on the cluster; off by default. Delete with the slurm watchdog block, or mark as stub.
11. ssh:// RemoteConn mode broken for get_chunk_delay_remaining, cmd_delay_all, next_experiment_to_start, rerun_from(force) scancel, IntegrityError via RPC. HPC uses the command queue; consider removing RemoteConn/_rpc_or_local/experiment_tracking_server.py (~300 lines + docs).
12. `_push_back_pull_status` skipped when nothing is pending — `get_commands_and_update_registry.py:433-435, 458-461` vs docstring. Call it before the early returns.
13. Calendar guessed by substring `"CMIP6" in script or config` — `chunk_runner.py:300`. Bias-corrected CMIP6 runs get noleap arithmetic. Read `runtime.calendar` from `*_config.yaml` or store it.
14. Three chunk-chaining loops (`chunk_chain.py`, `run_chunks_local.py:124-140`, unreferenced `run_chunks_local.sh`, plus bash in run_chunk.slurm). run_chunks_local.py → chunk_chain.chain(); delete the .sh.
15. Orphan/staleness test duplicated — `chunk_runner.py:270-298`, `reap_orphaned_chunks.py:61-79`. One shared helper. Rows without slurm_job_id are reaped after 4 days even if running.
16. Duplicated CMIP6 boundary splice — `oceanicu_providers.py:1109-1136` vs `1191-1218` (`_cmip6_bdy_folder` redefined; `setup=` and folder_template formatting inconsistent). One helper (not a hook file).
17. Eight near-identical `cmd_set_*`/`set_*` functions — `oceanicu_experiments.py:899-1001`, `experiment_tracking.py:797-888, 978-1044`; `main()` ~300 lines.
18. Stale docs/comments: oceanicu_driver.py:20, 112-113, 140-147, 366-367, 576-579; oceanicu_providers.py:11-24, 49-54, 306, 353, 795-803, 826; meteo.py:64, 99, 113-120; rivers.py:64, 229; fabm.py:77-81, 312-316; experiment_tracking.py:1644; chunk_runner.py:25; setup_experiment_tracking.sh:29; README.md:180-224; data_roots.yaml.example fallback.
19. Small dead code: meteo.py `_RAW_STORED_VARNAME`; fabm.py duplicate runtime_cfg (200, 334); oceanicu_driver.py:358; regenerate_hugo.py:240, 247; oceanicu_experiments.py:292, 1594 f-strings; experiment_tracking.py:553-556 duplicates resolve_db_path_arg.
20. One-off scripts in `running/` with no references: fix_monthly_river_time.py, diagnose_ersem_benthic_blowup.py, bench_collect_results.py, bench_concurrent_reads*, extended_dry_run_all_combos.py, test_compute_to_login_ssh.sbatch → `investigations/`.

## P3: nice to have

21. `oceanicu_driver.py:591` KeyError without `river_discharge`.
22. `rivers.py:340-343, 373-376` silently skips missing rivers; emorid folder/file used for positions but not data.
23. `hydrography.py:604-605` KeyError for constant/ZERO_GRADIENT tracer under WOA.
24. `ghg_scenario` help offers ssp245/ssp585 but N-dep dataset documents historical/ssp126/ssp370 (nse_cmems/nse_woa use ssp245 — check the data exists); `_noleap` only on GHG files.
25. `chunk_runner.py:75-80` monthly advance raises for day 29–31 starts.
26. `experiment_tracking.py:1752, 1288` string date comparisons rely on validated dates.
27. Dry-run scratch DBs create backup git repos in /tmp (`experiment_tracking.py:644-716`).
28. get_commands cron has no lock (flock -n); `_args_dict_to_cli` breaks for values starting with `-`.
29. `oceanicu_experiments.py:511` `/tmp/` prefix check bypassable with `/tmp/../`.
30. `chunk_chain.py:50-53` SIGINT claim wrong; "another chunk running" reported as paused.
31. hosts[hostname]-else-relay lookup implemented three times; wrapper ignores `--relay-host`.
32. `run_chunk.slurm:163-193, 240-243, 312-342` interpolates `$EXPERIMENT_ID` into python -c.

## Comment bloat

| File | Lines | `#` comments | Docstrings |
|---|---|---|---|
| driver/oceanicu_providers.py | 1587 | 702 | 36 |
| driver/oceanicu_driver.py | 791 | 326 | 31 |
| driver/scripts/fabm.py | 444 | 221 | 62 |
| driver/scripts/hydrography.py | 610 | 266 | 93 |
| driver/scripts/meteo.py | 547 | 103 | 212 |
| driver/scripts/rivers.py | 407 | 85 | 153 |
| running/run_chunk.slurm | 353 | 186 | – |
| regen_and_deploy.sh | 203 | 88 | – |
| running/experiment_tracking.py | 1768 | 133 | 512 |

Hook docstrings/comments are copied into every generated `*_utils.py`. Worst places: oceanicu_driver.py (33), oceanicu_providers.py incident histories and the 35-line `_fabm_tracers_param` help (34), the hook-file docstrings (35; keep one self-containment note per file), run_chunk.slurm / experiment_tracking / get_commands 127-line module docstring / regen_and_deploy.sh (36).

## Status of CODE_REVIEW_2026-10-24.md

| Earlier finding | Status |
|---|---|
| DB-path resolution in 6 files | Mostly fixed (411ffcf6); `connect()` still duplicates it |
| run_chunks_local.py /tmp restriction | Fixed |
| Only one tested file in running/ | Partly: test_reap_orphaned_chunks.py added; registry, CLI, chunk_runner, get_commands, check_inputs untested |
| NSe configs hardcode machine paths | Open (nse_bathymetry*, nse_bdy_create, nse_tidal_bdy, nse_delta_bdy, experiment_defaults.yaml:29) |
| Hardcoded /tmp | Open (oceanicu_experiments.py:200, 511, 1552; run_chunks_local.py:77) |

## Checked and found fine

pyflakes clean apart from 16/19; hook self-containment holds; choice-flattening consistent; meteo historical/scenario splice incl. 2015 seam; rivers EMORID splice; `_open_with_small_cache`; hydrography MPI slicing and NaN fill; experiment_tracking SQL parameterisation, migrations, RPC allow-list, rerun hash; get_commands merge/isolation/write-back; `_queue_command`; chunk_runner first-chunk realignment; regen_and_deploy.sh flock/relay/--apply; regenerate_hugo.py relay decision.
