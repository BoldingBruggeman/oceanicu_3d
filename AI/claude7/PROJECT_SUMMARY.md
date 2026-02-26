# Ocean Model Analysis Toolkit - Complete Package

Complete toolkit for analyzing ocean model output against observations, supporting 2D surface fields, fields at specific depths, and full 3D depth-resolved analysis.

## 📦 Package Contents

### Core Modules

1. **`multi_variable_analysis.py`** - Surface & 2D field analysis
   - Multiple files with wildcards
   - 5 regridding methods (nearest, bilinear, conservative, conservative_normed, patch)
   - Bidirectional regridding (obs→model or model→obs)
   - Comprehensive statistics and visualizations

2. **`depth_analysis/depth_resolved_analysis.py`** - Full 3D analysis
   - Handles sigma coordinates (ROMS, NEMO, HYCOM)
   - Depth interpolation to standard levels
   - Depth-dependent statistics
   - Vertical profile plots and sections

### Example Scripts

Located in `examples/`:

1. **`example_01_surface_sst.py`** - Surface SST analysis
2. **`example_02_depth_slices.py`** - Extract and analyze fields at specific depths
3. **`example_03_3d_profiles.py`** - Full 3D depth-resolved analysis

### Documentation

- **`examples/README.md`** - Complete usage guide and comparison
- **`depth_analysis/README.md`** - 3D analysis quick start
- **Main README.md** - Surface analysis documentation

## 🚀 Quick Decision Tree

**What do you want to analyze?**

```
Surface field (SST, SSH)
└─> Use: example_01_surface_sst.py
    Module: multi_variable_analysis.py

Temperature/salinity at specific depths (e.g., 100m, 500m)
└─> Use: example_02_depth_slices.py
    Module: multi_variable_analysis.py + extraction function

Full vertical profiles with depth-dependent statistics
└─> Use: example_03_3d_profiles.py
    Module: depth_resolved_analysis.py
```

## 📋 Features Matrix

| Feature | Surface | Depth Slices | 3D Profiles |
|---------|---------|--------------|-------------|
| SST, SSH | ✅ | ❌ | ❌ |
| Temp at 100m | ❌ | ✅ | ✅ |
| Multiple depths | ❌ | ✅ (loop) | ✅ |
| Depth profiles | ❌ | ❌ | ✅ |
| Depth sections | ❌ | ❌ | ✅ |
| Speed | ⚡ Fast | ⚡ Fast | 🐌 Slower |
| Memory | 💚 Low | 💚 Low | ❤️ High |
| Sigma coords | ❌ | ✅ | ✅ |

## 💡 Usage Examples

### Surface Analysis
```python
from multi_variable_analysis import MultiVariableAnalyzer

analyzer = MultiVariableAnalyzer()
analyzer.add_dataset_pair(
    obs_folder="./obs", obs_pattern="sst_*.nc",
    model_folder="./model", model_pattern="model_*.nc",
    variable="sst"
)
analyzer.load_all_data()
analyzer.regrid_data(method='conservative')
analyzer.compute_statistics()
analyzer.create_visualizations()
```

### Depth Slices
```python
# Extract 100m level from 3D data
obs_2d = extract_depth_level(obs_3d, varname='temperature',
                              depth_coord='depth', target_depth=100.0)

# Then analyze as surface field
analyzer.add_dataset_pair(...)
```

### Full 3D
```python
from depth_analysis.depth_resolved_analysis import DepthResolvedAnalyzer

analyzer = DepthResolvedAnalyzer()
depths = np.arange(0, 1000, 10)

analyzer.add_dataset_pair(
    obs_folder="./obs", obs_pattern="temp_*.nc",
    model_folder="./model", model_pattern="ocean_*.nc",
    variable="temperature", standard_depths=depths
)

analyzer.load_all_data()
analyzer.interpolate_to_standard_depths(depths, method='linear')
analyzer.regrid_horizontal(method='conservative')
analyzer.compute_statistics()
analyzer.create_visualizations()
```

## 🎯 Common Use Cases

### Model Validation Report
```bash
python examples/example_01_surface_sst.py    # SST
python examples/example_03_3d_profiles.py    # T/S profiles
```

### Mixed Layer Depth Study
```bash
python examples/example_02_depth_slices.py   # 10m, 20m, 50m, 100m
```

### Thermocline Analysis
```bash
python examples/example_03_3d_profiles.py    # High-res 0-200m
```

## 🔧 Installation

```bash
# Basic (surface analysis only)
pip install numpy xarray matplotlib seaborn scipy pandas

# Full (includes conservative regridding)
pip install numpy xarray matplotlib seaborn scipy pandas xesmf
```

## 📁 Recommended File Organization

```
your_project/
├── data/
│   ├── observations/
│   │   ├── sst/
│   │   │   └── sst_*.nc
│   │   └── profiles/
│   │       ├── temp_*.nc
│   │       └── salt_*.nc
│   └── model/
│       ├── surface/
│       │   └── model_sst_*.nc
│       └── 3d_output/
│           └── ocean_*.nc
├── multi_variable_analysis.py
├── depth_analysis/
│   └── depth_resolved_analysis.py
└── examples/
    ├── example_01_surface_sst.py
    ├── example_02_depth_slices.py
    └── example_03_3d_profiles.py
```

## 🎨 Visualization Outputs

### Surface/Depth Slice Analysis
- Spatial maps (obs, model, bias, RMSE)
- Time series (spatial mean)
- Statistical plots (scatter, distributions, Q-Q plots)

### 3D Analysis
- Depth profiles (bias, RMSE, correlation vs depth)
- Vertical statistics (mean and std dev profiles)
- Depth sections (depth-longitude/latitude)

## 📊 Statistics Computed

### 2D Fields
- Overall: mean, std, min, max, quantiles
- Comparison: bias, RMSE, MAE, correlation
- Spatial: time-averaged maps of bias, RMSE, correlation
- Temporal: monthly statistics (if applicable)

### 3D Fields
- All 2D statistics plus:
- Depth-dependent bias, RMSE, correlation
- Vertical profiles
- Depth-integrated metrics

## ⚙️ Regridding Methods

| Method | Speed | Accuracy | Requires xESMF | Use For |
|--------|-------|----------|----------------|---------|
| nearest | ⚡⚡⚡ | ⭐ | No | Quick tests |
| bilinear | ⚡⚡ | ⭐⭐ | No | Smooth fields |
| conservative | ⚡ | ⭐⭐⭐ | Yes | Temperature, salinity |
| conservative_normed | ⚡ | ⭐⭐⭐ | Yes | Fluxes |
| patch | 🐌 | ⭐⭐⭐⭐ | Yes | Highest accuracy |

## 🔍 Model-Specific Coordinate Names

### ROMS
```python
model_depth="s_rho"  # or s_w, s_u, s_v
```

### NEMO
```python
model_depth="deptht"  # or depthw, depthv, depthu
```

### MOM6
```python
model_depth="z_l"  # layer depths
```

### HYCOM
```python
model_depth="depth"  # hybrid coordinate
```

## 📈 Performance Guidelines

| Data Size | Module | Time | Memory |
|-----------|--------|------|--------|
| 1yr SST (daily) | Surface | ~1 min | <1 GB |
| 10yr SST | Surface | ~5 min | ~2 GB |
| 1 depth level | Depth slice | ~2 min | ~1 GB |
| 5 depth levels | Depth slice | ~10 min | ~2 GB |
| 1yr 3D (50 levels) | 3D | ~10 min | ~5 GB |
| 5yr 3D (100 levels) | 3D | ~60 min | ~20 GB |

## 🆘 Troubleshooting

**Variable not found**
→ Specify: `obs_varname="thetao", model_varname="temp"`

**Depth coordinate not found**
→ Specify: `obs_depth="depth", model_depth="s_rho"`

**Memory error in 3D**
→ Use fewer depths, fewer time steps, or `method='nearest'`

**NaN in depth slice**
→ Increase `tolerance` or check depth range

## 📚 Further Reading

- Surface analysis: `README.md` in main directory
- 3D analysis: `depth_analysis/README.md`
- Detailed examples: `examples/README.md`
- xESMF: https://xesmf.readthedocs.io/
- xarray: https://docs.xarray.dev/

## 🎓 Citation

When using this toolkit, please cite:
- The ocean model used
- Observation datasets used
- xESMF (if using conservative regridding)

## 📄 License

Free to use and modify for scientific research.

## ✨ Key Advantages

1. **Flexible architecture** - Choose the right tool for your needs
2. **Handles sigma coordinates** - Works with ROMS, NEMO, HYCOM
3. **Multiple files support** - Wildcards for multi-year analysis
4. **Comprehensive statistics** - Everything you need for validation
5. **Publication-quality plots** - High-resolution outputs
6. **Well-documented** - Examples for every use case
7. **Extensible** - Easy to add new variables or methods

---

**Start with the examples, modify for your data, and run your analysis!** 🚀
