#!/bin/bash
# test_compute_to_login_ssh.sbatch -- one-off test: can a compute node ssh
# to the login node it was submitted from? Answers the open question for
# the "push the registry out from a compute node" design (EXPERIMENT_TRACKING.md).
#
# On this project's actual production HPC: confirmed 2026-08-29, NO --
# see EXPERIMENT_TRACKING.md "Keeping bb-server1's copy of the registry up to
# date", use the login-node cron there instead. Kept here in case this
# tooling is ever deployed to a different cluster, where the answer may
# differ -- re-run it there rather than assuming either way.
#
# Submit from an actual login-node terminal session (so $SLURM_SUBMIT_HOST
# resolves to the real login node, no hostname to guess or hardcode):
#   sbatch test_compute_to_login_ssh.sbatch
# Then check the output file it prints the name of, e.g.:
#   cat slurm-<jobid>.out
#
#SBATCH --job-name=test_ssh
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:02:00

echo "Running on compute node: $(hostname)"
echo "Submitted from (login node): $SLURM_SUBMIT_HOST"
echo

echo "--- ssh -o BatchMode=yes -o ConnectTimeout=5 $SLURM_SUBMIT_HOST 'hostname && whoami && date' ---"
if ssh -o BatchMode=yes -o ConnectTimeout=5 "$SLURM_SUBMIT_HOST" 'hostname && whoami && date'; then
    echo
    echo "RESULT: SSH FROM COMPUTE NODE TO LOGIN NODE WORKS."
else
    rc=$?
    echo
    echo "RESULT: SSH FAILED (exit code $rc)."
    echo "  -o BatchMode=yes means this failed fast rather than hanging on a"
    echo "  password prompt -- likely no passwordless key set up between"
    echo "  compute nodes and the login node, or it's blocked outright."
fi
