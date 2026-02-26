# Multi-Variable Analysis Script with Advanced Regridding

Enhanced version of the SST analysis script featuring:
- **Multiple files per variable** using wildcards (e.g., `sst_*.nc`)
- **Advanced regridding methods** via xESMF (conservative, patch, etc.)
- **Bidirectional regridding** - obs→model or model→obs
- **Multiple variables** (SST, salinity, SSH, velocity, chlorophyll, etc.)
- **Automatic variable detection** with manual override options

## Key Features

### 1. Advanced Regridding Methods

#### Scipy-based (no extra dependencies):
- **nearest**: Nearest neighbor interpolation (fast, simple)
- **bilinear**: Bilinear interpolation (smooth, moderate accuracy)

#### xESMF-based (requires xESMF installation):
- **conservative**: Conservative regridding (preserves field integrals, ideal for extensive properties)
- **conservative_normed**: Normalized conservative (recommended for flux variables)
- **patch**: Patch recovery method (higher-order accuracy)

### 2. Bidirectional Regridding

Choose which grid to perform the comparison on:
- **obs_to_model** (default): Regrid observations to model grid
- **model_to_obs**: Regrid model to observation grid

This is crucial when:
- Observation grid is much finer than model grid
- Model grid is irregular and you want regular output
- You want to preserve specific grid characteristics

### 3. Multiple Files Per Variable with Wildcards
- Process yearly, monthly, or arbitrary file splits automatically
- Use wildcard patterns or space-separated lists
- Automatic time-dimension concatenation

## Installation

### Basic Installation (scipy methods only)
```bash
pip install numpy xarray matplotlib seaborn scipy pandas
```

### Full Installation (including xESMF for conservative regridding)
```bash
pip install numpy xarray matplotlib seaborn scipy pandas xesmf
```

**Note**: xESMF requires additional dependencies (ESMF library). See [xESMF installation guide](https://xesmf.readthedocs.io/en/latest/installation.html).

## Quick Start Examples

### Example 1: Conservative Regridding (Recommended)
```bash
python multi_variable_analysis.py \
    --obs-folder ./observations \
    --obs-pattern "sst_*.nc" \
    --model-folder ./model_output \
    --model-pattern "model_sst_*.nc" \
    --variable sst \
    --regrid-method conservative \
    --regrid-direction obs_to_model
```

### Example 2: Model to Observation Grid
Useful when obs grid is regular and you want output on that grid:
```bash
python multi_variable_analysis.py \
    --obs-folder ./obs \
    --obs-pattern "*.nc" \
    --model-folder ./model \
    --model-pattern "*.nc" \
    --variable sst \
    --regrid-method conservative \
    --regrid-direction model_to_obs
```

### Example 3: Fast Analysis with Nearest Neighbor
```bash
python multi_variable_analysis.py \
    --obs-folder ./obs \
    --obs-pattern "sst_*.nc" \
    --model-folder ./model \
    --model-pattern "*.nc" \
    --variable sst \
    --regrid-method nearest \
    --regrid-direction obs_to_model
```

### Example 4: Multiple Variables with Different Methods
```bash
# SST with conservative, salinity with patch recovery
python multi_variable_analysis.py \
    --obs-folder ./obs/sst ./obs/sal \
    --obs-pattern "sst_*.nc" "sal_*.nc" \
    --model-folder ./model/sst ./model/sal \
    --model-pattern "sst_*.nc" "sal_*.nc" \
    --variable sst salinity \
    --regrid-method conservative \
    --regrid-direction obs_to_model
```

## Regridding Method Selection Guide

### When to Use Each Method

**Nearest Neighbor** (`nearest`)
- ✓ Fastest method
- ✓ Good for quick exploratory analysis
- ✓ Preserves sharp boundaries
- ✗ Can introduce step artifacts
- ✗ Less accurate for smooth fields
- **Use for**: Quick tests, categorical data, preliminary analysis

**Bilinear** (`bilinear`)
- ✓ Smooth interpolation
- ✓ Reasonable speed
- ✓ Good for continuous fields
- ✗ Doesn't preserve integrals
- ✗ Can create overshoots/undershoots
- **Use for**: Temperature, smooth scalar fields

**Conservative** (`conservative`)
- ✓ Preserves field integrals (total mass, energy, etc.)
- ✓ Physically consistent for extensive properties
- ✓ No overshoots if source data has none
- ✗ Slower than nearest/bilinear
- ✗ Requires xESMF
- **Use for**: Heat content, precipitation totals, any integrated quantity

**Conservative Normed** (`conservative_normed`)
- ✓ All benefits of conservative
- ✓ Better for flux variables
- ✓ Normalizes by grid cell areas
- ✗ Slower than nearest/bilinear
- ✗ Requires xESMF
- **Use for**: Flux variables (heat flux, momentum flux), normalized quantities

**Patch Recovery** (`patch`)
- ✓ Highest accuracy
- ✓ Higher-order interpolation
- ✓ Smooth fields
- ✗ Slowest method
- ✗ Requires xESMF
- ✗ Can create small overshoots
- **Use for**: When accuracy is critical, smooth fields with fine structure

### Recommended Methods by Variable Type

| Variable Type | Recommended Method | Alternative |
|--------------|-------------------|-------------|
| Temperature (SST, air temp) | `conservative` | `bilinear` |
| Salinity | `conservative` | `bilinear` |
| Sea Surface Height | `bilinear` | `patch` |
| Velocity (U, V) | `conservative` | `patch` |
| Heat Flux | `conservative_normed` | `conservative` |
| Precipitation | `conservative` | `conservative_normed` |
| Chlorophyll | `conservative` | `bilinear` |

## Bidirectional Regridding Guide

### Obs to Model Grid (`obs_to_model`)

**When to use:**
- Model grid is the reference for validation
- You want to see model performance on its native grid
- Model grid is coarser (faster computation)
- Standard model validation workflow

**Example use case:**
Comparing high-resolution satellite observations (0.05°) with coarse model output (0.25°)

```bash
python multi_variable_analysis.py \
    --obs-folder ./satellite \
    --obs-pattern "*.nc" \
    --model-folder ./model \
    --model-pattern "*.nc" \
    --variable sst \
    --regrid-direction obs_to_model \
    --regrid-method conservative
```

### Model to Obs Grid (`model_to_obs`)

**When to use:**
- Observation grid is regular and well-suited for analysis
- Model grid is irregular or curvilinear
- You want outputs on a standard lat/lon grid
- Comparing multiple models on a common observation grid

**Example use case:**
Comparing an irregular ocean model grid with regular satellite observations (1°)

```bash
python multi_variable_analysis.py \
    --obs-folder ./satellite \
    --obs-pattern "*.nc" \
    --model-folder ./model \
    --model-pattern "*.nc" \
    --variable sst \
    --regrid-direction model_to_obs \
    --regrid-method conservative
```

## Command Line Arguments

### Required Arguments
- `--obs-folder`: Folder(s) containing observed data files
- `--obs-pattern`: File pattern(s) for observed files
- `--model-folder`: Folder(s) containing model data files
- `--model-pattern`: File pattern(s) for model files
- `--variable`: Variable identifier(s)

### Optional Arguments
- `--obs-var`: Variable names in observed files (auto-detected)
- `--model-var`: Variable names in model files (auto-detected)
- `--variable-label`: Display labels for plots
- `--output-dir`: Output directory (default: `./analysis_output`)
- `--regrid-method`: Regridding method (default: `nearest`)
  - Choices: `nearest`, `bilinear`, `conservative`, `conservative_normed`, `patch`
- `--regrid-direction`: Regridding direction (default: `obs_to_model`)
  - Choices: `obs_to_model`, `model_to_obs`
- `--inspect`: Only inspect files, don't run analysis

## Advanced Examples

### Example 1: High-Accuracy Analysis
For publication-quality results:
```bash
python multi_variable_analysis.py \
    --obs-folder /data/obs \
    --obs-pattern "OISST_*.nc" \
    --model-folder /data/model \
    --model-pattern "NEMO_*.nc" \
    --variable sst \
    --regrid-method patch \
    --regrid-direction obs_to_model \
    --output-dir ./high_accuracy_analysis
```

### Example 2: Multi-Model Comparison on Common Grid
Compare multiple models on observation grid:
```bash
# Model 1
python multi_variable_analysis.py \
    --obs-folder /obs \
    --obs-pattern "*.nc" \
    --model-folder /models/model1 \
    --model-pattern "*.nc" \
    --variable sst \
    --regrid-method conservative \
    --regrid-direction model_to_obs \
    --output-dir ./comparison/model1

# Model 2
python multi_variable_analysis.py \
    --obs-folder /obs \
    --obs-pattern "*.nc" \
    --model-folder /models/model2 \
    --model-pattern "*.nc" \
    --variable sst \
    --regrid-method conservative \
    --regrid-direction model_to_obs \
    --output-dir ./comparison/model2
```

### Example 3: Multiple Variables, Multiple Years
```bash
python multi_variable_analysis.py \
    --obs-folder /obs/sst /obs/ssh \
    --obs-pattern "sst_20*.nc" "ssh_20*.nc" \
    --model-folder /model/output /model/output \
    --model-pattern "sst_*.nc" "ssh_*.nc" \
    --variable sst ssh \
    --variable-label "Sea Surface Temperature" "Sea Surface Height" \
    --regrid-method conservative bilinear \
    --regrid-direction obs_to_model
```

### Example 4: Flux Variable Analysis
For heat flux or momentum flux:
```bash
python multi_variable_analysis.py \
    --obs-folder /obs/flux \
    --obs-pattern "heatflux_*.nc" \
    --model-folder /model/flux \
    --model-pattern "*.nc" \
    --variable heatflux \
    --obs-var surface_heat_flux \
    --model-var hfds \
    --regrid-method conservative_normed \
    --regrid-direction obs_to_model
```

## Output Structure

```
analysis_output/
├── statistics_report.txt          # Includes regridding method and direction
├── sst/
│   ├── spatial_maps.png           # Maps on comparison grid
│   ├── time_series.png
│   ├── statistics.png
│   ├── sst_regridded.nc           # Regridded dataset
│   └── sst_spatial_statistics.nc  # Bias, RMSE, correlation on comparison grid
└── ...
```

## Performance Considerations

### Speed Comparison (approximate, relative)

| Method | Relative Speed | Accuracy | Memory |
|--------|----------------|----------|--------|
| nearest | 1.0× (fastest) | Low | Low |
| bilinear | 1.5× | Medium | Low |
| conservative | 3-5× | High | Medium |
| conservative_normed | 3-5× | High | Medium |
| patch | 5-10× (slowest) | Very High | Medium-High |

### Tips for Large Datasets

1. **Start with nearest**: Test your workflow with `--regrid-method nearest` first
2. **Use conservative for final analysis**: Switch to `conservative` for production runs
3. **Process subset first**: Test on one year before processing decades
4. **Consider regridding direction**: 
   - Regridding to coarser grid is faster
   - `obs_to_model` is typically faster if model grid is coarser
5. **xESMF caching**: xESMF creates weight files that speed up repeated regridding

## Troubleshooting

### xESMF Installation Issues

**Problem**: `ImportError: No module named 'xesmf'`
**Solution**: 
```bash
conda install -c conda-forge xesmf  # Recommended
# or
pip install xesmf
```

**Problem**: ESMF library not found
**Solution**: Use conda to install (handles ESMF automatically):
```bash
conda install -c conda-forge esmf xesmf
```

### Regridding Errors

**Problem**: "Could not build regridder"
**Solution**: 
- Check that lat/lon coordinates are properly detected
- Use `--inspect` to verify coordinate names
- Ensure grids overlap geographically
- Check for NaN values in coordinates

**Problem**: Conservative regridding produces all NaN
**Solution**:
- Grid cells may not overlap
- Check coordinate units (degrees vs radians)
- Verify longitude convention (-180 to 180 vs 0 to 360)

**Problem**: Memory error during regridding
**Solution**:
- Use `nearest` or `bilinear` (lower memory)
- Process fewer files at once
- Use a machine with more RAM
- Consider processing variables separately

### Method-Specific Issues

**Conservative methods produce unexpected results**:
- Check if your variable is intensive vs extensive
- Try `conservative_normed` for flux variables
- Verify grid cell area calculations are correct

**Patch method creates oscillations**:
- Normal for sharp gradients
- Consider using `conservative` instead
- Or apply post-processing smoothing

## Grid Compatibility

### Supported Grid Types

Both obs and model can be on any of these grids:

**Regular Grids**:
- 1D lat/lon coordinates
- Evenly or unevenly spaced
- Example: Standard satellite products

**Curvilinear Grids**:
- 2D lat/lon arrays
- Example: Tripolar ocean models, rotated grids

**Unstructured Grids**:
- Not currently supported (xESMF limitation)

### Coordinate Requirements

- Coordinates must be in degrees
- Longitude can be -180 to 180 or 0 to 360 (auto-handled)
- Grids must have some geographical overlap

## Best Practices

### 1. Always Inspect First
```bash
python multi_variable_analysis.py --inspect \
    --obs-folder ./obs --obs-pattern "*.nc" \
    --model-folder ./model --model-pattern "*.nc" \
    --variable sst
```

### 2. Start Simple, Then Refine
```bash
# Step 1: Quick test with nearest
--regrid-method nearest

# Step 2: Better accuracy with bilinear  
--regrid-method bilinear

# Step 3: Publication quality with conservative
--regrid-method conservative
```

### 3. Choose Direction Wisely
- Default (`obs_to_model`): Good for most model validation
- Use `model_to_obs`: When you need regular grid output or comparing multiple models

### 4. Document Your Methods
The statistics report automatically includes:
- Regridding method used
- Regridding direction
- All file paths

### 5. Validate Regridding
Check the regridded NetCDF files:
```bash
ncdump -h analysis_output/sst/sst_regridded.nc
```

## Comparison with Original Script

| Feature | Original | Enhanced |
|---------|----------|----------|
| Files per variable | Single | Multiple (wildcards) |
| Regridding methods | Nearest only | 5 methods (scipy + xESMF) |
| Regridding direction | Obs→Model only | Bidirectional |
| File specification | Direct paths | Folder + pattern |
| Variables | SST only | Any variable |
| Conservative regridding | No | Yes (via xESMF) |

## Citation and References

If you use conservative regridding (xESMF), please cite:
- xESMF: [Zhuang et al., 2020](https://joss.theoj.org/papers/10.21105/joss.02042)
- ESMF: [Hill et al., 2004](https://www.earthsystemcog.org/projects/esmf/)

## License

Modify and use freely for scientific research.

## Support and Resources

- **xESMF Documentation**: https://xesmf.readthedocs.io/
- **ESMF Documentation**: https://earthsystemmodeling.org/docs/
- **xarray Documentation**: https://docs.xarray.dev/

For issues:
1. Check coordinate detection with `--inspect`
2. Test with `--regrid-method nearest` first
3. Verify xESMF installation for conservative methods
4. Check that grids have geographical overlap
