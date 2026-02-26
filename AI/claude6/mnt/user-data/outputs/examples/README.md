# Ocean Model Analysis - Examples and Usage Guide

Comprehensive examples showing how to analyze ocean model output against observations for different scenarios: surface fields, fields at specific depths, and full 3D depth-resolved analysis.

## Architecture Overview

The analysis framework consists of two core modules:

1. **`multi_variable_analysis.py`** - Surface and 2D field analysis
   - Handles 2D surface fields (SST, SSH, surface salinity, etc.)
   - Can also analyze 2D fields extracted from 3D data
   - Supports multiple files, wildcards, various regridding methods
   
2. **`depth_resolved_analysis.py`** - Full 3D vertical profile analysis  
   - Handles full 3D data with depth coordinates
   - Sigma coordinate support (ROMS, FVCOM, HYCOM)
   - Depth-dependent statistics and visualizations

## Quick Start - Which Example to Use?

| Your Data | Use This Example | Module |
|-----------|------------------|---------|
| 2D surface fields (SST, SSH) | Example 1 | `multi_variable_analysis.py` |
| Want temperature at 100m, 500m | Example 2 | `multi_variable_analysis.py` + extraction |
| Full vertical profiles | Example 3 | `depth_resolved_analysis.py` |

## Example 1: Surface Variable Analysis

**File**: `example_01_surface_sst.py`

Analyzes 2D surface fields using the standard surface analysis workflow.

### When to Use
- Sea surface temperature (SST)
- Sea surface height (SSH)  
- Surface salinity
- Surface chlorophyll
- Any other 2D surface field

### What It Does
1. Loads multiple files (yearly, monthly, etc.) using wildcards
2. Auto-detects variable names
3. Regrids to common grid (conservative, bilinear, etc.)
4. Computes comprehensive statistics
5. Creates spatial maps, time series, scatter plots

### Usage
```bash
python example_01_surface_sst.py
```

### Key Features
```python
analyzer = MultiVariableAnalyzer(output_dir="./sst_output")

analyzer.add_dataset_pair(
    obs_folder="./data/obs",
    obs_pattern="sst_*.nc",  # Matches sst_2020.nc, sst_2021.nc, etc.
    model_folder="./data/model",
    model_pattern="model_sst_*.nc",
    variable="sst",
)

analyzer.load_all_data()
analyzer.regrid_data(method='conservative', direction='obs_to_model')
analyzer.compute_statistics()
analyzer.create_visualizations()
```

### Output
```
sst_output/
├── statistics_report.txt
└── sst/
    ├── spatial_maps.png
    ├── time_series.png
    ├── statistics.png
    ├── sst_regridded.nc
    └── sst_spatial_statistics.nc
```

## Example 2: Fields at Specific Depths

**File**: `example_02_depth_slices.py`

Extracts 2D fields at specific depths from 3D data, then analyzes them as surface fields.

### When to Use
- Want temperature at 100m
- Want salinity at multiple levels (50m, 100m, 500m)
- Need 2D analysis at subsurface levels
- Comparing mixed layer depth properties

### What It Does
1. Loads 3D data (observations and model)
2. Extracts 2D slice at specified depth
3. Handles both z-level and sigma coordinates
4. Saves extracted 2D fields
5. Analyzes each depth level using surface analysis

### Usage
```bash
python example_02_depth_slices.py
```

### Key Features
```python
# Extract 2D slice at 100m from 3D data
obs_2d = extract_depth_level(
    obs_3d,
    varname='temperature',
    depth_coord='depth',
    target_depth=100.0,
    tolerance=15.0
)

# Then analyze as a surface field
analyzer.add_dataset_pair(
    obs_folder="./temp_slices",
    obs_pattern="obs_temp_100m.nc",
    model_folder="./temp_slices",
    model_pattern="model_temp_100m.nc",
    variable="temp_100m",
)
```

### When to Use This vs Full 3D
- ✅ Use depth slices when you only care about specific levels
- ✅ Faster than full 3D analysis
- ✅ Can compare specific features (e.g., thermocline depth)
- ❌ Don't use if you need vertical profiles or depth-dependent statistics

### Output
```
temp_at_100m_output/
├── statistics_report.txt
└── temp_100m/
    ├── spatial_maps.png      # Temperature at 100m
    ├── time_series.png
    └── statistics.png

temp_at_500m_output/
├── statistics_report.txt
└── temp_500m/
    └── ...
```

## Example 3: Full 3D Depth-Resolved Analysis

**File**: `example_03_3d_profiles.py`

Complete vertical profile analysis with depth-dependent statistics.

### When to Use
- Full water column analysis
- Depth-dependent bias/RMSE needed
- Comparing vertical structure
- Analyzing thermocline, halocline
- Mixed layer depth validation
- Any analysis requiring vertical profiles

### What It Does
1. Loads 3D data with any vertical coordinate system
2. Interpolates from sigma to standard depth levels
3. Regrids horizontally at each depth
4. Computes statistics at each depth level
5. Creates vertical profile plots and depth sections

### Usage
```bash
python example_03_3d_profiles.py
```

### Key Features
```python
analyzer = DepthResolvedAnalyzer(output_dir="./3d_output")

# Define standard depths
depths = np.concatenate([
    np.arange(0, 50, 5),      # High resolution near surface
    np.arange(50, 500, 25),   # Medium resolution thermocline
    np.arange(500, 2000, 100), # Coarser deep ocean
])

analyzer.add_dataset_pair(
    obs_folder="./data/obs",
    obs_pattern="temp_*.nc",
    model_folder="./data/model",
    model_pattern="ocean_*.nc",
    variable="temperature",
    standard_depths=depths,
)

analyzer.load_all_data()
analyzer.interpolate_to_standard_depths(depths, method='linear')
analyzer.regrid_horizontal(method='conservative')
analyzer.compute_statistics()
```

### Handles Sigma Coordinates
The module automatically handles:
- 1D depth coordinates (z-level): `depth = [0, 10, 20, ...]`
- 3D depth coordinates (sigma): `depth(z, y, x)`
- 4D time-varying sigma: `depth(t, z, y, x)`

### Output
```
3d_analysis_output/
├── statistics_report.txt
└── temperature/
    ├── depth_profiles.png           # Bias, RMSE, corr vs depth
    ├── vertical_statistics.png      # Mean and std profiles
    ├── depth_sections.png           # Depth-longitude sections
    ├── temperature_regridded.nc     # Full 3D regridded data
    └── temperature_depth_statistics.nc  # Stats at each level
```

## Comparison: Depth Slices vs Full 3D

| Feature | Depth Slices (Ex. 2) | Full 3D (Ex. 3) |
|---------|---------------------|-----------------|
| Specific depths only | ✅ Yes | ❌ No, all depths |
| Vertical profiles | ❌ No | ✅ Yes |
| Depth sections | ❌ No | ✅ Yes |
| Speed | ✅ Fast | ❌ Slower |
| Memory | ✅ Low | ❌ Higher |
| Best for | Few specific levels | Full water column |

## Workflow Comparison

### Surface Analysis Workflow
```
Load 2D files → Regrid horizontally → Statistics → Plots
```

### Depth Slice Workflow  
```
Load 3D files → Extract at depth → Save 2D → Surface workflow
```

### Full 3D Workflow
```
Load 3D files → Interpolate vertically → Regrid horizontally → 
Depth statistics → Depth plots
```

## File Organization Recommendations

### Option 1: Separate by Variable
```
data/
├── observations/
│   ├── sst/
│   │   ├── sst_2020.nc
│   │   └── sst_2021.nc
│   └── profiles/
│       ├── temp_profile_2020.nc
│       └── salt_profile_2020.nc
└── model/
    ├── surface/
    │   └── model_sst_*.nc
    └── 3d_output/
        └── ocean_*.nc
```

### Option 2: Separate by Time Period
```
data/
├── observations/
│   ├── 2020/
│   │   ├── sst.nc
│   │   ├── temp_profiles.nc
│   │   └── salt_profiles.nc
│   └── 2021/
│       └── ...
└── model/
    └── ...
```

## Common Use Cases

### Case 1: Model Validation Report
Need overall model performance across multiple variables:

```bash
# Surface validation
python example_01_surface_sst.py   # SST analysis
# Modify for SSH, surface salinity, etc.

# Subsurface validation
python example_03_3d_profiles.py   # Temperature and salinity profiles
```

### Case 2: Mixed Layer Depth Study
Compare temperature at specific levels:

```bash
python example_02_depth_slices.py  # Analyze at 10m, 20m, 50m, 100m
```

### Case 3: Thermocline Analysis
Need full vertical structure:

```bash
python example_03_3d_profiles.py  # With high resolution 0-200m
```

### Case 4: Multi-Year Comparison
All examples support wildcards:

```python
obs_pattern="sst_*.nc"           # Processes all years automatically
obs_pattern="sst_202*.nc"        # Only 2020-2029
obs_pattern="sst_2020.nc sst_2021.nc"  # Specific files
```

## Customization Guide

### Adding More Variables

**Surface variable:**
```python
analyzer.add_dataset_pair(
    obs_folder="./data/obs",
    obs_pattern="chl_*.nc",
    model_folder="./data/model",
    model_pattern="model_chl_*.nc",
    variable="chlorophyll",
    variable_label="Surface Chlorophyll",
)
```

**Multiple depths:**
```python
for depth in [50, 100, 200, 500]:
    # Extract and analyze (see example 2)
    ...
```

**3D variable:**
```python
analyzer.add_dataset_pair(
    obs_folder="./data/obs",
    obs_pattern="velocity_*.nc",
    model_folder="./data/model",  
    model_pattern="ocean_vel_*.nc",
    variable="velocity_u",
    standard_depths=depths,
)
```

### Changing Regridding Methods

```python
# Fast for testing
analyzer.regrid_data(method='nearest')

# Smooth interpolation
analyzer.regrid_data(method='bilinear')

# Mass-conserving (requires xESMF)
analyzer.regrid_data(method='conservative')

# Highest accuracy (requires xESMF)
analyzer.regrid_data(method='patch')
```

### Changing Regridding Direction

```python
# Standard: compare on model grid
direction='obs_to_model'

# Alternative: compare on observation grid (useful for multi-model comparison)
direction='model_to_obs'
```

### Custom Standard Depths

```python
# High resolution upper ocean
depths = np.arange(0, 100, 2)

# Variable resolution
depths = np.concatenate([
    np.arange(0, 50, 1),      # 1m: surface
    np.arange(50, 200, 5),    # 5m: thermocline
    np.arange(200, 1000, 50), # 50m: deep
])

# Specific observational levels
depths = np.array([0, 10, 20, 30, 50, 75, 100, 150, 200, 300, 500, 1000])
```

## Tips and Best Practices

### 1. Start Simple
```python
# First: test with one year, nearest neighbor
obs_pattern="sst_2020.nc"
method='nearest'

# Then: add more years, better method
obs_pattern="sst_*.nc"
method='conservative'
```

### 2. Check Your Data First
```python
import xarray as xr
ds = xr.open_dataset("your_file.nc")
print(ds)  # See available variables and coordinates
print(ds['temperature'].dims)  # Check dimensions
```

### 3. Memory Management
For large 3D datasets:
- Process fewer years at once
- Use coarser standard depths for testing
- Process variables separately

### 4. Validate Depth Extraction
For depth slices, always check:
```python
print(f"Requested depth: {target_depth}m")
print(f"Actual depth: {actual_depth}m")  
print(f"Difference: {abs(target_depth - actual_depth)}m")
```

### 5. Choose the Right Tool
- Need quick validation at one depth? → Example 2
- Need full water column analysis? → Example 3
- Only surface data? → Example 1

## Troubleshooting

### Problem: "Could not auto-detect variable"
**Solution**: Specify manually:
```python
obs_varname="thetao",  # Your exact variable name
model_varname="temp",
```

### Problem: "Could not identify depth coordinate"  
**Solution**: Specify depth coordinate:
```python
obs_depth="depth",     # or "z", "lev", "s_rho", etc.
model_depth="s_rho",
```

### Problem: Depth slice gives NaN values
**Cause**: Target depth outside data range or tolerance too strict
**Solution**:
```python
tolerance=20.0  # Increase tolerance
# Or check actual depth range:
print("Obs depths:", obs_ds['depth'].values)
```

### Problem: Memory error in 3D analysis
**Solutions**:
- Use fewer standard depths
- Process fewer time steps
- Use `method='nearest'` instead of `linear`/`cubic`

## Performance Guidelines

| Dataset Size | Example | Expected Time | Memory |
|--------------|---------|---------------|---------|
| 1 year SST | Example 1 | < 1 min | < 1 GB |
| 10 years SST | Example 1 | ~ 5 min | ~ 2 GB |
| 1 depth level | Example 2 | ~ 2 min | ~ 1 GB |
| 5 depth levels | Example 2 | ~ 10 min | ~ 2 GB |
| 1 year 3D (50 levels) | Example 3 | ~ 10 min | ~ 5 GB |
| 5 years 3D (100 levels) | Example 3 | ~ 60 min | ~ 20 GB |

*Times are approximate for regional domain (~500x500 grid points)*

## Further Reading

- **Surface analysis details**: See `multi_variable_analysis.py` README
- **3D analysis details**: See `depth_resolved_analysis.py` README  
- **xESMF documentation**: https://xesmf.readthedocs.io/
- **xarray documentation**: https://docs.xarray.dev/

## Getting Help

1. Check the appropriate example script
2. Review the module README
3. Inspect your data with `xr.open_dataset()`
4. Start with simpler methods (nearest, fewer files)
5. Check coordinate names match your data
