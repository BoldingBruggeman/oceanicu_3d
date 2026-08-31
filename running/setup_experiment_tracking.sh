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
        echo "get_commands_and_update_registry.py), rsync'd in from bb-server1 -- never"
        echo "git/GitHub."
        echo
        echo "Optional env var, not set above (default 5 is usually fine):"
        echo "  OCEANICU_DB_BACKUP_EVERY_N_WRITES -- how many writes between automatic"
        echo "  git-backup snapshots of the registry (EXPERIMENT_TRACKING.md 'Accidental-"
        echo "  deletion protection'); set to 0 to disable."
        echo
        echo "Optional cron, on the LOGIN NODE specifically (it needs outbound reach"
        echo "for --pull-from; compute nodes don't have it) -- pulls whatever's new in"
        echo "bb-server1's hpc_commands/ in, then applies whatever's pending. NEVER"
        echo "calls sbatch, for any experiment, new or resubmitting:"
        echo "  crontab -e   # on the login node -- then add a line like:"
        echo "  0 * * * * cd $scripts_dir && OCEANICU_EXPERIMENT_DB=$path/submission_registry.sqlite OCEANICU_EXPERIMENT_ROOT_BASE=$path OCEANICU_HPC_COMMANDS_DIR=$queue_dir python3 get_commands_and_update_registry.py --pull-from bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands >> $queue_dir/get_commands_and_update_registry.log 2>&1"
        echo "  cron does NOT source ~/.bashrc -- all three env vars are set inline on"
        echo "  the line itself, not relied on from the exports above. Replace"
        echo "  $scripts_dir if it was left as a placeholder. Omit --pull-from entirely"
        echo "  if hpc_commands/ is kept in sync some other way (e.g. a human running"
        echo "  rsync by hand) -- it then behaves exactly as the old apply_commands.py"
        echo "  always did, applying only what's already local."
        echo
        echo "Optional SECOND cron, on the LOGIN NODE specifically (it needs outbound"
        echo "reach; compute nodes don't have it) -- pushes the registry out to"
        echo "bb-server1 on a schedule, regardless of whether run_chunk.slurm's own"
        echo "best-effort per-chunk push (see OCEANICU_LOGIN_NODE above) ever works:"
        echo "  crontab -e   # on the login node -- then add a line like:"
        echo "  */10 * * * * OCEANICU_EXPERIMENT_DB=$path/submission_registry.sqlite $scripts_dir/bin/push_registry_snapshot.sh"
        echo
        echo "Optional THIRD cron, on the LOGIN NODE specifically -- pulls freshly"
        echo "staged experiment files (driver script/config, see 'oceanicu-experiments"
        echo "stage') in from bb-server1, filtered so real chunk output is never swept"
        echo "up (see bin/pull_experiment_files.sh's own header). This is what makes"
        echo "the presence-check on a queued 'add' actually reliable here:"
        echo "  crontab -e   # on the login node -- then add a line like:"
        echo "  */15 * * * * OCEANICU_EXPERIMENT_ROOT_BASE=$path $scripts_dir/bin/pull_experiment_files.sh bb-server1:/data/OceanICU/oceanicu_3d/experiments"
        echo
        echo "Optional FOURTH cron, on the LOGIN NODE specifically -- watchdog for"
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
        ;;
    relay)
        # Same two independent paths as hpc, same reasoning: hpc_commands/
        # is small/transient (queue_*.yaml only), the experiment tree holds
        # staged driver/config files and has no required relationship to it.
        path="${2:?usage: $0 relay EXPERIMENT_ROOT_PATH HPC_COMMANDS_PATH}"
        queue_dir="${3:?usage: $0 relay EXPERIMENT_ROOT_PATH HPC_COMMANDS_PATH}"
        mkdir -p "$path" "$queue_dir"
        echo "Relay ready: experiment tree at $path, hpc_commands/ queue at"
        echo "$queue_dir (independent paths -- no relationship to each other"
        echo "required, same as hpc). Staged experiment files (from workstations"
        echo "running 'oceanicu-experiments stage') land directly under $path,"
        echo "at their real relative path -- no separate setup needed here for that."
        echo "Make sure experiment_tracking.py + experiment_tracking_server.py are deployed here"
        echo "too, for the ssh:// relay (workstation <-> bb-server1)."
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
