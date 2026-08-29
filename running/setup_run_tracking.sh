#!/bin/bash
# setup_run_tracking.sh -- one-time env/directory setup for the OceanICU
# run-tracking system. Standalone: copy this ONE file anywhere (no git
# checkout needed -- this is exactly the file to hand to someone on the
# HPC, which never needs the oceanicu_3d repo at all) and run it there.
#
# Usage:
#   ./setup_run_tracking.sh hpc         /data/OceanICU/oceanicu_3d/experiments  [/path/to/scripts] [login-node-hostname]
#   ./setup_run_tracking.sh relay       /data/OceanICU/oceanicu_3d/experiments   # bb-server1
#   ./setup_run_tracking.sh workstation ~/hpc_commands
#
# hpc's 3rd arg (optional) is where run_tracking.py/apply_commands.py
# etc. actually live on THIS machine -- only used to print ready-to-use
# cron lines; defaults to a placeholder if omitted.
#
# hpc's 4th arg (optional) is the login node's own hostname -- if given,
# sets OCEANICU_LOGIN_NODE so run_chunk.slurm can try pushing a fresh
# registry snapshot out from a compute node right before each
# self-resubmission (best-effort; see push_registry_snapshot.sh and
# test_compute_to_login_ssh.sbatch -- whether this actually works depends
# on cluster-specific ssh reachability, unconfirmed as of writing). Omit
# it to skip that and rely solely on the login-node cron fallback below.
# Since $HOME is shared between login and compute nodes on this cluster,
# setting this once here makes it visible everywhere run_chunk.slurm's
# own `source ~/.bashrc` runs, no separate per-node setup needed.
#
# See RUN_TRACKING.md ("Working across machines", "Command queue") for
# what each of these env vars/directories is actually for.
set -eu

role="${1:?usage: $0 hpc|relay|workstation PATH}"
path="${2:?usage: $0 hpc|relay|workstation PATH}"
scripts_dir="${3:-<path-to-scripts-dir>}"
login_node="${4:-}"

add_to_bashrc() {
    grep -qxF "$1" ~/.bashrc 2>/dev/null || echo "$1" >> ~/.bashrc
}

case "$role" in
    hpc)
        mkdir -p "$path/hpc_commands/run_files"
        # -z guarded, not a plain export: if this var is already set when
        # .bashrc runs (e.g. sbatch --export=OCEANICU_RUN_DB=... on THIS
        # submission), that value wins -- these are only a fallback
        # default, never allowed to clobber an explicit one.
        add_to_bashrc "[ -z \"\${OCEANICU_RUN_DB:-}\" ] && export OCEANICU_RUN_DB=$path/run_registry.sqlite"
        add_to_bashrc "[ -z \"\${OCEANICU_RUN_ROOT_BASE:-}\" ] && export OCEANICU_RUN_ROOT_BASE=$path"
        # Marks this machine, and only this machine, as the real HPC --
        # oceanicu_runs.py's direct (non---queue) `add` refuses to run
        # anywhere this isn't set to "1" (see RUN_TRACKING.md "Set up a
        # run"), since bb-server1's mirror sits at the exact same path
        # string and a path-based check alone can't tell them apart.
        add_to_bashrc "[ -z \"\${OCEANICU_HPC:-}\" ] && export OCEANICU_HPC=1"
        if [ -n "$login_node" ]; then
            add_to_bashrc "[ -z \"\${OCEANICU_LOGIN_NODE:-}\" ] && export OCEANICU_LOGIN_NODE=$login_node"
        fi
        echo "HPC ready: registry + hpc_commands/ under $path."
        echo "Still needed here (not this script): running/ itself"
        echo "(run_tracking.py, oceanicu_runs.py, chunk_runner.py, run_chunk.slurm,"
        echo "apply_commands.py), rsync'd in from bb-server1 -- never git/GitHub."
        echo
        echo "Optional env var, not set above (default 5 is usually fine):"
        echo "  OCEANICU_DB_BACKUP_EVERY_N_WRITES -- how many writes between automatic"
        echo "  git-backup snapshots of the registry (RUN_TRACKING.md 'Accidental-"
        echo "  deletion protection'); set to 0 to disable."
        echo
        echo "Optional cron -- applies whatever's already landed in hpc_commands/"
        echo "(does NOT do the rsync itself, and NEVER calls sbatch, ever):"
        echo "  crontab -e   # then add a line like:"
        echo "  */15 * * * * cd $scripts_dir && OCEANICU_RUN_DB=$path/run_registry.sqlite OCEANICU_RUN_ROOT_BASE=$path python3 apply_commands.py --db $path/run_registry.sqlite --queue-dir $path/hpc_commands >> $path/hpc_commands/apply_commands.log 2>&1"
        echo "  cron does NOT source ~/.bashrc -- both env vars are set inline on the"
        echo "  line itself, not relied on from the exports above. Replace"
        echo "  $scripts_dir if it was left as a placeholder."
        echo
        echo "Optional SECOND cron, on the LOGIN NODE specifically (it needs outbound"
        echo "reach; compute nodes don't have it) -- pushes the registry out to"
        echo "bb-server1 on a schedule, regardless of whether run_chunk.slurm's own"
        echo "best-effort per-chunk push (see OCEANICU_LOGIN_NODE above) ever works:"
        echo "  crontab -e   # on the login node -- then add a line like:"
        echo "  */10 * * * * OCEANICU_RUN_DB=$path/run_registry.sqlite $scripts_dir/push_registry_snapshot.sh"
        ;;
    relay)
        mkdir -p "$path/hpc_commands/run_files"
        echo "Relay ready: hpc_commands/ scaffold under $path."
        echo "Make sure run_tracking.py + run_tracking_server.py are deployed here"
        echo "too, for the ssh:// relay (workstation <-> bb-server1)."
        ;;
    workstation)
        mkdir -p "$path/run_files"
        echo "Workstation ready: local queue staging dir at $path."
        echo "Use:"
        echo "  oceanicu_runs.py --queue $path/queue_\$USER.yaml <command>"
        echo "  oceanicu_runs.py stage --run-root ... --source-dir ... --run-files-dir $path/run_files"
        echo "Then rsync $path/ to bb-server1:/data/OceanICU/oceanicu_3d/experiments/hpc_commands/"
        ;;
    *)
        echo "ERROR: role must be hpc, relay, or workstation" >&2
        exit 1
        ;;
esac

echo "Re-source ~/.bashrc (or open a new shell) for env vars to take effect."
