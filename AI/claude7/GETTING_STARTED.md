# Getting Started - Ocean Model Analysis Toolkit

## 📁 What You Downloaded

Your download contains the complete toolkit with this structure:

```
ocean_model_analysis/
│
├── PROJECT_SUMMARY.md                    # Overview of entire toolkit
├── README.md                             # Documentation for surface analysis
├── multi_variable_analysis.py            # Main module: Surface/2D analysis
│
├── examples/                             # ⭐ START HERE - Working examples
│   ├── README.md                         # Detailed usage guide
│   ├── example_01_surface_sst.py         # Example: SST analysis
│   ├── example_02_depth_slices.py        # Example: Fields at depths
│   └── example_03_3d_profiles.py         # Example: Full 3D analysis
│
└── depth_analysis/                       # 3D depth-resolved analysis
    ├── README.md                         # Quick start for 3D analysis
    ├── depth_resolved_analysis.py        # Module: Full 3D analysis
    └── multi_variable_analysis.py        # Copy for convenience
```

**Total: 10 files across 3 folders**

## 🚀 Quick Start (3 Steps)

### Step 1: Choose Your Analysis Type

| What You Have | Which Example | Which Module |
|---------------|---------------|--------------|
| Surface data (SST, SSH) | `example_01_surface_sst.py` | `multi_variable_analysis.py` |
| 3D data, want specific depths | `example_02_depth_slices.py` | `multi_variable_analysis.py` |
| 3D data, want full profiles | `example_03_3d_profiles.py` | `depth_resolved_analysis.py` |

### Step 2: Copy and Modify Example

```bash
# Copy the appropriate example
cp examples/example_01_surface_sst.py my_analysis.py

# Edit to point to your data
nano my_analysis.py  # or use your preferred editor
```

### Step 3: Run Your Analysis

```bash
python my_analysis.py
```

## 📖 Documentation Guide

Read in this order:

1. **This file (GETTING_STARTED.md)** - You are here! ✓
2. **`examples/README.md`** - Detailed comparison of all three approaches
3. **Example script for your use case** - See working code
4. **Module README** - Deep dive into specific module

## 🎯 Example Modifications

### Surface SST Analysis

**Original:**
```python
analyzer.add_dataset_pair(
    obs_folder="./data/observations/sst",
    obs_pattern="sst_*.nc",
    model_folder="./data/model/surface",
    model_pattern="model_sst_*.nc",
    variable="sst",
)
```

**Modified for your data:**
```python
analyzer.add_dataset_pair(
    obs_folder="/path/to/your/obs",
    obs_pattern="OISST_*.nc",
    model_folder="/path/to/your/model",
    model_pattern="ROMS_sst_*.nc",
    variable="sst",
)
```

### Multiple Depth Levels

**Original:**
```python
depths_to_analyze = [50, 100, 200, 500]
```

**Modified:**
```python
depths_to_analyze = [10, 30, 50, 75, 100]  # Your depths
```

### Full 3D with Custom Depths

**Original:**
```python
standard_depths = np.concatenate([
    np.arange(0, 50, 5),
    np.arange(50, 500, 25),
])
```

**Modified:**
```python
# High resolution in upper 100m for mixed layer analysis
standard_depths = np.concatenate([
    np.arange(0, 20, 1),      # 1m resolution: 0-20m
    np.arange(20, 100, 5),    # 5m resolution: 20-100m
    np.arange(100, 500, 25),  # 25m resolution: 100-500m
])
```

## 📂 Organizing Your Data

Recommended structure for your data:

```
your_project/
│
├── data/
│   ├── observations/
│   │   ├── sst/
│   │   │   ├── sst_2020.nc
│   │   │   ├── sst_2021.nc
│   │   │   └── sst_2022.nc
│   │   └── profiles/
│   │       ├── temp_2020.nc
│   │       └── salt_2020.nc
│   └── model/
│       ├── surface/
│       │   └── model_sst_*.nc
│       └── 3d/
│           └── ocean_*.nc
│
├── ocean_model_analysis/          # ← This downloaded toolkit
│   ├── multi_variable_analysis.py
│   ├── examples/
│   └── depth_analysis/
│
├── my_analysis.py                 # ← Your analysis script
└── output/                        # ← Results go here
```

## 🔧 Installation Requirements

### Minimum (for surface analysis):
```bash
pip install numpy xarray matplotlib seaborn scipy pandas
```

### Full (for conservative regridding):
```bash
pip install numpy xarray matplotlib seaborn scipy pandas xesmf
```

### Check installation:
```python
import xarray as xr
import numpy as np
print("Basic installation OK!")

try:
    import xesmf as xe
    print("xESMF installed - conservative regridding available!")
except:
    print("xESMF not installed - use 'nearest' or 'bilinear' methods")
```

## 🎓 Learning Path

### Beginner Path
1. Start with `example_01_surface_sst.py`
2. Modify for your SST data
3. Try different regridding methods
4. Explore the outputs

### Intermediate Path
1. Use `example_02_depth_slices.py`
2. Extract fields at depths you care about
3. Compare multiple depth levels
4. Understand subsurface validation

### Advanced Path
1. Use `example_03_3d_profiles.py`
2. Set up standard depths for your domain
3. Handle sigma coordinates
4. Analyze full water column

## ⚡ Common First-Time Issues

### Issue 1: "Could not auto-detect variable"
**Solution:** Specify variable names explicitly:
```python
obs_varname="analysed_sst",  # Exact name in your file
model_varname="temp",         # Exact name in your file
```

### Issue 2: "Could not identify lat/lon coordinates"
**Solution:** Specify coordinate names:
```python
obs_lat="latitude",   # Your coordinate name
obs_lon="longitude",
model_lat="lat_rho",  # ROMS uses lat_rho
model_lon="lon_rho",
```

### Issue 3: "No files found matching pattern"
**Check:**
```python
# Test your pattern in Python first
import glob
files = glob.glob("/path/to/your/data/sst_*.nc")
print(f"Found {len(files)} files:", files[:3])
```

### Issue 4: "Memory error"
**Solutions:**
- Use fewer years: `sst_2020.nc` instead of `sst_*.nc`
- Use coarser depths: `np.arange(0, 1000, 50)` instead of `np.arange(0, 1000, 5)`
- Use `method='nearest'` instead of `'conservative'`

## 📊 Understanding Outputs

### Surface/Depth Slice Analysis Output:
```
output/
├── statistics_report.txt          # Text summary
└── sst/
    ├── spatial_maps.png           # 4-panel: obs, model, bias, RMSE
    ├── time_series.png            # Time evolution
    ├── statistics.png             # Scatter, histograms, Q-Q plot
    ├── sst_regridded.nc          # Regridded data (NetCDF)
    └── sst_spatial_statistics.nc  # Statistics (NetCDF)
```

### 3D Analysis Output:
```
output/
├── statistics_report.txt
└── temperature/
    ├── depth_profiles.png         # Bias/RMSE/correlation vs depth
    ├── vertical_statistics.png    # Mean and std profiles
    ├── depth_sections.png         # Depth-longitude sections
    └── temperature_regridded.nc   # Full 3D regridded data
```

## 🎨 Customizing Plots

All examples produce plots automatically. To customize:

```python
# After running the analysis, load and plot your way
import xarray as xr
import matplotlib.pyplot as plt

# Load regridded data
ds = xr.open_dataset("output/sst/sst_regridded.nc")

# Custom plot
plt.figure(figsize=(12, 6))
ds['sst'].isel(time=0).plot()
plt.title("My Custom Plot")
plt.savefig("custom_plot.png")
```

## 🔄 Workflow Summary

```
1. Choose example script
   ↓
2. Copy to your project folder
   ↓
3. Modify file paths and patterns
   ↓
4. Specify variable/coordinate names if needed
   ↓
5. Run script
   ↓
6. Check outputs in specified output directory
   ↓
7. Review statistics_report.txt
   ↓
8. Examine visualizations (PNG files)
   ↓
9. Use regridded NetCDF for further analysis
```

## 💡 Pro Tips

1. **Start simple:** Use one year of data with `method='nearest'` first
2. **Inspect first:** Look at your NetCDF files with `ncdump -h file.nc`
3. **Test patterns:** Verify wildcards match with `glob.glob()`
4. **Read reports:** The `statistics_report.txt` has all key metrics
5. **Use inspect mode:** Some examples have inspection code

## 📞 Next Steps

After getting your first analysis working:

1. **Add more variables** - Copy the `add_dataset_pair()` block
2. **Try better methods** - Switch from `'nearest'` to `'conservative'`
3. **Analyze multiple years** - Use wildcards to include all files
4. **Compare directions** - Try `'model_to_obs'` regridding
5. **Customize outputs** - Modify visualization code in the modules

## 📚 Further Reading

- **`examples/README.md`** - Complete usage guide with comparisons
- **`README.md`** - Surface analysis documentation
- **`depth_analysis/README.md`** - 3D analysis documentation
- **Example scripts** - Well-commented working code

## ✅ Checklist for First Analysis

- [ ] Identified which example matches my data
- [ ] Copied example script to my project folder
- [ ] Updated folder paths to my data
- [ ] Updated file patterns (wildcards)
- [ ] Checked files are found with `glob.glob()`
- [ ] Installed required packages
- [ ] Specified variable names (if auto-detect fails)
- [ ] Specified coordinate names (if needed)
- [ ] Ran the script
- [ ] Checked output directory was created
- [ ] Reviewed statistics_report.txt
- [ ] Examined PNG plots
- [ ] Loaded NetCDF outputs

---

**You're ready to start! Pick an example and modify it for your data.** 🚀

For detailed documentation, see `examples/README.md`
