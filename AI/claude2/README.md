# Multi-Variable Analysis Script

Enhanced version of the SST analysis script that supports:
- **Multiple input files** for both observations and models
- **Multiple variables** (SST, salinity, SSH, velocity, chlorophyll, etc.)
- **Automatic variable detection** with manual override options
- **Flexible coordinate naming** for different NetCDF conventions

## Key Improvements

### 1. Multiple File Support
- Process multiple observation-model pairs in a single run
- Each variable can come from a different file
- Separate output directories for each variable

### 2. Variable Flexibility
- Not limited to SST - works with any variable
- Automatic detection of common ocean variables:
  - Sea Surface Temperature (SST)
  - Salinity
  - Sea Surface Height (SSH)
  - Velocity components (U, V)
  - Chlorophyll
  - Any other variable with proper specification

### 3. Automatic Unit Conversion
- Kelvin to Celsius conversion for temperature variables
- Extensible for other unit conversions

## Installation

No additional dependencies beyond the original script:
```bash
pip install numpy xarray matplotlib seaborn scipy pandas
```

## Usage Examples

### Example 1: Single Variable (SST only)
```bash
python multi_variable_analysis.py \
    --obs observed_sst.nc \
    --model modeled_sst.nc \
    --variable sst
```

### Example 2: Multiple Variables
```bash
python multi_variable_analysis.py \
    --obs obs_sst.nc obs_salinity.nc obs_ssh.nc \
    --model model_sst.nc model_salinity.nc model_ssh.nc \
    --variable sst salinity ssh \
    --variable-label "Sea Surface Temperature" "Salinity" "Sea Surface Height"
```

### Example 3: With Specific Variable Names
If the script can't auto-detect variables, specify them manually:
```bash
python multi_variable_analysis.py \
    --obs obs_data.nc \
    --model model_data.nc \
    --variable temperature \
    --obs-var analysed_sst \
    --model-var tos
```

### Example 4: Multiple Variables with Manual Specification
```bash
python multi_variable_analysis.py \
    --obs obs_file1.nc obs_file2.nc \
    --model model_file1.nc model_file2.nc \
    --variable temp salinity \
    --obs-var temperature so \
    --model-var thetao salt \
    --variable-label "Temperature" "Salinity" \
    --output-dir ./my_analysis
```

### Example 5: Inspect Files First
To see what variables and coordinates are available:
```bash
python multi_variable_analysis.py \
    --obs obs_data.nc \
    --model model_data.nc \
    --variable sst \
    --inspect
```

## Command Line Arguments

### Required Arguments
- `--obs`: One or more observed data NetCDF files
- `--model`: One or more model data NetCDF files  
- `--variable`: Variable identifiers (e.g., sst, salinity, ssh)

### Optional Arguments
- `--obs-var`: Variable names in observed files (auto-detected if not provided)
- `--model-var`: Variable names in model files (auto-detected if not provided)
- `--variable-label`: Display labels for plots (defaults to uppercase variable names)
- `--output-dir`: Output directory (default: `./analysis_output`)
- `--regrid-method`: `nearest` or `bilinear` (default: `nearest`)
- `--inspect`: Only inspect files, don't run analysis

## Output Structure

```
analysis_output/
├── statistics_report.txt          # Combined report for all variables
├── sst/                            # SST-specific outputs
│   ├── spatial_maps.png
│   ├── time_series.png
│   ├── statistics.png
│   ├── sst_obs_regridded.nc
│   └── sst_spatial_statistics.nc
├── salinity/                       # Salinity-specific outputs
│   ├── spatial_maps.png
│   ├── time_series.png
│   ├── statistics.png
│   ├── salinity_obs_regridded.nc
│   └── salinity_spatial_statistics.nc
└── ...
```

## Supported Variables

The script automatically detects common variable names:

- **SST**: `sst`, `SST`, `sea_surface_temperature`, `tos`, `analysed_sst`, `temp`, `temperature`
- **Salinity**: `salinity`, `sal`, `so`, `salt`, `salin`
- **Temperature**: `temp`, `temperature`, `thetao`, `t`, `theta`
- **SSH**: `ssh`, `SSH`, `sea_surface_height`, `zos`, `eta`
- **Velocity U**: `u`, `uo`, `u_velocity`, `uvel`, `U`
- **Velocity V**: `v`, `vo`, `v_velocity`, `vvel`, `V`
- **Chlorophyll**: `chl`, `chlor`, `chlorophyll`, `chla`, `CHL`

If your variable names don't match these patterns, use `--obs-var` and `--model-var` to specify them explicitly.

## Coordinate Detection

The script automatically detects common coordinate names:
- **Latitude**: `lat`, `latitude`, `y`, `nav_lat`, `TLAT`, `geolat`, etc.
- **Longitude**: `lon`, `longitude`, `x`, `nav_lon`, `TLONG`, `geolon`, etc.

Works with both 1D (lat, lon) and 2D (lat[y,x], lon[y,x]) coordinate arrays.

## Features

### Statistical Metrics Computed
- Mean, standard deviation, min, max
- Quantiles (5%, 25%, 50%, 75%, 95%)
- Bias (Model - Observed)
- Root Mean Square Error (RMSE)
- Mean Absolute Error (MAE)
- Temporal correlation
- Monthly statistics

### Visualizations Generated
1. **Spatial Maps**: Time-averaged fields, bias, and RMSE
2. **Time Series**: Spatial mean evolution and differences
3. **Statistics**: Scatter plots, distributions, Q-Q plots, error histograms

### Output Files
- Text report with comprehensive statistics
- NetCDF files with regridded observations
- NetCDF files with spatial statistics
- High-resolution PNG plots

## Differences from Original Script

| Feature | Original | Enhanced |
|---------|----------|----------|
| Files | Single pair | Multiple pairs |
| Variables | SST only | Any variable |
| CLI | Fixed arguments | Flexible lists |
| Output | Single directory | Per-variable directories |
| Detection | SST-specific | Generic with patterns |
| Organization | Class for one pair | Class for multiple pairs |

## Migration Guide

### Old way (original script):
```bash
python sst_analysis.py obs.nc model.nc \
    --obs-var sst \
    --model-var tos
```

### New way (enhanced script):
```bash
python multi_variable_analysis.py \
    --obs obs.nc \
    --model model.nc \
    --variable sst \
    --obs-var sst \
    --model-var tos
```

### Processing multiple variables that previously required multiple runs:
```bash
# Old way: Multiple runs
python sst_analysis.py obs_sst.nc model_sst.nc --output-dir sst_output
python sst_analysis.py obs_sal.nc model_sal.nc --output-dir sal_output

# New way: Single run
python multi_variable_analysis.py \
    --obs obs_sst.nc obs_sal.nc \
    --model model_sst.nc model_sal.nc \
    --variable sst salinity
```

## Tips

1. **Always inspect first**: Use `--inspect` to see available variables before running the full analysis
2. **Memory considerations**: Processing multiple large files simultaneously may require significant RAM
3. **Regridding**: The `nearest` method is faster; `bilinear` is more accurate but slower
4. **Custom variables**: For unusual variable names, always specify `--obs-var` and `--model-var`
5. **Units**: Temperature data in Kelvin is automatically converted to Celsius

## Troubleshooting

### "Could not auto-detect variable"
Use `--inspect` to see available variables, then specify with `--obs-var` and `--model-var`.

### "Shape mismatch after regridding"
Check that your files have compatible time dimensions and spatial grids.

### "Could not identify lat/lon coordinates"
Specify coordinate names manually (feature to be added in future version).

### Memory errors
Process fewer variables at once or use a machine with more RAM.

## Future Enhancements

Potential additions:
- Command-line coordinate specification
- Support for different time ranges
- Parallel processing for multiple variables
- Additional statistical metrics
- Interactive visualizations
- Regional subsetting options

## License

Same as original script - modify and use freely for scientific research.
