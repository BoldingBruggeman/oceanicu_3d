# [OceanICU](https://ocean-icu.eu/) [pyGETM ](https://github.com/BoldingBruggeman/getm-rewrite) setups

This repository contains  3(4?) different pyGETM setups used in the OceanICU project. 

The setups are:

- North Sea (ns)
- Eastern North Atlantic - ena4  - coarse resolution
- Eastern North Atlantic - ena8 - finer resolution
- [AMM7](https://pml.ac.uk/publications/a-reproducible-co9p2-amm7-nemov4-0-4-ersem-configuration/) configuration from [PML](https://pml.ac.uk/) - fine resolution

The North Sea setup is similar to the [legacy GETM North Sea](https://sourceforge.net/p/getm/getm-setups/ci/master/tree/NorthSea/) setup but using an updated bathymetry from [GEBCO](https://www.gebco.net/) and different forcing.

The coarse and finer resolution Eastern North Atlantic setups covers the same domain and only differs in horizontal resolution.

AMM7 is a well tested setup that has been used with [NEMO](https://www.nemo-ocean.eu/) as the model.

The keep the repository small only the absolute minimal number of files for making the full setups are include.  Making the full setups is described below and includes generation of bathymetry and downloading various external files

#### Activating pyGETM and clone this repository

Any work with pyGETM requires that the pygetm module can be imported into Python. 

- Install [pyGETM](https://github.com/BoldingBruggeman/getm-rewrite) on your machine and activate pygetm

```shell
conda activate pygetm
```

- [Clone/or fork](github.com://BoldingBruggeman/oceanicu_3d) this repository to your local machine

```shell
git clone https://github.com/BoldingBruggeman/oceanicu_3d.git
```

#### Preparing the setups

Below is given a step by step description of preparing evrything so a simulation can be done.

Note that all commands are executed within the clones `oceanicu_3d` repository.

##### Bathymetry

To prepare a bathymetry file do the following:

```shell
cd Bathymetry
python create_bathymetry.py <setup>
```

Here setup is either `ns`, `ena4` or `ena8`. 

A new file e.g.  `bathymetry_ena4.nc` is created and is ready for use in a simulation.

By default the GEBCO data are obtained on the fly from the [GEBCO web-site](https://www.gebco.net/) . If the data are locally available they can be used via a command line option to the create_bathymetry.py script see -  `python create_bathymetry.py -h`.

Two PNG's with the raw bathymetry and the bathymetry after processing will also be created.  Additional pre-processing can be added in the script in which case  the script must be executed again.

##### External dependencies

To make a real simulation additional external dependencies are required. These are tidal boundary data, initial data for temperature and salinity (+ other tracers if available) and meteorological forcing.

In order to run the download commands for for WOA and ERA5 data sets the pyGETM conda environment has to be activated.

1. 2D boundary conditions from [TPXO](https://www.tpxo.net/global/tpxo10) (v9 and v10 are supported)

2. Initial conditions for temperature and salinity from the [World Ocean Atlas - WOA](https://www.ncei.noaa.gov/products/world-ocean-atlas).
   The data can be obtained by using tools provided with pyGETM.

   ```bash
   python -m pygetm.input.woa -h
   ```
   e.g. to download temperature at 01 resolution using the default output name
   ```bash
   python -m pygetm.input.woa --grid 01 t 
   ```

3. Meteorological forcing from [ERA5](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels?tab=overview):
   ```bash
   python -m pygetm.input.era5 -h
   ```
   e.g. to download default variables for a given area and time period

   a minimal execution is:
   ```bash
    python -m pygetm.input.era -15 10 50 60 2020 2021
   ```

4. Meteorological forcing from [CMIP6](https://pcmdi.llnl.gov/CMIP6/)

   ```bash
   python -m pygetm.input.cmip6 -h
   ```
   e.g. to download default variables for a given area and time period

   a minimal execution is:

   ```bash
   python pygetm.input.cmip6 -15 10 50 60
   ```

5. Rivers - to follow

Notes:

- Due to licensing the TPXO data will have to be obtained on an individual basis. All files will have to be placed in a common folder.
- TPXO and WOA are global data sets and only have to be downloaded once and can then be used for different geographical setups.
- Even if it is possible ERA5 can also be downloaded for the entire globe  it will take a long time and will require a lot of disk space. 
- To use the ERA5 download script the necessary [credentials](https://cds.climate.copernicus.eu/how-to-api) have to be in place.
- CMIP6 contains a large number of different experiments and the command above will have to be specified for each of the configurations intended to simulate.

#### Making a simulation

All necessary data are now available and a simulation can be made.

##### The main run-script

A general run-script - `run_model.py` is included and it can be used to run the  different setups. The script takes a number of command line arguments providing a fast method to investigate  the configuration and alter e.g. file locations. 

Setup specific configurations are done in a YAML-formatted file specific to a given setup. The default name is `<setup>_config.yaml` - but the file can be copied and modified and the new file used instead.

The run-script must be executed with a YAML-configuration file - even in the case with the `-h` option. The rationale is given below.

`python run_model.py ns_config.yaml -h `

###### Configuration precedens

The 3 sources for configurations and their order of precedens is explained here:

1. Simple values in the YAML-configuration file
2. Optionally a reference to an  `environment variable` also specified in the YAML-file - e.g.  `Path(os.getenv("WOA", "/data/WOA"))`. If the `WOA` is set it will take precedens over the simple variable `/data/WOA`. In both cases a value of type Path is returned.
3. Command line arguments provided to the run-script  Use the above given command to list the possible arguments and apply them as  - e.g. `--woa_dir /mydata/woa`.

The above implementation gives a very flexible way of configuring for for different execution environments without having to edit files - making the out of date with what is in the repository.

Typically external dependencies will be in different folders for different users and on different machines. 

To evaluate if the configuration is what was intended the following command can be executed and the result inspected:

`python run_model.py ns_config.yaml <many options> --print_config `

The runscript only takes three mandatory command line options - the configuration-script and the start and stop time of the simulation. 

```bash
python run_model.py ns_config.yaml "2020-01-01 00:00:00" "2022-00-01 00:00:00"
```

If all external files are in default places this will make a 2 year long simulation saving the resulting files to the current folder.

As an example to read TPXO from a non-default folder:



```shell
python run_model.py ns_config.yaml "2020-01-01 00:00:00" "2020-02-01 00:00:00" --tpxo_dir ~/TPXO10b
```

Similar to the first example - but getting TPXO information from a specified folder. The same can be achieved by setting an environment variable - ```export TPXO=~/TPXO10```.

Note that using the `--print-config` option will in all cases show the final configuration.

To run the simulation on multiple processors the above command has to be modified like:

```shell
mpiexec -n 12 python run_model.py ns_config.yaml "2020-01-01 00:00:00" "2020-02-01 00:00:00" 
```

In which case the simulation will be done on 12 processors in parallel.

#### Splitting a full simulation into a number of time periods

Sometimes making a full simulation over e.g. many years is not feasible and the simulation must be split up in time chunks. All chunks will in principle be run using the same configuration and only the start and stop time of the different chunks will vary. Basically what the script does is to do the book keeping of time and provide and handle output and restart files in a manner so nothing is overwritten or missing when the entire run is done. Furthermore, all configuration files are copied to the output folder for future reference - and possible re-run.

```bash
python run_chunks.py -h
```

The script takes four mandatory arguments :

1.  A python script for doing a single simulation
2. A YAML-configuration file
3. An output folder - files will be written into this folder with possible additional levels
4. The number of processors to use

The optional arguments:

1. `--exp` - provide a string that will be appended to the output folder
2. `--daily_chunks` or `--monthly_chunks` - the default is to split the simulation in annual chunks
3. `--chunk_multiplier`  - defaults to 1 - if e.g. set to 10 the chunk size will be 10 years
4. `--dryrun` - if specified the commands to be executed will be writte to the screen for inspection

The time period for the simulation for the simulation is obtained from environment variables. This is useful when running in a batch submission script. The variables are:

- SIMULATION_INITIAL_DATE
- SIMULATION_START_DATE
- SIMULATION_STOP_DATE

All with the format "%Y-%m-%d" - e.g. "2020-01-01". If SIMULATION_START_DATE is not set the start date of the simulation sequence will be set to SIMULATION_INITIAL_DATE. 

A working example is - with proper settings of the above mentioned environment variables:

```bash
python run_chunks.py run_model.py ns_config.yaml /data/$USER/NS/ 12
```

Running the simulation in annual chunks and saving results in /data/$USER/NS/<yyyy>

Note that the configuration for the actual simulations is fully controlled by the two files provides as the first two command line arguments.

Notes: 

- This is work in progress and updates to scripts can be foreseen - furthermore they come with no guarantee.
- To run the create_bathymetry.py script it is necessary to have scikit-image installed.

*This work was funded by the European Union under grant agreement no. 101083922 (OceanICU) and UK Research and Innovation (UKRI) under the UK government’s Horizon Europe funding guarantee [grant number 10054454, 10063673, 10064020, 10059241, 10079684, 10059012, 10048179].*
