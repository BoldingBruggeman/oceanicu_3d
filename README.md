# [OceanICU](https://ocean-icu.eu/) [pyGETM ](https://github.com/BoldingBruggeman/getm-rewrite) setups

> **Updating [bolding-bruggeman.com/oceanicu_3d](https://bolding-bruggeman.com/oceanicu_3d/)?**
> Read [`docs/web-regeneration.md`](docs/web-regeneration.md) first — two
> scripts, one real gotcha (hand-edited content pages get silently
> overwritten), and a stale-looking directory that's actually a red herring.

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

Each domain has its own bathymetry generator, e.g. for NSe:

```shell
python NSe/generate_nse_bathymetry.py -h
```

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

A domain config (e.g. `NSe/config/nse_cmems.yaml`) is run, or turned into a
standalone script for the HPC, with `driver/oceanicu_driver.py`:

```bash
python driver/oceanicu_driver.py NSe/config/nse_cmems.yaml --start 2020-01-01 --stop 2020-02-01
python driver/oceanicu_driver.py NSe/config/nse_cmems.yaml --start 2020-01-01 --stop 2020-02-01 --dump-python generated_nse_cmems.py
python driver/oceanicu_driver.py -h
```

The generated script needs only pyGETM (not pygetm-config), and takes its own
`--start/--stop/--load-restart/--save-restart` arguments.

#### Splitting a full simulation into a number of time periods

Long simulations are run in chunks (annual, monthly or daily, times a
multiplier), each restarting from the previous one, tracked in an SQLite
registry: `running/chunk_runner.py` runs one chunk, `running/run_chunk.slurm`
resubmits itself for the next, and `running/oceanicu_experiments.py` registers,
pauses, reruns and lists experiments. See `running/EXPERIMENT_TRACKING.md`.

Notes: 

- This is work in progress and updates to scripts can be foreseen - furthermore they come with no guarantee.

*This work was funded by the European Union under grant agreement no. 101083922 (OceanICU) and UK Research and Innovation (UKRI) under the UK government’s Horizon Europe funding guarantee [grant number 10054454, 10063673, 10064020, 10059241, 10079684, 10059012, 10048179].*
