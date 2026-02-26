#!/usr/bin/env python3
"""
Example 2: Analysis at Specific Depth Levels
Demonstrates extracting 2D fields at specific depths from 3D data and analyzing them.
"""

import sys
from pathlib import Path
import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).parent.parent))
from multi_variable_analysis import MultiVariableAnalyzer


def extract_depth_level(ds, varname, depth_coord, target_depth, tolerance=10.0):
    """
    Extract a 2D field at a specific depth from 3D data.
    
    Parameters:
    -----------
    ds : xr.Dataset
        Input 3D dataset
    varname : str
        Variable name
    depth_coord : str
        Depth coordinate name
    target_depth : float
        Target depth in meters (positive down)
    tolerance : float
        Tolerance for depth matching (meters)
        
    Returns:
    --------
    xr.Dataset : 2D dataset at target depth
    """
    depth_data = ds[depth_coord]
    
    if depth_data.ndim == 1:
        # Simple 1D depth coordinate (z-level)
        depth_vals = depth_data.values
        depth_idx = np.argmin(np.abs(depth_vals - target_depth))
        
        actual_depth = depth_vals[depth_idx]
        if np.abs(actual_depth - target_depth) > tolerance:
            print(f"Warning: Nearest depth {actual_depth:.1f}m exceeds tolerance {tolerance}m")
        
        # Select the depth level
        slice_ds = ds.isel({depth_coord: depth_idx})
        print(f"  Extracted at depth: {actual_depth:.1f}m")
        
    else:
        # Sigma coordinates - need interpolation
        print(f"  Interpolating from sigma coordinates to {target_depth}m...")
        from scipy.interpolate import interp1d
        
        data_var = ds[varname]
        dims = data_var.dims
        depth_axis = dims.index(depth_coord)
        
        # Move depth axis to last
        data = np.moveaxis(data_var.values, depth_axis, -1)
        depth = np.moveaxis(depth_data.values, depth_axis, -1) if depth_data.ndim > 1 else depth_data.values
        
        # Flatten for interpolation
        original_shape = data.shape
        data_flat = data.reshape(-1, original_shape[-1])
        depth_flat = depth.reshape(-1, original_shape[-1]) if depth.ndim > 1 else np.broadcast_to(depth, data.shape).reshape(-1, original_shape[-1])
        
        # Interpolate each profile
        interp_values = np.full(data_flat.shape[0], np.nan)
        
        for i in range(data_flat.shape[0]):
            profile = data_flat[i, :]
            depths = depth_flat[i, :]
            
            valid = np.isfinite(profile) & np.isfinite(depths)
            if valid.sum() < 2:
                continue
            
            # Sort by depth
            sort_idx = np.argsort(depths[valid])
            sorted_depths = depths[valid][sort_idx]
            sorted_profile = profile[valid][sort_idx]
            
            if target_depth < sorted_depths[0] or target_depth > sorted_depths[-1]:
                continue
            
            try:
                f = interp1d(sorted_depths, sorted_profile, kind='linear')
                interp_values[i] = f(target_depth)
            except:
                continue
        
        # Reshape back (without depth dimension)
        new_shape = list(original_shape[:-1])
        interp_data = interp_values.reshape(new_shape)
        
        # Create new dataset
        new_dims = [d for d in dims if d != depth_coord]
        new_coords = {d: ds[d] for d in new_dims if d in ds.coords}
        
        slice_ds = xr.Dataset(
            {varname: (new_dims, interp_data)},
            coords=new_coords
        )
        slice_ds[varname].attrs = data_var.attrs
    
    return slice_ds


def main():
    """
    Analyze temperature at specific depth levels (e.g., 100m, 500m).
    
    This example shows:
    - Extracting 2D fields from 3D data at specific depths
    - Handling both z-level and sigma coordinates
    - Analyzing multiple depth levels
    - Using the standard surface analysis workflow
    """
    
    print("=" * 80)
    print("EXAMPLE 2: TEMPERATURE AT SPECIFIC DEPTHS")
    print("=" * 80)
    
    # Depths to analyze
    depths_to_analyze = [50, 100, 200, 500]
    
    for target_depth in depths_to_analyze:
        print(f"\n{'='*80}")
        print(f"ANALYZING TEMPERATURE AT {target_depth}m")
        print('='*80)
        
        # Load 3D datasets
        print("\nLoading 3D datasets...")
        obs_3d = xr.open_mfdataset("./data/obs/temp_*.nc")
        model_3d = xr.open_mfdataset("./data/model/ocean_*.nc")
        
        print("  Observed 3D shape:", obs_3d['temperature'].shape)
        print("  Model 3D shape:", model_3d['temp'].shape)
        
        # Extract 2D slices at target depth
        print(f"\nExtracting {target_depth}m level...")
        print("  From observations...")
        obs_2d = extract_depth_level(
            obs_3d, 
            varname='temperature',
            depth_coord='depth',  # Usually 'depth' or 'z' for observations
            target_depth=target_depth,
            tolerance=15.0
        )
        
        print("  From model...")
        model_2d = extract_depth_level(
            model_3d,
            varname='temp',
            depth_coord='s_rho',  # ROMS sigma coordinate
            target_depth=target_depth,
            tolerance=15.0
        )
        
        # Save extracted 2D fields
        obs_file = f"./temp_depth_slices/obs_temp_{target_depth}m.nc"
        model_file = f"./temp_depth_slices/model_temp_{target_depth}m.nc"
        
        Path("./temp_depth_slices").mkdir(exist_ok=True, parents=True)
        obs_2d.to_netcdf(obs_file)
        model_2d.to_netcdf(model_file)
        
        print(f"  Saved extracted fields to ./temp_depth_slices/")
        
        # Now analyze the 2D field using standard surface analysis
        print(f"\nAnalyzing temperature at {target_depth}m...")
        
        analyzer = MultiVariableAnalyzer(
            output_dir=f"./temp_at_{target_depth}m_output"
        )
        
        # Add the extracted 2D fields
        analyzer.add_dataset_pair(
            obs_folder="./temp_depth_slices",
            obs_pattern=f"obs_temp_{target_depth}m.nc",
            model_folder="./temp_depth_slices",
            model_pattern=f"model_temp_{target_depth}m.nc",
            variable=f"temp_{target_depth}m",
            obs_varname='temperature',
            model_varname='temp',
            variable_label=f"Temperature at {target_depth}m",
        )
        
        # Run standard analysis
        analyzer.load_all_data()
        analyzer.regrid_data(method='conservative', direction='obs_to_model')
        analyzer.compute_statistics()
        analyzer.create_visualizations()
        analyzer.save_statistics_report()
        analyzer.save_netcdf_outputs()
        
        print(f"\nAnalysis complete for {target_depth}m depth level!")
        print(f"Results in: ./temp_at_{target_depth}m_output/")
    
    print("\n" + "=" * 80)
    print("ALL DEPTH LEVELS ANALYZED!")
    print("=" * 80)
    
    # Summary of results
    print("\nSUMMARY OF DEPTH-DEPENDENT BIAS:")
    print("-" * 40)
    print(f"{'Depth (m)':<15} {'Bias':>15}")
    print("-" * 40)
    
    # This would be populated from actual analysis results
    for depth in depths_to_analyze:
        print(f"{depth:<15} {'[see report]':>15}")


if __name__ == "__main__":
    main()
