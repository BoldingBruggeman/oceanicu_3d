#!/bin/bash
# setup_experiment_tracking.sh -- one-time env/directory setup for the OceanICU
# experiment-tracking system. Standalone: copy this ONE file anywhere (no git
# checkout needed -- this is exactly the file to hand to someone on the
# HPC, which never needs the oceanicu_3d repo at all) and run it there.
#
# Usage:
#   ./setup_experiment_tracking.sh hpc         EXPERIMENT_ROOT_PATH HPC_COMMANDS_PATH [/path/to/scripts] [login-node-hostname]
#   ./setup_experiment_tracking.sh relay       EXPERIMENT_ROOT_PATH HPC_COMMANDS_PATH   # bb-server1
#   ./setup_experiment_tracking.sh workstation EXPERIMENT_ROOT_PATH QUEUE_PATH
#
# ALL THREE roles take the same two independent paths, in the same order
# -- they have different lifecycles (see the comment inside the hpc case
# below) and no required relationship to each other, on any machine:
#   EXPERIMENT_ROOT_PATH -- the real experiment tree: registry DB (hpc/
#     relay only), staged driver/config files, and (once running) chunk
#     output/logs/restarts. Sets OCEANICU_EXPERIMENT_ROOT_BASE everywhere.
#   HPC_COMMANDS_PATH / QUEUE_PATH -- the small, transient command-queue
#     directory (only ever queue_*.yaml files; workstation's copy holds
#     just its own queue file(s) before they're rsync'd onward, hence the
#     different name for the same role). Sets OCEANICU_HPC_COMMANDS_DIR
#     on hpc/relay. Can live anywhere -- pick any local directory, does
#     not need to nest under or near EXPERIMENT_ROOT_PATH. If you stage
#     files AND queue a command from a workstation, you end up with two
#     genuinely separate local directories to rsync onward, into relay's
#     own two independently-configured paths -- never one folder with
#     the other nested inside it.
#
# hpc's 4th arg (optional) is where experiment_tracking.py/apply_commands.py
# etc. actually live on THIS machine -- only used to print ready-to-use
# cron lines; defaults to a placeholder if omitted.
#
# hpc's 5th arg (optional, shifted from 4th now that hpc takes two required
# paths) is the login node's own hostname -- if given,
# sets OCEANICU_LOGIN_NODE so run_chunk.slurm can try pushing a fresh
# registry snapshot out from a compute node right before each
# self-resubmission (best-effort; see push_registry_snapshot.sh and
# test_compute_to_login_ssh.sbatch). On THIS project's actual HPC, DO NOT
# pass this -- confirmed 2026-08-29 that compute nodes here can't ssh to
# their own login node at all, so it would just silently never fire. Use
# the login-node cron and/or watch_registry_and_push.sh's inotify
# watcher instead (see EXPERIMENT_TRACKING.md "Keeping bb-server1's copy
# of the registry up to date") -- the only real mechanisms here, both
# running entirely on the login node; this 5th arg only matters if this
# tooling is ever deployed to a different cluster where compute-to-login
# ssh actually works.
# Since $HOME is shared between login and compute nodes on this cluster,
# setting this once here makes it visible everywhere run_chunk.slurm's
# own `source ~/.bashrc` runs, no separate per-node setup needed.
#
# See EXPERIMENT_TRACKING.md ("Working across machines", "Command queue") for
# what each of these env vars/directories is actually for.
set -eu

role="${1:?usage: $0 hpc|relay|workstation ...}"

add_to_bashrc() {
    grep -qxF "$1" ~/.bashrc 2>/dev/null || echo "$1" >> ~/.bashrc
}

case "$role" in
    hpc)
        # Two independent paths -- different lifecycles, no required
        # relationship. hpc_commands/ (queue_dir) is small and transient,
        # meant to go back to empty once everything in it is applied;
        # ONLY ever contains queue_*.yaml. path (the experiment tree) is
        # where the registry DB, staged driver/config files, and (once
        # running) real chunk output/logs/restarts/results all live --
        # that output can get large, which is exactly why it must NOT be
        # nested inside the small thing that gets synced on its own
        # lightweight schedule.
        path="${2:?usage: $0 hpc EXPERIMENT_ROOT_PATH HPC_COMMANDS_PATH [scripts_dir] [login_node]}"
        queue_dir="${3:?usage: $0 hpc EXPERIMENT_ROOT_PATH HPC_COMMANDS_PATH [scripts_dir] [login_node]}"
        scripts_dir="${4:-<path-to-scripts-dir>}"
        login_node="${5:-}"
        mkdir -p "$queue_dir"
        # -z guarded, not a plain export: if this var is already set when
        # .bashrc runs (e.g. sbatch --export=OCEANICU_EXPERIMENT_DB=... on THIS
        # submission), that value wins -- these are only a fallback
        # default, never allowed to clobber an explicit one.
        add_to_bashrc "[ -z \"\${OCEANICU_EXPERIMENT_DB:-}\" ] && export OCEANICU_EXPERIMENT_DB=$path/submission_registry.sqlite"
        add_to_bashrc "[ -z \"\${OCEANICU_EXPERIMENT_ROOT_BASE:-}\" ] && export OCEANICU_EXPERIMENT_ROOT_BASE=$path"
        add_to_bashrc "[ -z \"\${OCEANICU_HPC_COMMANDS_DIR:-}\" ] && export OCEANICU_HPC_COMMANDS_DIR=$queue_dir"
        # Marks this machine, and only this machine, as the real HPC --
        # oceanicu_experiments.py's direct (non---queue) `add` refuses to run
        # anywhere this isn't set to "1" (see EXPERIMENT_TRACKING.md "Set up an
        # experiment"), since bb-server1's mirror sits at the exact same path
        # string and a path-based check alone can't tell them apart.
        add_to_bashrc "[ -z \"\${OCEANICU_HPC:-}\" ] && export OCEANICU_HPC=1"
        if [ -n "$login_node" ]; then
            add_to_bashrc "[ -z \"\${OCEANICU_LOGIN_NODE:-}\" ] && export OCEANICU_LOGIN_NODE=$login_node"
        fi
        echo "HPC ready: registry + experiment tree under $path, hpc_commands/ scaffold"
        echo "at $queue_dir (an independent path -- no relationship to $path required)."
        echo "Still needed here (not this script): running/ itself"
        echo "(experiment_tracking.py, oceanicu_experiments.py, chunk_runner.py, run_chunk.slurm,"
        echo "get_commands_and_update_registry.py) -- rsync'd in from bb-server1 if this"
        echo "machine can't reach GitHub, or a plain git clone/pull if it can (confirmed"
        echo "working on at least one real deployment -- don't assume the login node has"
        echo "no GitHub access without actually checking). git pull is the better option"
        echo "where available: no separate deploy step to forget, unlike a manually-copied"
        echo "checkout (see EXPERIMENT_TRACKING.md 'Working across machines' for a real"
        echo "case of exactly that going stale and breaking things)."
        echo
        echo "Every write gets its own snapshot immediately by default now (see"
        echo "experiment_tracking.py's own _DEFAULT_BACKUP_EVERY_N_WRITES) -- what makes"
        echo "the watcher/push cron below actually near-real-time end to end, rather"
        echo "than silently batching multiple writes before there's even a local"
        echo "snapshot to push. Override with OCEANICU_DB_BACKUP_EVERY_N_WRITES if write"
        echo "volume ever gets high enough for batching to matter (unlikely at this"
        echo "project's scale), or set it to 0 to disable snapshots entirely."
        echo
        echo "The \$path/\$queue_dir/\$scripts_dir showing up in the cron lines below are"
        echo "NOT left for cron to resolve -- they're THIS SCRIPT's own bash variables,"
        echo "already substituted with the real values you passed above, at the moment"
        echo "this script printed them. What you actually see below (and should paste"
        echo "into crontab -e as-is) is already a literal absolute path, e.g."
        echo "'cd /real/path && ...', never a literal '\$scripts_dir' string -- cron"
        echo "never needs to resolve anything, since there's no variable left by the"
        echo "time you see it. If a line below still literally says <path-to-scripts-dir>,"
        echo "that means the 4th argument (scripts_dir) was left off this script's own"
        echo "invocation -- re-run with it set, or hand-edit that one placeholder."
        echo
        echo "Optional FIRST cron, on the LOGIN NODE specifically (it needs outbound"
        echo "reach; compute nodes don't have it) -- pulls freshly staged experiment"
        echo "files in from bb-server1 FIRST, THEN (only if that succeeds) pulls"
        echo "whatever's new in bb-server1's hpc_commands/ and applies whatever's"
        echo "pending. NEVER calls sbatch, for any experiment, new or resubmitting."
        echo "These two steps are chained with && (not run as two separate cron"
        echo "lines) on purpose: both were previously independent crons at */15 and"
        echo "hourly respectively, which meant they landed on the SAME minute every"
        echo "hour (:00) with no guaranteed ordering between them -- a real race, not"
        echo "theoretical, since the presence-check on a queued 'add' depends on the"
        echo "file-pull having already happened. Chaining makes the dependency"
        echo "explicit and deterministic: if the file-pull fails, the apply step"
        echo "correctly never runs at all, rather than maybe-racing it."
        echo "  crontab -e   # on the login node -- then add a line like:"
        echo "  */15 * * * * export OCEANICU_EXPERIMENT_DB=$path/submission_registry.sqlite; export OCEANICU_EXPERIMENT_ROOT_BASE=$path; export OCEANICU_HPC_COMMANDS_DIR=$queue_dir; export OCEANICU_HPC=1; ( $scripts_dir/bin/pull_experiment_files.sh bb-server1:/data/OceanICU/oceanicu_3d/experiments && $scripts_dir/bin/get-commands-and-update-registry --pull-from bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands ) >> $queue_dir/get_commands_and_update_registry.log 2>&1"
        echo "  cron does NOT source ~/.bashrc -- all four env vars are set inline on"
        echo "  the line itself (as separate export statements, since a bare"
        echo "  VAR=val prefix only applies to the single command right after it,"
        echo "  not to anything later in the same && chain), not relied on from the"
        echo "  exports above. OCEANICU_HPC=1 specifically is easy to miss if you"
        echo "  hand-edit this line later -- without it, get-commands-and-update-"
        echo "  registry's replayed 'add' commands fail with 'this doesn't look like"
        echo "  the HPC', even though an interactive shell on this same machine (which"
        echo "  DOES source ~/.bashrc) shows OCEANICU_HPC=1 just fine -- confirmed"
        echo "  hitting exactly this in practice. Replace $scripts_dir if it was left"
        echo "  as a placeholder."
        echo "  The (...) group is what makes >> capture BOTH commands' combined"
        echo "  output in one log -- without it, only the second command's output"
        echo "  would be redirected. Omit the pull_experiment_files.sh half (and the"
        echo "  && before get-commands-and-update-registry) entirely if hpc_commands/"
        echo "  and the experiment tree are kept in sync some other way (e.g. a human"
        echo "  running rsync by hand)."
        echo
        echo "Optional SECOND cron, on the LOGIN NODE specifically (it needs outbound"
        echo "reach; compute nodes don't have it) -- pushes the registry out to"
        echo "bb-server1 on a schedule, regardless of whether run_chunk.slurm's own"
        echo "best-effort per-chunk push (see OCEANICU_LOGIN_NODE above) ever works:"
        echo "  crontab -e   # on the login node -- then add a line like:"
        echo "  */10 * * * * OCEANICU_EXPERIMENT_DB=$path/submission_registry.sqlite $scripts_dir/bin/push_registry_snapshot.sh >> $queue_dir/push_registry_snapshot.log 2>&1"
        echo "  Without this redirect, push_registry_snapshot.sh's own output --"
        echo "  including its \"nothing to push yet\" message when no snapshot exists --"
        echo "  goes wherever cron's default mail delivery sends it, which may not be"
        echo "  checked. The log makes every run's outcome (pushed / nothing new /"
        echo "  actual error) visible without relying on that."
        echo
        echo "Optional THIRD cron, on the LOGIN NODE specifically -- watchdog for"
        echo "watch_registry_and_push.sh's persistent inotify watcher (near-immediate"
        echo "pushes on top of the periodic push cron above, see its own header and"
        echo "EXPERIMENT_TRACKING.md 'Keeping bb-server1's copy up to date'):"
        echo "  crontab -e   # on the login node -- then add a line like:"
        echo "  0 * * * * OCEANICU_EXPERIMENT_DB=$path/submission_registry.sqlite $scripts_dir/bin/restart_registry_watcher.sh"
        echo "  Needs inotify-tools (inotifywait) -- no root here to apt/yum install it,"
        echo "  but it's on conda-forge (confirmed available), so it can go into an"
        echo "  existing env with no root needed. That env's bin/ isn't on PATH in a"
        echo "  cron context though, so point at the exact binary instead:"
        echo "    OCEANICU_INOTIFYWAIT=/path/to/envs/pygetm/bin/inotifywait \\"
        echo "    OCEANICU_EXPERIMENT_DB=$path/submission_registry.sqlite $scripts_dir/bin/restart_registry_watcher.sh"
        echo "  If it's missing entirely, the watchdog logs one clear message and gives"
        echo "  up gracefully (not a retry-forever loop) -- the cron above still covers you."
        echo
        echo "Optional FOURTH cron, on the LOGIN NODE specifically (needs squeue, same"
        echo "requirement as chunk_runner.py's own liveness check -- never on the relay)"
        echo "-- proactively reaps chunks left stuck at status=running by a job that died"
        echo "without chunk_runner.py itself getting the chance to record the failure"
        echo "(e.g. the whole job's cgroup OOM-killed at once, taking chunk_runner.py out"
        echo "along with the MPI ranks it was waiting on -- confirmed happening in"
        echo "production, 2026-09-01). Without this, that stale 'running' status just"
        echo "sits there indefinitely, with zero new history entries, until a human"
        echo "happens to notice and manually retry something -- the existing lock/orphan"
        echo "check in chunk_runner.py only ever runs the next time someone tries to"
        echo "start a NEW chunk for that same experiment, it's lazy, not proactive:"
        echo "  crontab -e   # on the login node -- then add a line like:"
        echo "  */30 * * * * OCEANICU_EXPERIMENT_DB=$path/submission_registry.sqlite $scripts_dir/bin/reap-orphaned-chunks >> $queue_dir/reap_orphaned_chunks.log 2>&1"
        echo "  Same conservative fallback as chunk_runner.py's own check: if squeue"
        echo "  can't confirm a job is dead (unavailable, or ambiguous), a chunk is only"
        echo "  ever reaped once its start_time is >4 days old -- never a false positive"
        echo "  from a merely-slow squeue or a brief network blip."
        ;;
    relay)
        # Same two independent paths as hpc, same reasoning: hpc_commands/
        # is small/transient (queue_*.yaml only), the experiment tree holds
        # staged driver/config files and has no required relationship to it.
        path="${2:?usage: $0 relay EXPERIMENT_ROOT_PATH HPC_COMMANDS_PATH}"
        queue_dir="${3:?usage: $0 relay EXPERIMENT_ROOT_PATH HPC_COMMANDS_PATH}"
        mkdir -p "$path" "$queue_dir"
        # Registry filename is submission_registry.sqlite, NOT
        # experiment_registry.sqlite -- this same directory may also host a
        # completely separate reporting/scenario-catalog DB (e.g.
        # ocean-post's cli.reporting) that's already claimed that other
        # name for itself. See EXPERIMENT_TRACKING.md "Working across
        # machines" for the full story (found the hard way once already).
        add_to_bashrc "[ -z \"\${OCEANICU_EXPERIMENT_DB:-}\" ] && export OCEANICU_EXPERIMENT_DB=$path/submission_registry.sqlite"
        add_to_bashrc "export PATH=\"\$HOME/source/repos/OceanICU/oceanicu_3d/running/bin:\$PATH\""
        echo "Relay ready: experiment tree at $path, hpc_commands/ queue at"
        echo "$queue_dir (independent paths -- no relationship to each other"
        echo "required, same as hpc). Staged experiment files (from workstations"
        echo "running 'oceanicu-experiments stage') land directly under $path,"
        echo "at their real relative path -- no separate setup needed here for that."
        echo "OCEANICU_EXPERIMENT_DB set to the LOCAL path above (this machine IS"
        echo "the relay, so it never needs its own ssh:// URL back to itself),"
        echo "running/bin added to PATH -- both for direct interactive use here."
        echo
        echo "For the ssh:// relay itself (workstation <-> bb-server1), point"
        echo "OCEANICU_RELAY_DIR (on EVERY machine that connects through here) at"
        echo "a git checkout of oceanicu_3d/running on THIS machine -- e.g.:"
        echo "  export OCEANICU_RELAY_DIR=$HOME/source/repos/OceanICU/oceanicu_3d/running"
        echo "Do NOT hand-deploy a separate copy of experiment_tracking.py/"
        echo "experiment_tracking_server.py elsewhere -- that copy has no way to"
        echo "ever pick up future fixes and WILL silently drift out of sync (this"
        echo "happened for real once already, see EXPERIMENT_TRACKING.md). A plain"
        echo "git pull here is the only thing that should ever need to happen."
        echo
        echo "Workstations should rsync onward into exactly these two paths --"
        echo "e.g. (adjust bb-server1/paths to match this machine's real hostname"
        echo "and whatever was actually passed above):"
        echo "  rsync -a <local-queue-dir>/ bb-server1:$queue_dir/"
        echo "  rsync -a --include 'generated*.py' --include 'generated*.yaml' \\"
        echo "      --include '*/' --exclude '*' <local-experiment-root>/ \\"
        echo "      bb-server1:$path/"
        ;;
    workstation)
        # Same two independent paths as hpc/relay: EXPERIMENT_ROOT_PATH for
        # 'stage' output (sets OCEANICU_EXPERIMENT_ROOT_BASE, persisted here
        # instead of leaving it to a manual per-shell export), QUEUE_PATH
        # for '--queue' files -- never nested inside each other, so staging
        # files AND queuing a command produces two genuinely separate local
        # directories to rsync onward, matching relay's own two paths.
        path="${2:?usage: $0 workstation EXPERIMENT_ROOT_PATH QUEUE_PATH}"
        queue_dir="${3:?usage: $0 workstation EXPERIMENT_ROOT_PATH QUEUE_PATH}"
        mkdir -p "$path" "$queue_dir"
        add_to_bashrc "[ -z \"\${OCEANICU_EXPERIMENT_ROOT_BASE:-}\" ] && export OCEANICU_EXPERIMENT_ROOT_BASE=$path"
        echo "Workstation ready: local experiment tree at $path (exported as"
        echo "OCEANICU_EXPERIMENT_ROOT_BASE), local queue staging dir at $queue_dir."
        echo "Use:"
        echo "  oceanicu_experiments.py --queue $queue_dir/queue_\$USER.yaml <command>"
        echo "  oceanicu_experiments.py stage --experiment-root ... --source-dir ..."
        echo
        echo "Copy BOTH pieces onward to relay (bb-server1) -- into ITS OWN two"
        echo "independently-configured paths (see setup_experiment_tracking.sh relay"
        echo "on that machine for what those actually are; the paths below are only"
        echo "this project's current convention, ask whoever set up relay if unsure):"
        echo "  rsync -a $queue_dir/ bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands/"
        echo "  rsync -a --include 'generated*.py' --include 'generated*.yaml' \\"
        echo "      --include '*/' --exclude '*' $path/ \\"
        echo "      bb-server1:/data/OceanICU/oceanicu_3d/experiments/"
        ;;
    *)
        echo "ERROR: role must be hpc, relay, or workstation" >&2
        exit 1
        ;;
esac

echo "Re-source ~/.bashrc (or open a new shell) for env vars to take effect."
