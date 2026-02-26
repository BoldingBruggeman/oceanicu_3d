# SST Analysis Troubleshooting Guide

## Quick Fix for Coordinate Detection Issues

If you get the error:
```
ValueError: Could not identify lat/lon coordinates
```

Follow these steps:

### Step 1: Inspect Your Files

Run the script with the `--inspect` flag to see what's in your NetCDF files:

```bash
python sst_analysis.py your_obs.nc your_model.nc --inspect
```

This will show you all available:
- Dimensions
- Coordinates
- Data variables

### Step 2: Identify the Right Variables

From the inspection output, identify:
1. The SST variable name (look for temperature-related variables)
2. The latitude coordinate name
3. The longitude coordinate name

For both observed and model files.

### Step 3: Run with Explicit Names

Use the identified names in your command:

```bash
python sst_analysis.py obs.nc model.nc \
    --obs-var sst \
    --model-var tos \
    --obs-lat latitude \
    --obs-lon longitude \
    --model-lat nav_lat \
    --model-lon nav_lon
```

## Common NetCDF Coordinate Patterns

### Pattern 1: CF-Compliant (most satellite data)
```
Coordinates: lat, lon
Variables: sst, analysed_sst
```

**Command:**
```bash
python sst_analysis.py obs.nc model.nc \
    --obs-var analysed_sst \
    --obs-lat lat \
    --obs-lon lon
```

### Pattern 2: NEMO Ocean Model
```
Coordinates: nav_lat, nav_lon (or nav_lat_grid_T, nav_lon_grid_T)
Variables: sosstsst, tos
```

**Command:**
```bash
python sst_analysis.py obs.nc model.nc \
    --model-var sosstsst \
    --model-lat nav_lat \
    --model-lon nav_lon
```

### Pattern 3: ROMS Ocean Model
```
Coordinates: lat_rho, lon_rho
Variables: temp
```

**Command:**
```bash
python sst_analysis.py obs.nc model.nc \
    --model-var temp \
    --model-lat lat_rho \
    --model-lon lon_rho
```

### Pattern 4: CMIP6 Models
```
Coordinates: latitude, longitude (or lat, lon)
Variables: tos
```

**Command:**
```bash
python sst_analysis.py obs.nc model.nc \
    --model-var tos \
    --model-lat latitude \
    --model-lon longitude
```

### Pattern 5: CESM/CAM Models
```
Coordinates: TLAT, TLONG
Variables: SST
```

**Command:**
```bash
python sst_analysis.py obs.nc model.nc \
    --model-var SST \
    --model-lat TLAT \
    --model-lon TLONG
```

## Other Common Issues

### Issue: "Could not auto-detect SST variable"

**Solution:** Specify the variable name explicitly:
```bash
python sst_analysis.py obs.nc model.nc \
    --obs-var <your_sst_variable_name> \
    --model-var <your_sst_variable_name>
```

### Issue: Memory Error

**Solutions:**
1. Process a subset of the data (spatial or temporal)
2. Use a machine with more RAM
3. Consider processing in chunks (requires code modification)

### Issue: Time Dimension Not Found

**Check:** Your files must have a dimension called 'time' (case-sensitive)

If your time dimension has a different name, you'll need to rename it:
```python
import xarray as xr
ds = xr.open_dataset('file.nc')
ds = ds.rename({'TIME': 'time'})  # or whatever your time dim is called
ds.to_netcdf('file_renamed.nc')
```

### Issue: Different Time Units

The script handles this automatically through xarray, but if you have issues:
- Ensure both files have proper CF-compliant time encoding
- Check that time units are specified (e.g., "days since 1950-01-01")

### Issue: Coordinates Are Not Aligned

This is expected! The script is designed to handle different grids. Just make sure:
- Both files have latitude and longitude information
- Coordinates are in degrees (not radians)
- Longitude range is either [0, 360] or [-180, 180] (script handles both)

## Getting More Help

### Check Your Data Structure

Use ncdump or xarray to inspect your files:

```python
import xarray as xr
ds = xr.open_dataset('your_file.nc')
print(ds)
```

Or from command line:
```bash
ncdump -h your_file.nc
```

### Contact Information

If you continue to have issues, please provide:
1. Output from `--inspect` flag
2. The exact error message
3. Your command line call

## Example Workflow

```bash
# Step 1: Inspect both files
python sst_analysis.py obs_data.nc model_data.nc --inspect

# Step 2: Note the coordinate and variable names from the output

# Step 3: Run the analysis with explicit names
python sst_analysis.py obs_data.nc model_data.nc \
    --obs-var analysed_sst \
    --obs-lat lat \
    --obs-lon lon \
    --model-var tos \
    --model-lat latitude \
    --model-lon longitude \
    --output-dir my_results

# Step 4: Check the results in my_results/ directory
```

## Updated Coordinate Detection

The script now automatically detects these coordinate names:

**Latitude:**
- lat, latitude, y, nav_lat, LATITUDE, Latitude
- lat_rho, lat_u, lat_v, TLAT, ULAT, geolat
- yt_ocean, ylat
- Plus any variable with 'lat' in the name

**Longitude:**
- lon, longitude, x, nav_lon, LONGITUDE, Longitude
- lon_rho, lon_u, lon_v, TLONG, ULONG, geolon
- xt_ocean, xlon
- Plus any variable with 'lon' in the name

If your coordinates still aren't detected, use the manual specification flags!
