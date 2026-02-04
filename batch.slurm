#!/bin/bash

# Make queue settings here

export TPXO_FOLDER="/server/data/TPXO9"
export WOA="/server/data/WOA"
#export RIVER_FOLDER="/data/emorid"
#export RIVER_FILE="/server/data/TPXO9"
export SIMULATION_INITIAL_DATE="2020-01-01"
export SIMULATION_START_DATE="2020-01-01"
export SIMULATION_STOP_DATE="2022-01-01"

python run_chunks.py run_model.py ns_config.yaml /tmp 2 --dryrun
