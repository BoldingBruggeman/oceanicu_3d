# Depth-Resolved Ocean Model Analysis

This module provides full 3D depth-resolved analysis of ocean model data.

## Files in This Directory

- **`depth_resolved_analysis.py`** - Core module for 3D analysis
- **`multi_variable_analysis.py`** - Copy of surface analysis module (for reference)

## Quick Start

```python
from depth_resolved_analysis import DepthResolvedAnalyzer
import numpy as np

# Initialize
analyzer = DepthResolvedAnalyzer(output_dir="./output")

# Define depths
depths = np.concatenate([
    np.arange(0, 50, 5),      # 5m resolution near surface
    np.arange(50, 500, 25),   # 25m resolution main thermocline
    np.arange(500, 2000, 100) # 100m resolution deep ocean
])

# Add dataset
analyzer.add_dataset_pair(
    obs_folder="./data/obs",
    obs_pattern="temp_*.nc",
    model_folder="./data/model",
    model_pattern="ocean_*.nc",
    variable="temperature",
    standard_depths=depths
)

# Run analysis
analyzer.load_all_data()
analyzer.interpolate_to_standard_depths(depths, method='linear')
analyzer.regrid_horizontal(method='conservative')
analyzer.compute_statistics()
analyzer.create_visualizations()
analyzer.save_statistics_report()
analyzer.save_netcdf_outputs()
```

## Features

- **Handles sigma coordinates** (ROMS, FVCOM, HYCOM)
- **Interpolates to standard depths**
- **Depth-dependent statistics** (bias, RMSE, correlation at each level)
- **Vertical profile plots**
- **Depth-longitude/latitude sections**
- **Works with irregular temporal frequencies**

## Output

```
output/
├── statistics_report.txt
└── temperature/
    ├── depth_profiles.png           # Bias, RMSE, correlation vs depth
    ├── vertical_statistics.png      # Mean and std dev profiles
    ├── depth_sections.png           # Depth-longitude sections
    └── temperature_regridded.nc     # Regridded 3D data
```

## See Also

- **Examples**: See `../examples/example_03_3d_profiles.py` for complete working example
- **Full documentation**: See `../examples/README.md` for detailed usage guide
- **Surface analysis**: Use `multi_variable_analysis.py` for 2D fields
