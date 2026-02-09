#!/bin/bash

# Make queue settings here -from Gennadis amm7 script

#SBATCH --job-name=test
###SBATCH --error=job_%j.err
###SBATCH --output=job_%j.out
#SBATCH --nodes=12
#SBATCH --ntasks-per-core=1
#SBATCH --exclusive
####SBATCH --ntasks-per-node=63
####SBATCH --cpus-per-task=1
#SBATCH --time=06:00:00
###SBATCH --mail-type=END,FAIL
###SBATCH --mail-user=gle@pml.ac.uk
###SBATCH --partition=mod
###SBATCH --mem=32GB

export OMP_NUM_THREADS=1
export SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK
NP=$SLURM_NPROCS

module purge
##module load openmpi/4.1.6-gcc-11.4.1
##module load netcdf-hdf5parallel/4.9.2-ogr-2024.10

module load amd/2024.10
module load netcdf-hdf5parallel/4.9.2-amd-2024.10

# Simulation configuration

# Setup file/path environment variables

# Tidal boundary conditions
#export TPXO_FOLDER="/server/data/TPXO9"

# Initial and boundary conditions - WOA (CMEMS)
#export HYDROGRAPHY_FOLDER="/server/data/WOA"

# River settings
#export RIVER_FOLDER="/data/emorid"
#export RIVER_FILE="EMORID_1990_2024.nc"

# Setup dates defining the simulation period
# If SIMULATION_START_DATE is not set it will
# default to SIMULATION_INITIAL_DATE
export SIMULATION_INITIAL_DATE="2020-01-01"
export SIMULATION_START_DATE="2020-01-01"
export SIMULATION_STOP_DATE="2022-01-01"

# python run_chunks.py -h
#
# 4 Mandatory arguments:
#   1) python run-script
#   2) setup specific yaml configutration file
#   3) Base output folder
#   4) number of cores to use

# 3 Optional arguments:
#   1) --exp - append to output folder
#   2) --daily_chunks, monthly_chunks - default is annual chunks
#   3) --chunk_size - default is 1 of the chunk size
#   4) --dryrun - will print the run commands to screen for inspection

conda activate pygetm
python run_chunks.py run_model.py ns_config.yaml /data/$USER/NS $NP --exp test
conda deactivate
