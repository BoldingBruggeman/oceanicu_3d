# Quick Start Guide for Your Data

Based on your file inspection, here's how to run the analysis:

## Your Data Structure

**Observed Data** (`/data/CCI-SST/sst_2020.nc`):
- Variable: `analysed_sst`
- Coordinates: `latitude`, `longitude`
- Units: **Kelvin** (will be auto-converted to Celsius)
- Grid: 800 x 1500 regular grid
- Time steps: 367

**Model Data** (`/data/kb/NS/test/2020/ns_surface_daily.nc`):
- Variable: `temp`
- Coordinates: `latt`, `lont` (2D curvilinear grid)
- Units: **Celsius**
- Grid: 125 x 112 curvilinear grid
- Time steps: 32

## Command to Run

```bash
python sst_analysis.py \
    /data/CCI-SST/sst_2020.nc \
    /data/kb/NS/test/2020/ns_surface_daily.nc \
    --obs-var analysed_sst \
    --obs-lat latitude \
    --obs-lon longitude \
    --model-var temp \
    --model-lat latt \
    --model-lon lont \
    --output-dir ./sst_comparison_results
```

## What the Script Does

1. **Loads both files** and auto-detects that:
   - Observed data is in Kelvin → converts to Celsius
   - Model data is already in Celsius → no conversion needed

2. **Regrids observations** from the 800x1500 regular grid to the 125x112 curvilinear model grid

3. **Finds common time periods** (32 days in January 2020)

4. **Computes statistics**:
   - Bias, RMSE, MAE
   - Spatial and temporal correlations
   - Monthly climatology
   - Spatial patterns of errors

5. **Creates 7 plots**:
   - Scatter plot
   - Time series comparison
   - Monthly climatology
   - Spatial maps (bias, RMSE, correlation)
   - Distribution comparisons
   - Taylor diagram
   - Q-Q plot

6. **Saves outputs**:
   - All plots as PNG files (300 DPI)
   - Statistics report as text file
   - Regridded observations as NetCDF
   - Spatial statistics as NetCDF

## Expected Results

With your data, you should expect:
- Analysis of 32 common time steps (overlapping period)
- Regridded observations on 125x112 grid
- All temperatures in Celsius (no 274°C bias!)
- Valid statistical comparisons

## Key Improvements in Updated Script

✅ **Automatic unit conversion** (Kelvin → Celsius)
✅ **Proper handling of 2D curvilinear grids**
✅ **Correct dimension alignment** (no more shape mismatch errors)
✅ **Better error messages** with detailed debugging info
✅ **Enhanced coordinate detection** for various grid types

## Common Issues Resolved

### Issue 1: Unit Mismatch (FIXED)
- Old: -274°C bias due to Kelvin vs Celsius
- New: Auto-converts Kelvin to Celsius

### Issue 2: Shape Mismatch (FIXED)
- Old: Different array shapes after regridding
- New: Proper dimension handling for curvilinear grids

### Issue 3: Coordinate Detection (FIXED)
- Old: Couldn't find `latt`, `lont` coordinates
- New: Extended coordinate name list + manual specification

## Output Directory Structure

```
sst_comparison_results/
├── scatter_plot.png
├── timeseries.png
├── monthly_climatology.png
├── spatial_maps.png
├── distributions.png
├── taylor_diagram.png
├── qq_plot.png
├── statistics_report.txt
├── obs_regridded.nc
└── spatial_statistics.nc
```

## Verification Steps

After running, check:

1. **Statistics report** should show:
   - Bias: ~0-2°C (reasonable for regional model)
   - Observed mean: ~8-9°C (North Sea winter temperatures)
   - Model mean: ~8-9°C (similar)

2. **Scatter plot** should show:
   - Points clustered around 1:1 line
   - Temperature range 0-15°C (typical for North Sea)

3. **Spatial maps** should show:
   - Bias patterns related to coastal features
   - Lower RMSE in open ocean
   - Higher correlation in stable regions

## Next Steps

If you want to:
- **Analyze multiple years**: Create a loop to process each year
- **Focus on specific regions**: Subset the data before analysis
- **Add custom metrics**: Extend the `CustomSSTAnalyzer` class (see example_usage.py)
- **Compare multiple models**: Run the script multiple times with different model files

## Need Help?

If you encounter any issues:
1. Run with `--inspect` to verify file structure
2. Check that coordinates match what's shown in inspection
3. Verify time overlap exists between files
4. Look at the statistics_report.txt for diagnostic info
