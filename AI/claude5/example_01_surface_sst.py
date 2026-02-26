#!/usr/bin/env python3
"""
Example 1: Surface Variable Analysis (SST)
Demonstrates analyzing 2D surface fields using the core analysis module.
"""

import sys
from pathlib import Path

# Import the core module
sys.path.insert(0, str(Path(__file__).parent.parent))
from multi_variable_analysis import MultiVariableAnalyzer


def main():
    """
    Analyze sea surface temperature (SST) from multiple files.
    
    This example shows:
    - Loading multiple yearly SST files
    - Auto-detection of SST variable
    - Conservative regridding for accurate comparison
    - Complete statistical analysis and visualization
    """
    
    print("=" * 80)
    print("EXAMPLE 1: SURFACE SST ANALYSIS")
    print("=" * 80)
    
    # Initialize analyzer
    analyzer = MultiVariableAnalyzer(output_dir="./sst_output")
    
    # Add SST dataset
    # Files can be: sst_2020.nc, sst_2021.nc, sst_2022.nc
    # Or use wildcards: sst_*.nc
    analyzer.add_dataset_pair(
        obs_folder="./data/observations/sst",
        obs_pattern="sst_*.nc",  # Wildcard for all years
        model_folder="./data/model/surface",
        model_pattern="model_sst_*.nc",
        variable="sst",
        variable_label="Sea Surface Temperature",
    )
    
    # Load all data files
    analyzer.load_all_data()
    
    # Regrid observations to model grid using conservative method
    analyzer.regrid_data(
        method='conservative',  # Preserves integrals
        direction='obs_to_model'  # Compare on model grid
    )
    
    # Compute statistics
    analyzer.compute_statistics()
    
    # Create visualizations
    analyzer.create_visualizations()
    
    # Save outputs
    analyzer.save_statistics_report()
    analyzer.save_netcdf_outputs()
    
    print("\n" + "=" * 80)
    print("SST ANALYSIS COMPLETE!")
    print(f"Results saved to: {analyzer.output_dir}")
    print("=" * 80)


if __name__ == "__main__":
    main()
