#!/usr/bin/env python3
"""
Example 3: Full 3D Depth-Resolved Analysis
Demonstrates analyzing vertical profiles with depth-dependent statistics.
Uses the depth_resolved_analysis module for complete vertical analysis.
"""

import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

# Note: This requires the depth_resolved_analysis.py module
try:
    from depth_analysis.depth_resolved_analysis import DepthResolvedAnalyzer
except ImportError:
    print("ERROR: depth_resolved_analysis module not found!")
    print("Make sure depth_resolved_analysis.py is available.")
    sys.exit(1)


def main():
    """
    Full 3D analysis of temperature and salinity profiles.
    
    This example shows:
    - Handling sigma coordinates (ROMS, NEMO)
    - Interpolating to standard depth levels
    - Conservative horizontal regridding
    - Depth-dependent statistics
    - Vertical profile visualizations
    """
    
    print("=" * 80)
    print("EXAMPLE 3: FULL 3D DEPTH-RESOLVED ANALYSIS")
    print("=" * 80)
    
    # Initialize analyzer
    analyzer = DepthResolvedAnalyzer(output_dir="./3d_analysis_output")
    
    # Define standard depth levels with variable resolution
    # High resolution near surface where gradients are strong
    standard_depths = np.concatenate([
        np.arange(0, 50, 5),        # 5m resolution: surface mixed layer
        np.arange(50, 100, 10),     # 10m resolution: seasonal thermocline
        np.arange(100, 200, 20),    # 20m resolution: main thermocline
        np.arange(200, 500, 50),    # 50m resolution: intermediate depths
        np.arange(500, 1000, 100),  # 100m resolution: deep ocean
        np.arange(1000, 2000, 200), # 200m resolution: very deep
    ])
    
    print(f"\nStandard depths: {len(standard_depths)} levels")
    print(f"Depth range: {standard_depths[0]:.0f} to {standard_depths[-1]:.0f} m")
    
    # Add temperature
    print("\nAdding temperature dataset...")
    analyzer.add_dataset_pair(
        obs_folder="./data/observations/profiles",
        obs_pattern="temp_profile_*.nc",  # e.g., monthly or yearly files
        model_folder="./data/model/3d_output",
        model_pattern="ocean_temp_*.nc",
        variable="temperature",
        variable_label="Potential Temperature",
        standard_depths=standard_depths,
        # Specify coordinates if needed:
        # obs_depth="depth",      # Z-level coordinate
        # model_depth="s_rho",    # ROMS sigma coordinate
    )
    
    # Add salinity
    print("Adding salinity dataset...")
    analyzer.add_dataset_pair(
        obs_folder="./data/observations/profiles",
        obs_pattern="salt_profile_*.nc",
        model_folder="./data/model/3d_output",
        model_pattern="ocean_salt_*.nc",
        variable="salinity",
        variable_label="Practical Salinity",
        standard_depths=standard_depths,
    )
    
    # STEP 1: Load data
    print("\n" + "="*80)
    print("STEP 1: LOADING 3D DATA")
    print("="*80)
    analyzer.load_all_data()
    
    # STEP 2: Interpolate to standard depths
    print("\n" + "="*80)
    print("STEP 2: VERTICAL INTERPOLATION")
    print("="*80)
    print("Interpolating from model sigma coordinates to standard depths...")
    analyzer.interpolate_to_standard_depths(
        standard_depths=standard_depths,
        method='linear'  # 'linear', 'nearest', or 'cubic'
    )
    
    # STEP 3: Horizontal regridding
    print("\n" + "="*80)
    print("STEP 3: HORIZONTAL REGRIDDING")
    print("="*80)
    analyzer.regrid_horizontal(
        method='conservative',  # Mass-conserving for temperature/salinity
        direction='obs_to_model'  # Compare on model grid
    )
    
    # STEP 4: Compute statistics
    print("\n" + "="*80)
    print("STEP 4: COMPUTING DEPTH-DEPENDENT STATISTICS")
    print("="*80)
    analyzer.compute_statistics()
    
    # STEP 5: Create visualizations
    print("\n" + "="*80)
    print("STEP 5: CREATING VISUALIZATIONS")
    print("="*80)
    analyzer.create_visualizations()
    
    # STEP 6: Save outputs
    print("\n" + "="*80)
    print("STEP 6: SAVING OUTPUTS")
    print("="*80)
    analyzer.save_statistics_report()
    analyzer.save_netcdf_outputs()
    
    print("\n" + "=" * 80)
    print("3D ANALYSIS COMPLETE!")
    print(f"Results saved to: {analyzer.output_dir}")
    print("=" * 80)
    
    # Print key results
    print("\nKEY DEPTH-DEPENDENT RESULTS:")
    print("=" * 80)
    
    for var_key, stats in analyzer.stats.items():
        print(f"\n{var_key.upper()}:")
        print(f"  Overall bias: {stats['bias']:.4f}")
        print(f"  Overall RMSE: {stats['rmse']:.4f}")
        print(f"  Overall correlation: {stats['correlation']:.4f}")
        
        # Show bias at selected depths
        print(f"\n  Bias at selected depths:")
        for i, depth in enumerate(standard_depths):
            if i % 10 == 0:  # Show every 10th level
                bias = stats['depth_bias'][i]
                print(f"    {depth:6.0f}m: {bias:8.4f}")
    
    print("\n" + "=" * 80)
    print("VISUALIZATIONS CREATED:")
    print("  - depth_profiles.png: Vertical profiles of bias, RMSE, correlation")
    print("  - vertical_statistics.png: Mean and std dev profiles")
    print("  - depth_sections.png: Depth-longitude cross-sections")
    print("=" * 80)


if __name__ == "__main__":
    main()
