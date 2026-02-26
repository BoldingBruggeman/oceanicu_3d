#!/usr/bin/env python3
"""
Example usage of the SST Analysis Tool

This script demonstrates how to use the SSTAnalyzer class
for comparing observed and modeled SST data.
"""

from sst_analysis import SSTAnalyzer

# Example 1: Basic usage with auto-detection
print("Example 1: Basic usage with auto-detection")
print("-" * 60)

analyzer = SSTAnalyzer(
    obs_file='observations.nc',
    model_file='model_output.nc',
    output_dir='./example_output'
)

# Load data - variable names auto-detected
analyzer.load_data()

# Regrid observations to model grid
analyzer.regrid_observations(method='nearest')

# Compute all statistics
analyzer.compute_statistics()

# Create visualizations
analyzer.create_visualizations()

# Save reports
analyzer.save_statistics_report()
analyzer.save_netcdf_outputs()

print("\nAnalysis complete! Check ./example_output for results\n")


# Example 2: Explicit variable names
print("Example 2: Explicit variable specification")
print("-" * 60)

analyzer2 = SSTAnalyzer(
    obs_file='satellite_sst.nc',
    model_file='cmip6_sst.nc',
    output_dir='./satellite_vs_model'
)

# Specify variable names explicitly
analyzer2.load_data(
    obs_varname='analysed_sst',
    model_varname='tos'
)

analyzer2.regrid_observations(method='nearest')
analyzer2.compute_statistics()
analyzer2.create_visualizations()
analyzer2.save_statistics_report()
analyzer2.save_netcdf_outputs()

print("\nAnalysis complete! Check ./satellite_vs_model for results\n")


# Example 3: Accessing computed statistics programmatically
print("Example 3: Accessing statistics programmatically")
print("-" * 60)

# After running the analysis, you can access statistics:
stats = analyzer.stats

print(f"Global Bias: {stats['bias']:.4f} °C")
print(f"RMSE: {stats['rmse']:.4f} °C")
print(f"MAE: {stats['mae']:.4f} °C")
print(f"Correlation: {stats['correlation']:.4f}")
print(f"Temporal Correlation: {stats['temporal_correlation']:.4f}")
print(f"\nObserved SST: {stats['obs_mean']:.2f} ± {stats['obs_std']:.2f} °C")
print(f"Modeled SST: {stats['model_mean']:.2f} ± {stats['model_std']:.2f} °C")

print("\nMonthly Bias:")
for month, bias in stats['monthly_bias'].items():
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    print(f"  {month_names[month-1]}: {bias:6.3f} °C")

print("\nQuantile comparison:")
for q in [0.05, 0.25, 0.5, 0.75, 0.95]:
    obs_q = stats['obs_quantiles'][q]
    model_q = stats['model_quantiles'][q]
    print(f"  {q:5.1%}: Obs={obs_q:6.2f} °C, Model={model_q:6.2f} °C, "
          f"Diff={model_q-obs_q:6.2f} °C")


# Example 4: Custom analysis workflow
print("\n\nExample 4: Custom analysis workflow")
print("-" * 60)

class CustomSSTAnalyzer(SSTAnalyzer):
    """Extended analyzer with custom methods"""
    
    def compute_seasonal_statistics(self):
        """Compute statistics by season"""
        seasons = {
            'DJF': [12, 1, 2],
            'MAM': [3, 4, 5],
            'JJA': [6, 7, 8],
            'SON': [9, 10, 11]
        }
        
        seasonal_stats = {}
        
        for season_name, months in seasons.items():
            obs_season = self.obs_regridded.sel(
                time=self.obs_regridded.time.dt.month.isin(months))
            model_season = self.model_ds[self.model_varname].sel(
                time=self.model_ds[self.model_varname].time.dt.month.isin(months))
            
            bias = float((model_season - obs_season).mean())
            rmse = float(((model_season - obs_season)**2).mean()**0.5)
            
            seasonal_stats[season_name] = {
                'bias': bias,
                'rmse': rmse
            }
        
        return seasonal_stats
    
    def identify_hotspots(self, threshold=2.0):
        """Identify regions with large bias (>threshold °C)"""
        import numpy as np
        
        high_bias_mask = np.abs(self.spatial_bias) > threshold
        
        n_hotspots = int(high_bias_mask.sum())
        
        print(f"\nFound {n_hotspots} grid points with |bias| > {threshold} °C")
        
        if n_hotspots > 0:
            max_bias = float(self.spatial_bias.max())
            min_bias = float(self.spatial_bias.min())
            print(f"Maximum bias: {max_bias:.2f} °C")
            print(f"Minimum bias: {min_bias:.2f} °C")
        
        return high_bias_mask


# Use custom analyzer
custom_analyzer = CustomSSTAnalyzer(
    obs_file='observations.nc',
    model_file='model_output.nc',
    output_dir='./custom_analysis'
)

# Standard workflow
custom_analyzer.load_data()
custom_analyzer.regrid_observations()
custom_analyzer.compute_statistics()

# Custom analyses
seasonal_stats = custom_analyzer.compute_seasonal_statistics()
print("\nSeasonal Statistics:")
for season, stats in seasonal_stats.items():
    print(f"  {season}: Bias={stats['bias']:6.3f} °C, RMSE={stats['rmse']:6.3f} °C")

hotspots = custom_analyzer.identify_hotspots(threshold=2.0)

# Continue with standard outputs
custom_analyzer.create_visualizations()
custom_analyzer.save_statistics_report()
custom_analyzer.save_netcdf_outputs()

print("\n" + "="*60)
print("All examples completed successfully!")
print("="*60)
