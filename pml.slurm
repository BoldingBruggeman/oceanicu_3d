#!/bin/bash

# Make queue settings here -from Gennadis amm7 script

#SBATCH --job-name=oceanICU_amm7
###SBATCH --error=job_%j.err
###SBATCH --output=job_%j.out
#SBATCH --nodes=4
#SBATCH --ntasks-per-core=1
##SBATCH --nodelist=scyllacpu13,scyllacpu14,scyllacpu15,,scyllacpu16
#SBATCH --exclusive
#SBATCH --ntasks-per-node=64
#SBATCH --cpus-per-task=1
#SBATCH --time=24:00:00
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=rict@pml.ac.uk
###SBATCH --partition=mod
###SBATCH --mem=32GB

export OMP_NUM_THREADS=1
export SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK
NP=$SLURM_NPROCS

module purge
prgenv=igi
vprgenv=2024.1
module load ${prgenv}/${vprgenv}
module load ucx/1.16.0.mlx
module load hdf5-parallel/1.14.5-${prgenv}-${vprgenv}
module load netcdf-hdf5parallel/4.9.2-${prgenv}-${vprgenv}
module load intel_ipp_intel64/latest


# conda run -n pygetm  python /scyllapfs/scratch/rict/run/oceanicu_3d/run_simulation.py /scyllapfs/scratch/rict/run/oceanicu_3d/ns_run.yaml  --experiment Baseline --skip-setup --np $NP
conda run -n pygetm  python /scyllapfs/scratch/rict/run/oceanicu_3d/run_simulation.py /scyllapfs/scratch/rict/run/oceanicu_3d/amm7_run.yaml  --experiment Baseline --skip-setup --np $NP

#python /scyllapfs/scratch/rict/run/oceanicu_3d/run_chunks.py /work/rict/run/oceanICU/NS/Baseline/run_model.py /work/rict/run/oceanICU/NS/Baseline/ns_model_config.yaml /work/rict/run/oceanICU/NS/Baseline $NP --skip-setup 
#conda run -n pygetm python run_chunks.py run_model.py ns_config.yaml "/data/$USER/NS" "$NP" --exp test
