#!/bin/bash

# Simple bash script - mainly for testing on different machines

if [ $(hostname) == "orca" ]; then

  export HYDROGRAPHY_FOLDER="/server/data/WOA"
  export TPXO_FOLDER="/server/data/TPXO9"
  export METEO_FOLDER="/data/ERA5/NA"
  export RIVER_FOLDER="/data/emorid"
  export RIVER_FILE="EMORID_1990_2024.nc"
  export FABM_FOLDER="/data/FABM"

fi

export SIMULATION_INITIAL_DATE="2020-01-01"
export SIMULATION_START_DATE="2020-01-01"
export SIMULATION_STOP_DATE="2022-01-01"

conda activate pygetm
python run_chunks.py run_model.py ns_config.yaml /data/$USER/NS $NP --exp test
conda deactivate
