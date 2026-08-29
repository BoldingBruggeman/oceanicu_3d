#!/bin/bash
# setup_run_tracking.sh -- one-time env/directory setup for the OceanICU
# run-tracking system. Standalone: copy this ONE file anywhere (no git
# checkout needed -- this is exactly the file to hand to someone on the
# HPC, which never needs the oceanicu_3d repo at all) and run it there.
#
# Usage:
#   ./setup_run_tracking.sh hpc         /data/OceanICU/oceanicu_3d/experiments
#   ./setup_run_tracking.sh relay       /data/OceanICU/oceanicu_3d/experiments   # bb-server1
#   ./setup_run_tracking.sh workstation ~/hpc_commands
#
# See RUN_TRACKING.md ("Working across machines", "Command queue") for
# what each of these env vars/directories is actually for.
set -eu

role="${1:?usage: $0 hpc|relay|workstation PATH}"
path="${2:?usage: $0 hpc|relay|workstation PATH}"

add_to_bashrc() {
    grep -qxF "$1" ~/.bashrc 2>/dev/null || echo "$1" >> ~/.bashrc
}

case "$role" in
    hpc)
        mkdir -p "$path/hpc_commands/run_files"
        add_to_bashrc "export OCEANICU_RUN_DB=$path/run_registry.sqlite"
        add_to_bashrc "export OCEANICU_RUN_ROOT_BASE=$path"
        echo "HPC ready: registry + hpc_commands/ under $path."
        echo "Still needed here (not this script): scripts/ itself"
        echo "(run_tracking.py, oceanicu_runs.py, chunk_runner.py, run_chunk.slurm,"
        echo "apply_commands.py), rsync'd in from bb-server1 -- never git/GitHub."
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
