# Sea Surface Temperature Statistical Analysis Tool

A comprehensive Python tool for statistical analysis and comparison of observed and modeled Sea Surface Temperature (SST) data on different spherical grids.

## Features

### Data Handling
- **Automatic variable detection**: Intelligently identifies SST variables and coordinate names
- **Flexible grid support**: Handles both 1D and 2D coordinate arrays
- **Different grids**: Regrids observations to model grid using nearest-neighbor or bilinear interpolation
- **Time alignment**: Automatically finds and uses common time periods

### Statistical Metrics
- **Global Statistics**:
  - Bias (Model - Observed)
  - Root Mean Square Error (RMSE)
  - Mean Absolute Error (MAE)
  - Spatial and temporal correlations
  - Standard deviations
  - Quantile analysis (5%, 25%, 50%, 75%, 95%)

- **Monthly Statistics**:
  - Monthly climatology comparison
  - Monthly bias and RMSE

- **Spatial Statistics**:
  - Gridpoint-by-gridpoint bias maps
  - Gridpoint-by-gridpoint RMSE maps
  - Gridpoint-by-gridpoint correlation maps

### Visualizations
The tool generates 7 comprehensive plots:

1. **Scatter Plot**: Hexbin density plot of observed vs modeled SST with 1:1 line and regression
2. **Time Series**: Global mean SST time series and bias evolution
3. **Monthly Climatology**: Seasonal cycle comparison with monthly bias and RMSE
4. **Spatial Maps**: Geographic distribution of bias, RMSE, and correlation
5. **Distribution Comparison**: Histograms, CDFs, and box plots
6. **Taylor Diagram**: Comprehensive pattern statistics visualization
7. **Q-Q Plot**: Quantile-quantile comparison for distribution assessment

### Outputs
- High-resolution PNG plots (300 DPI)
- Comprehensive text report with all statistics
- NetCDF files with regridded observations and spatial statistics

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Command Line

Basic usage:
```bash
python sst_analysis.py /path/to/observed_sst.nc /path/to/model_sst.nc
```

With optional parameters:
```bash
python sst_analysis.py observed_sst.nc model_sst.nc \
    --obs-var sst \
    --model-var tos \
    --output-dir ./my_analysis \
    --regrid-method nearest
```

### Command Line Arguments

- `obs_file`: Path to observed SST NetCDF file (required)
- `model_file`: Path to modeled SST NetCDF file (required)
- `--obs-var`: Variable name in observed file (auto-detected if not provided)
- `--model-var`: Variable name in model file (auto-detected if not provided)
- `--output-dir`: Output directory (default: `./sst_analysis_output`)
- `--regrid-method`: Regridding method - `nearest` or `bilinear` (default: `nearest`)

### Python API

```python
from sst_analysis import SSTAnalyzer

# Create analyzer
analyzer = SSTAnalyzer(
    obs_file='path/to/observed.nc',
    model_file='path/to/model.nc',
    output_dir='./results'
)

# Load data (auto-detects variable names)
analyzer.load_data()

# Or specify variable names explicitly
analyzer.load_data(obs_varname='sst', model_varname='tos')

# Regrid observations to model grid
analyzer.regrid_observations(method='nearest')

# Compute comprehensive statistics
analyzer.compute_statistics()

# Create all visualizations
analyzer.create_visualizations()

# Save text report and NetCDF outputs
analyzer.save_statistics_report()
analyzer.save_netcdf_outputs()

# Access computed statistics
print(f"Global bias: {analyzer.stats['bias']:.4f} °C")
print(f"RMSE: {analyzer.stats['rmse']:.4f} °C")
print(f"Correlation: {analyzer.stats['correlation']:.4f}")
```

## Data Requirements

### NetCDF Files
Both observed and model files should:
- Be in NetCDF format
- Contain daily SST data
- Have a time dimension (must be named 'time' in both files)
- Have spatial coordinates (latitude/longitude)
- Cover at least one year of data

### Supported Coordinate Names

The tool automatically detects common coordinate naming conventions:

**Latitude**: `lat`, `latitude`, `y`, `nav_lat`, `LATITUDE`

**Longitude**: `lon`, `longitude`, `x`, `nav_lon`, `LONGITUDE`

**SST Variables**: `sst`, `SST`, `sea_surface_temperature`, `tos`, `analysed_sst`, `temp`, `temperature`

### Grid Types

- **1D coordinates**: Regular lat/lon grids (e.g., 0.25° x 0.25°)
- **2D coordinates**: Curvilinear grids (e.g., ORCA, tripolar)
- **Different resolutions**: Automatic regridding handles any combination

## Output Files

All outputs are saved to the specified output directory:

### Plots (PNG, 300 DPI)
- `scatter_plot.png` - Observed vs modeled scatter with regression
- `timeseries.png` - Global mean time series and bias evolution
- `monthly_climatology.png` - Seasonal cycle and monthly statistics
- `spatial_maps.png` - Geographic patterns of bias, RMSE, correlation
- `distributions.png` - Statistical distributions and box plots
- `taylor_diagram.png` - Pattern statistics summary
- `qq_plot.png` - Quantile-quantile comparison

### Data Files
- `statistics_report.txt` - Comprehensive text report with all metrics
- `obs_regridded.nc` - Regridded observations on model grid
- `spatial_statistics.nc` - Gridded bias, RMSE, and correlation fields

## Example Output

```
============================================================
STATISTICAL SUMMARY
============================================================
Number of samples: 12,345,678

Global Statistics:
  Bias (Model - Obs):       0.1234 °C
  RMSE:                     1.2345 °C
  MAE:                      0.9876 °C
  Spatial Correlation:      0.9500
  Temporal Correlation:     0.9800

Observed SST:
  Mean:                    18.5000 °C
  Std Dev:                  8.2000 °C
  Range:                   [-1.8000, 32.0000] °C

Modeled SST:
  Mean:                    18.6234 °C
  Std Dev:                  8.1500 °C
  Range:                   [-1.5000, 31.8000] °C
============================================================
```

## Algorithm Details

### Regridding Method

The tool uses a KD-tree based nearest-neighbor approach:

1. Normalizes longitudes to [-180, 180]
2. Builds a spatial index of observation points
3. For each model grid point, finds the nearest observation point
4. Applies the mapping to each time step

This approach:
- Handles any combination of regular/curvilinear grids
- Is computationally efficient for large datasets
- Preserves data characteristics without interpolation artifacts

### Statistical Computations

- **Bias**: Mean difference (Model - Observed)
- **RMSE**: Root of mean squared differences
- **Correlation**: Pearson correlation coefficient
- **Monthly statistics**: Group by calendar month and compute metrics
- **Spatial statistics**: Time-mean metrics at each grid point

## Performance Considerations

- For very large datasets (>1 GB), processing may take several minutes
- Regridding is the most time-intensive step
- Memory usage scales with the larger of the two grids
- Scatter plots are subsampled to 50,000 points for visualization

## Troubleshooting

### Common Issues

**"Could not auto-detect SST variable"**
- Solution: Specify variable names explicitly with `--obs-var` and `--model-var`

**"Could not identify lat/lon coordinates"**
- Solution: Check your NetCDF file coordinate names
- Ensure coordinates follow CF conventions or common naming patterns

**Memory errors**
- Solution: Process data in chunks or use a machine with more RAM
- Consider spatial subsetting before analysis

**Time mismatch warnings**
- The tool uses only overlapping time periods
- Check that both datasets cover the same time range

## Citation

If you use this tool in your research, please cite:

```
SST Statistical Analysis Tool (2024)
https://github.com/yourusername/sst-analysis
```

## License

MIT License - See LICENSE file for details

## Contact

For bug reports and feature requests, please open an issue on GitHub.

## Requirements

- Python 3.7+
- numpy >= 1.21.0
- xarray >= 2022.3.0
- scipy >= 1.7.0
- matplotlib >= 3.5.0
- seaborn >= 0.11.0
- pandas >= 1.3.0
- netCDF4 >= 1.5.8
