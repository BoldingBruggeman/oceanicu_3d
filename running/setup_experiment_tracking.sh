#!/bin/bash
# setup_experiment_tracking.sh -- one-time env/directory setup for the OceanICU
# experiment-tracking system. Standalone: copy this ONE file anywhere (no git
# checkout needed -- this is exactly the file to hand to someone on the
# HPC, which never needs the oceanicu_3d repo at all) and run it there.
#
# Usage:
#   ./setup_experiment_tracking.sh hpc EXPERIMENT_ROOT_PATH HPC_COMMANDS_PATH [/path/to/scripts] [login-node-hostname]
#   ./setup_experiment_tracking.sh relay       /data/OceanICU/oceanicu_3d/experiments   # bb-server1
#   ./setup_experiment_tracking.sh workstation ~/hpc_commands
#
# hpc takes TWO separate paths now, not one -- they have different
# lifecycles (see the comment inside the hpc case below) and no required
# relationship to each other:
#   EXPERIMENT_ROOT_PATH -- the real experiment tree: registry DB, staged
#     driver/config files, and (once running) chunk output/logs/restarts.
#     Sets OCEANICU_EXPERIMENT_ROOT_BASE.
#   HPC_COMMANDS_PATH    -- the small, transient command-queue directory
#     (only ever queue_*.yaml files). Sets OCEANICU_HPC_COMMANDS_DIR. Can
#     live anywhere -- pick any local directory, does not need to nest
#     under or near EXPERIMENT_ROOT_PATH.
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
        add_to_bashrc "[ -z \"\${OCEANICU_EXPERIMENT_DB:-}\" ] && export OCEANICU_EXPERIMENT_DB=$path/experiment_registry.sqlite"
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
        echo "  0 * * * * cd $scripts_dir && OCEANICU_EXPERIMENT_DB=$path/experiment_registry.sqlite OCEANICU_EXPERIMENT_ROOT_BASE=$path OCEANICU_HPC_COMMANDS_DIR=$queue_dir python3 get_commands_and_update_registry.py --pull-from bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands >> $queue_dir/get_commands_and_update_registry.log 2>&1"
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
        echo "  */10 * * * * OCEANICU_EXPERIMENT_DB=$path/experiment_registry.sqlite $scripts_dir/bin/push_registry_snapshot.sh"
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
        echo "  0 * * * * OCEANICU_EXPERIMENT_DB=$path/experiment_registry.sqlite $scripts_dir/bin/restart_registry_watcher.sh"
        echo "  Needs inotify-tools (inotifywait) -- no root here to apt/yum install it,"
        echo "  but it's on conda-forge (confirmed available), so it can go into an"
        echo "  existing env with no root needed. That env's bin/ isn't on PATH in a"
        echo "  cron context though, so point at the exact binary instead:"
        echo "    OCEANICU_INOTIFYWAIT=/path/to/envs/pygetm/bin/inotifywait \\"
        echo "    OCEANICU_EXPERIMENT_DB=$path/experiment_registry.sqlite $scripts_dir/bin/restart_registry_watcher.sh"
        echo "  If it's missing entirely, the watchdog logs one clear message and gives"
        echo "  up gracefully (not a retry-forever loop) -- the cron above still covers you."
        ;;
    relay)
        path="${2:?usage: $0 relay PATH}"
        mkdir -p "$path/hpc_commands"
        echo "Relay ready: hpc_commands/ scaffold under $path (could live anywhere --"
        echo "this is just a default). Staged experiment files (from workstations"
        echo "running 'oceanicu-experiments stage') land directly under $path too,"
        echo "at their real relative path -- rsync'd there the same way hpc_commands/"
        echo "itself is, no separate setup needed on this machine for that."
        echo "Make sure experiment_tracking.py + experiment_tracking_server.py are deployed here"
        echo "too, for the ssh:// relay (workstation <-> bb-server1)."
        ;;
    workstation)
        path="${2:?usage: $0 workstation PATH}"
        mkdir -p "$path"
        echo "Workstation ready: local queue staging dir at $path."
        echo "Use:"
        echo "  oceanicu_experiments.py --queue $path/queue_\$USER.yaml <command>"
        echo
        echo "'stage' writes a new experiment's generated files directly into a real"
        echo "experiment tree -- a SEPARATE, independently-configured location, not"
        echo "nested under $path (hpc_commands/ only ever holds queue_*.yaml). Pick"
        echo "any local directory to mirror that tree (e.g. ~/experiments) and export:"
        echo "  export OCEANICU_EXPERIMENT_ROOT_BASE=~/experiments"
        echo "  oceanicu_experiments.py stage --experiment-root ... --source-dir ..."
        echo "Then rsync BOTH trees onward (same filter oceanicu-experiments stage"
        echo "itself uses for the experiment tree -- see bin/pull_experiment_files.sh):"
        echo "  rsync -a $path/ bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands/"
        echo "  rsync -a --include 'generated*.py' --include 'generated*.yaml' \\"
        echo "      --include '*/' --exclude '*' ~/experiments/ \\"
        echo "      bb-server1:/data/OceanICU/oceanicu_3d/experiments/"
        ;;
    *)
        echo "ERROR: role must be hpc, relay, or workstation" >&2
        exit 1
        ;;
esac

echo "Re-source ~/.bashrc (or open a new shell) for env vars to take effect."
