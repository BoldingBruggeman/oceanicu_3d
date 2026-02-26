# Multi-Variable Analysis Script with Wildcard Support

Enhanced version of the SST analysis script that supports:
- **Multiple files per variable** using wildcards (e.g., `sst_*.nc`)
- **Flexible file patterns** - wildcards or space-separated lists
- **Folder + pattern specification** for organized data structures
- **Multiple variables** (SST, salinity, SSH, velocity, chlorophyll, etc.)
- **Automatic variable detection** with manual override options
- **Flexible coordinate naming** for different NetCDF conventions

## Key Improvements

### 1. Multiple Files Per Variable with Wildcards
- Process yearly, monthly, or arbitrary file splits automatically
- Use wildcard patterns: `sst_*.nc`, `sst_202?.nc`, `*_sst.nc`
- Or specify exact files: `sst_2020.nc sst_2021.nc sst_2022.nc`
- Files are automatically concatenated along the time dimension

### 2. Folder + Pattern Organization
- Specify data folders separately from file patterns
- Clean separation of observations and model data locations
- Easy to manage large datasets organized in directories

### 3. Variable Flexibility
- Works with any variable type
- Automatic detection of common ocean variables
- Manual specification for custom variable names

## Installation

No additional dependencies beyond standard scientific Python:
```bash
pip install numpy xarray matplotlib seaborn scipy pandas
```

## Usage Examples

### Example 1: Single Variable with Wildcards
Process all SST files from 2020-2023:
```bash
python multi_variable_analysis.py \
    --obs-folder ./observations \
    --obs-pattern "sst_*.nc" \
    --model-folder ./model_output \
    --model-pattern "model_sst_*.nc" \
    --variable sst
```

### Example 2: Specific Years (Space-Separated)
Process only specific years:
```bash
python multi_variable_analysis.py \
    --obs-folder ./obs \
    --obs-pattern "sst_2020.nc sst_2021.nc sst_2022.nc" \
    --model-folder ./model \
    --model-pattern "model_2020.nc model_2021.nc model_2022.nc" \
    --variable sst
```

### Example 3: Multiple Variables, Each with Multiple Files
Analyze both SST and salinity with yearly files:
```bash
python multi_variable_analysis.py \
    --obs-folder ./obs/sst ./obs/salinity \
    --obs-pattern "sst_*.nc" "sal_*.nc" \
    --model-folder ./model/sst ./model/salinity \
    --model-pattern "sst_*.nc" "sal_*.nc" \
    --variable sst salinity \
    --variable-label "Sea Surface Temperature" "Salinity"
```

### Example 4: Complex Pattern with Wildcards
Using more specific wildcard patterns:
```bash
python multi_variable_analysis.py \
    --obs-folder ./data/observations \
    --obs-pattern "GHRSST_*_L4_*.nc" \
    --model-folder ./data/model \
    --model-pattern "CMEMS_model_202[0-3]*.nc" \
    --variable sst
```

### Example 5: Same Folder, Different Patterns
When observations and model are in the same folder:
```bash
python multi_variable_analysis.py \
    --obs-folder ./data \
    --obs-pattern "obs_*.nc" \
    --model-folder ./data \
    --model-pattern "model_*.nc" \
    --variable sst
```

### Example 6: With Manual Variable Names
```bash
python multi_variable_analysis.py \
    --obs-folder ./obs \
    --obs-pattern "ghrsst_*.nc" \
    --model-folder ./model \
    --model-pattern "cmems_*.nc" \
    --variable temperature \
    --obs-var analysed_sst \
    --model-var thetao \
    --variable-label "Sea Surface Temperature"
```

### Example 7: Inspect Files Before Analysis
Check what variables and coordinates are available:
```bash
python multi_variable_analysis.py \
    --obs-folder ./obs \
    --obs-pattern "*.nc" \
    --model-folder ./model \
    --model-pattern "*.nc" \
    --variable sst \
    --inspect
```

## Command Line Arguments

### Required Arguments
- `--obs-folder`: Folder(s) containing observed data files (one per variable)
- `--obs-pattern`: File pattern(s) for observed files (wildcards or space-separated list)
- `--model-folder`: Folder(s) containing model data files (one per variable)
- `--model-pattern`: File pattern(s) for model files (wildcards or space-separated list)
- `--variable`: Variable identifier(s) (e.g., sst, salinity, ssh)

### Optional Arguments
- `--obs-var`: Variable names in observed files (auto-detected if not provided)
- `--model-var`: Variable names in model files (auto-detected if not provided)
- `--variable-label`: Display labels for plots (defaults to uppercase variable names)
- `--output-dir`: Output directory (default: `./analysis_output`)
- `--regrid-method`: `nearest` or `bilinear` (default: `nearest`)
- `--inspect`: Only inspect files, don't run analysis

## File Pattern Syntax

### Wildcard Patterns
- `*` - Matches any characters: `sst_*.nc` matches `sst_2020.nc`, `sst_2021.nc`, etc.
- `?` - Matches single character: `sst_202?.nc` matches `sst_2020.nc`, `sst_2021.nc`, etc.
- `[...]` - Character range: `sst_20[12][0-9].nc` matches years 2010-2029
- `**` - Recursive directory search: `**/sst_*.nc` (searches subdirectories)

### Space-Separated Lists
Specify exact files with spaces:
```bash
--obs-pattern "file1.nc file2.nc file3.nc"
```

### Combining Patterns
Mix wildcards in a single pattern:
```bash
--obs-pattern "sst_2020*.nc sst_2021*.nc temp_*.nc"
```

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

## How File Concatenation Works

1. **File Discovery**: Script expands wildcards to find all matching files
2. **Sorting**: Files are sorted alphabetically by filename
3. **Loading**: Uses `xr.open_mfdataset` for efficiency when possible
4. **Concatenation**: Files are concatenated along the time dimension
5. **Time Sorting**: Resulting dataset is sorted by time coordinate

**Important**: Files should have compatible spatial grids and variable structures.

## Data Organization Best Practices

### Recommended Folder Structure
```
project/
├── observations/
│   ├── sst/
│   │   ├── sst_2020.nc
│   │   ├── sst_2021.nc
│   │   └── sst_2022.nc
│   └── salinity/
│       ├── sal_2020.nc
│       ├── sal_2021.nc
│       └── sal_2022.nc
└── model/
    ├── sst/
    │   ├── model_sst_2020.nc
    │   ├── model_sst_2021.nc
    │   └── model_sst_2022.nc
    └── salinity/
        ├── model_sal_2020.nc
        ├── model_sal_2021.nc
        └── model_sal_2022.nc
```

### File Naming Conventions
- Use consistent prefixes: `sst_`, `model_`, `obs_`
- Include time periods in filenames: `_2020.nc`, `_202001.nc`
- Use leading zeros for proper sorting: `sst_01.nc`, not `sst_1.nc`

## Supported Variables

The script automatically detects common variable names:

- **SST**: `sst`, `SST`, `sea_surface_temperature`, `tos`, `analysed_sst`
- **Salinity**: `salinity`, `sal`, `so`, `salt`, `salin`
- **Temperature**: `temp`, `temperature`, `thetao`, `t`, `theta`
- **SSH**: `ssh`, `SSH`, `sea_surface_height`, `zos`, `eta`
- **Velocity U**: `u`, `uo`, `u_velocity`, `uvel`, `U`
- **Velocity V**: `v`, `vo`, `v_velocity`, `vvel`, `V`
- **Chlorophyll**: `chl`, `chlor`, `chlorophyll`, `chla`, `CHL`

## Common Use Cases

### Case 1: Yearly Model Validation
You have annual files for observations and model:
```bash
python multi_variable_analysis.py \
    --obs-folder /data/obs \
    --obs-pattern "OISST_*_yearly.nc" \
    --model-folder /data/model \
    --model-pattern "NEMO_*_yearly.nc" \
    --variable sst
```

### Case 2: Monthly Files, Multiple Years
```bash
python multi_variable_analysis.py \
    --obs-folder /data/obs \
    --obs-pattern "sst_202[0-2]*.nc" \
    --model-folder /data/model \
    --model-pattern "model_202[0-2]*.nc" \
    --variable sst
```

### Case 3: Mixed Time Periods
```bash
# This will process all matching files and concatenate them
python multi_variable_analysis.py \
    --obs-folder /data/obs \
    --obs-pattern "daily_*.nc monthly_*.nc" \
    --model-folder /data/model \
    --model-pattern "output_*.nc" \
    --variable sst
```

### Case 4: Multi-Variable Ocean Model Validation
```bash
python multi_variable_analysis.py \
    --obs-folder /obs/sst /obs/ssh /obs/sal \
    --obs-pattern "*.nc" "*.nc" "*.nc" \
    --model-folder /model/output /model/output /model/output \
    --model-pattern "sst_*.nc" "ssh_*.nc" "sal_*.nc" \
    --variable sst ssh salinity \
    --variable-label "Sea Surface Temperature" "Sea Surface Height" "Salinity"
```

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
| Files | Single pair | Multiple files per variable |
| File specification | Direct paths | Folder + pattern |
| Wildcards | No | Yes |
| Variables | SST only | Any variable |
| CLI | Fixed arguments | Flexible patterns |
| File concatenation | Manual | Automatic |
| Output | Single directory | Per-variable directories |

## Performance Considerations

### Memory Usage
- Large datasets with many files may require significant RAM
- The script loads full datasets into memory
- Consider processing subsets if memory is limited

### Speed Tips
1. Use `--regrid-method nearest` (faster than bilinear)
2. Fewer files = faster loading
3. Files on SSD are faster than network drives
4. Smaller spatial grids process faster

### Parallel Processing
Current version processes variables sequentially. For many variables, consider running multiple instances in parallel with different variables.

## Troubleshooting

### "No files found matching pattern"
- Check folder path exists: `ls /your/folder/path`
- Test wildcard: `ls /your/folder/pattern*.nc`
- Use absolute paths if relative paths fail
- Check file permissions

### "Could not identify time dimension"
- Files must have a time dimension for concatenation
- Use `--inspect` to check dimension names
- Files must have compatible time dimensions

### "Shape mismatch after regridding"
- Ensure all files have the same spatial grid structure
- Check that time dimensions align properly
- Verify files are not corrupted

### "open_mfdataset failed"
- Script will fall back to sequential loading
- May be slower but should still work
- Check that all files are valid NetCDF

### Memory errors
- Process fewer files at once
- Use a subset of years
- Process variables separately
- Use a machine with more RAM

## Tips

1. **Always inspect first**: Use `--inspect` to verify file patterns match correctly
2. **Start small**: Test with one year before processing many years
3. **Organize by variable**: Keep different variables in separate folders
4. **Consistent naming**: Use consistent file naming conventions
5. **Check wildcards**: Test wildcard patterns with `ls` before running script

## Advanced Examples

### Using Shell Variables
```bash
OBS_DIR="/data/observations/sst"
MODEL_DIR="/data/model/output"
YEARS="202[0-3]"

python multi_variable_analysis.py \
    --obs-folder "$OBS_DIR" \
    --obs-pattern "sst_${YEARS}*.nc" \
    --model-folder "$MODEL_DIR" \
    --model-pattern "model_${YEARS}*.nc" \
    --variable sst
```

### Processing Specific Months
```bash
# Only process January data
python multi_variable_analysis.py \
    --obs-folder ./obs \
    --obs-pattern "*_01.nc" \
    --model-folder ./model \
    --model-pattern "*_01.nc" \
    --variable sst
```

### Subdirectory Search
```bash
# Search all subdirectories for SST files
python multi_variable_analysis.py \
    --obs-folder ./obs \
    --obs-pattern "**/sst*.nc" \
    --model-folder ./model \
    --model-pattern "**/sst*.nc" \
    --variable sst
```

## License

Modify and use freely for scientific research.

## Support

For issues with:
- **File discovery**: Verify patterns with `ls` command first
- **Variable detection**: Use `--inspect` to see available variables
- **NetCDF format**: Ensure files are valid with `ncdump -h filename.nc`
- **Memory issues**: Process smaller subsets or use more powerful hardware
