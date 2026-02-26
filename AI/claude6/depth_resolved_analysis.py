#!/usr/bin/env python3
"""
Depth-Resolved Ocean Model Analysis Module
Handles full 3D ocean data with depth coordinates including sigma coordinates.
Can be used standalone or imported into other scripts.
"""

import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d
from scipy.spatial import cKDTree
import pandas as pd
from pathlib import Path
import warnings
from typing import List, Dict, Optional, Tuple
import glob

warnings.filterwarnings("ignore")

# Try to import xESMF
try:
    import xesmf as xe
    XESMF_AVAILABLE = True
except ImportError:
    XESMF_AVAILABLE = False


class DepthResolvedAnalyzer:
    """
    Analyzer for full 3D ocean data with depth-resolved statistics.
    Handles different vertical coordinate systems (z-level, sigma, hybrid).
    """

    def __init__(self, output_dir="./depth_analysis_output"):
        """Initialize the depth-resolved analyzer."""
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)
        
        self.datasets = []
        self.stats = {}
        self.standard_depths = None
        self.depth_interp_method = 'linear'
        self.regrid_method = 'nearest'
        self.regrid_direction = 'obs_to_model'

    def add_dataset_pair(
        self,
        obs_folder: str,
        model_folder: str,
        obs_pattern: str,
        model_pattern: str,
        variable: str,
        standard_depths: np.ndarray,
        obs_varname: Optional[str] = None,
        model_varname: Optional[str] = None,
        obs_lat: Optional[str] = None,
        obs_lon: Optional[str] = None,
        obs_depth: Optional[str] = None,
        model_lat: Optional[str] = None,
        model_lon: Optional[str] = None,
        model_depth: Optional[str] = None,
        variable_label: Optional[str] = None,
        depth_interp_method: str = 'linear',
    ):
        """Add a 3D variable for depth-resolved analysis."""
        obs_files = self._expand_file_pattern(obs_folder, obs_pattern)
        model_files = self._expand_file_pattern(model_folder, model_pattern)
        
        if len(obs_files) == 0:
            raise ValueError(f"No observed files found: {obs_folder}/{obs_pattern}")
        if len(model_files) == 0:
            raise ValueError(f"No model files found: {model_folder}/{model_pattern}")
        
        dataset_config = {
            'obs_files': obs_files,
            'model_files': model_files,
            'variable': variable,
            'standard_depths': standard_depths,
            'depth_interp_method': depth_interp_method,
            'obs_varname': obs_varname,
            'model_varname': model_varname,
            'obs_lat': obs_lat,
            'obs_lon': obs_lon,
            'obs_depth': obs_depth,
            'model_lat': model_lat,
            'model_lon': model_lon,
            'model_depth': model_depth,
            'variable_label': variable_label or variable.upper(),
            'obs_ds': None,
            'model_ds': None,
            'obs_ds_interp': None,
            'model_ds_interp': None,
            'regridded_ds': None,
        }
        
        self.datasets.append(dataset_config)

    def _expand_file_pattern(self, folder: str, pattern: str) -> List[str]:
        """Expand file pattern to list of files."""
        folder_path = Path(folder)
        patterns = pattern.split()
        
        all_files = []
        for pat in patterns:
            full_pattern = str(folder_path / pat)
            matched_files = glob.glob(full_pattern)
            all_files.extend(matched_files)
        
        return sorted(set(all_files))

    def load_all_data(self):
        """Load all datasets."""
        print("\n" + "=" * 80)
        print("LOADING 3D DATASETS")
        print("=" * 80)
        
        for idx, config in enumerate(self.datasets):
            print(f"\nDataset {idx + 1}: {config['variable']}")
            print(f"  Observed files: {len(config['obs_files'])}")
            print(f"  Model files: {len(config['model_files'])}")
            
            # Load datasets
            config['obs_ds'] = self._load_and_concatenate(config['obs_files'])
            config['model_ds'] = self._load_and_concatenate(config['model_files'])
            
            # Auto-detect variables
            if config['obs_varname'] is None:
                config['obs_varname'] = self._detect_variable(
                    config['obs_ds'], config['variable']
                )
            if config['model_varname'] is None:
                config['model_varname'] = self._detect_variable(
                    config['model_ds'], config['variable']
                )
            
            print(f"  Observed variable: {config['obs_varname']}")
            print(f"  Model variable: {config['model_varname']}")
            
            # Identify depth coordinates
            if config['obs_depth'] is None:
                config['obs_depth'] = self._identify_depth_coordinate(
                    config['obs_ds'], config['obs_varname']
                )
            if config['model_depth'] is None:
                config['model_depth'] = self._identify_depth_coordinate(
                    config['model_ds'], config['model_varname']
                )
            
            print(f"  Observed depth: {config['obs_depth']}")
            print(f"  Model depth: {config['model_depth']}")
            
            # Check units
            self._check_and_convert_units(config)

    def _load_and_concatenate(self, file_list: List[str]) -> xr.Dataset:
        """Load and concatenate multiple files."""
        if len(file_list) == 1:
            return xr.open_dataset(file_list[0])
        
        try:
            return xr.open_mfdataset(file_list, combine='by_coords')
        except:
            datasets = [xr.open_dataset(f) for f in file_list]
            time_dims = [d for d in datasets[0].dims if 'time' in d.lower()]
            time_dim = time_dims[0] if time_dims else 'time'
            return xr.concat(datasets, dim=time_dim)

    def _detect_variable(self, ds: xr.Dataset, hint: str) -> str:
        """Auto-detect variable name."""
        patterns = {
            'temperature': ['temp', 'temperature', 'thetao', 't'],
            'salinity': ['salinity', 'sal', 'so', 'salt'],
        }
        
        for pattern in patterns.get(hint.lower(), [hint]):
            if pattern in ds.variables:
                return pattern
        
        for var in ds.variables:
            if hint.lower() in var.lower() and 'time' in ds[var].dims:
                return var
        
        raise ValueError(f"Could not detect variable: {hint}")

    def _identify_depth_coordinate(self, ds: xr.Dataset, varname: str) -> str:
        """Identify depth coordinate."""
        depth_names = ['depth', 'z', 'lev', 's_rho', 's_w', 'deptht']
        
        var_dims = ds[varname].dims
        for dim in var_dims:
            if any(d in dim.lower() for d in depth_names):
                return dim
        
        raise ValueError(f"Could not identify depth coordinate")

    def _check_and_convert_units(self, config: Dict):
        """Convert units if needed."""
        for prefix in ['obs', 'model']:
            var = config[f'{prefix}_ds'][config[f'{prefix}_varname']]
            units = getattr(var, 'units', '').lower()
            
            if units in ['kelvin', 'k']:
                print(f"  Converting {prefix} from Kelvin to Celsius")
                config[f'{prefix}_ds'][config[f'{prefix}_varname']] -= 273.15
                config[f'{prefix}_ds'][config[f'{prefix}_varname']].attrs['units'] = 'degrees_Celsius'

    def interpolate_to_standard_depths(
        self,
        standard_depths: Optional[np.ndarray] = None,
        method: str = 'linear'
    ):
        """Interpolate to standard depth levels."""
        if standard_depths is not None:
            self.standard_depths = standard_depths
        
        self.depth_interp_method = method
        
        print("\n" + "=" * 80)
        print("INTERPOLATING TO STANDARD DEPTHS")
        print("=" * 80)
        
        for config in self.datasets:
            if self.standard_depths is None:
                self.standard_depths = config['standard_depths']
            
            print(f"\nProcessing {config['variable']}")
            print(f"  {len(self.standard_depths)} depth levels")
            
            config['obs_ds_interp'] = self._interpolate_dataset_to_depths(
                config['obs_ds'],
                config['obs_varname'],
                config['obs_depth'],
                self.standard_depths,
                method
            )
            
            config['model_ds_interp'] = self._interpolate_dataset_to_depths(
                config['model_ds'],
                config['model_varname'],
                config['model_depth'],
                self.standard_depths,
                method
            )

    def _interpolate_dataset_to_depths(
        self,
        ds: xr.Dataset,
        varname: str,
        depth_coord: str,
        target_depths: np.ndarray,
        method: str
    ) -> xr.Dataset:
        """Interpolate dataset to target depths."""
        data_var = ds[varname]
        depth_var = ds[depth_coord]
        
        dims = data_var.dims
        depth_axis = dims.index(depth_coord)
        
        # Get time dimension
        time_dim = [d for d in dims if 'time' in d.lower()][0]
        
        # Prepare output
        new_shape = list(data_var.shape)
        new_shape[depth_axis] = len(target_depths)
        interp_data = np.full(new_shape, np.nan)
        
        # Move depth axis to last position
        data = np.moveaxis(data_var.values, depth_axis, -1)
        
        if depth_var.ndim == 1:
            depth = depth_var.values
        else:
            depth = np.moveaxis(depth_var.values, depth_axis, -1)
        
        # Flatten
        original_shape = data.shape
        data_flat = data.reshape(-1, original_shape[-1])
        
        if depth.ndim == 1:
            depth_flat = np.broadcast_to(depth, data.shape).reshape(-1, original_shape[-1])
        else:
            depth_flat = depth.reshape(-1, original_shape[-1])
        
        # Interpolate
        interp_flat = np.full((data_flat.shape[0], len(target_depths)), np.nan)
        
        for i in range(data_flat.shape[0]):
            if i % 10000 == 0:
                print(f"    {i}/{data_flat.shape[0]} profiles")
            
            profile = data_flat[i, :]
            depths = depth_flat[i, :]
            
            valid = np.isfinite(profile) & np.isfinite(depths)
            if valid.sum() < 2:
                continue
            
            # Sort by depth
            sort_idx = np.argsort(depths[valid])
            sorted_depths = depths[valid][sort_idx]
            sorted_profile = profile[valid][sort_idx]
            
            try:
                f = interp1d(sorted_depths, sorted_profile, kind=method, bounds_error=False)
                interp_flat[i, :] = f(target_depths)
            except:
                continue
        
        # Reshape back
        new_shape = list(original_shape)
        new_shape[-1] = len(target_depths)
        interp_data_reshaped = interp_flat.reshape(new_shape)
        
        # Move depth back
        interp_data = np.moveaxis(interp_data_reshaped, -1, depth_axis)
        
        # Create dataset
        new_dims = list(dims)
        new_dims[depth_axis] = 'depth_standard'
        
        coords = {}
        for dim in dims:
            if dim == depth_coord:
                coords['depth_standard'] = target_depths
            else:
                coords[dim] = ds[dim]
        
        interp_ds = xr.Dataset(
            {varname: (new_dims, interp_data)},
            coords=coords
        )
        
        interp_ds[varname].attrs = data_var.attrs
        
        return interp_ds

    def regrid_horizontal(self, method: str = 'nearest', direction: str = 'obs_to_model'):
        """Regrid horizontally after depth interpolation."""
        self.regrid_method = method
        self.regrid_direction = direction
        
        print("\n" + "=" * 80)
        print(f"HORIZONTAL REGRIDDING: {direction.upper()}")
        print("=" * 80)
        
        for config in self.datasets:
            print(f"\nRegridding {config['variable']}")
            
            # Identify coordinates
            if config['obs_lat'] is None:
                config['obs_lat'], config['obs_lon'] = self._identify_coordinates(config['obs_ds'])
            if config['model_lat'] is None:
                config['model_lat'], config['model_lon'] = self._identify_coordinates(config['model_ds'])
            
            config['regridded_ds'] = self._regrid_3d_data(config, method, direction)

    def _identify_coordinates(self, ds: xr.Dataset) -> Tuple[str, str]:
        """Identify lat/lon coordinates."""
        lat_names = ['lat', 'latitude', 'y', 'nav_lat']
        lon_names = ['lon', 'longitude', 'x', 'nav_lon']
        
        lat = next((n for n in lat_names if n in ds.coords), None)
        lon = next((n for n in lon_names if n in ds.coords), None)
        
        if lat is None or lon is None:
            raise ValueError("Could not identify lat/lon")
        
        return lat, lon

    def _regrid_3d_data(self, config: Dict, method: str, direction: str) -> xr.Dataset:
        """Regrid 3D data."""
        source_ds = config['obs_ds_interp'] if direction == 'obs_to_model' else config['model_ds_interp']
        target_ds = config['model_ds_interp'] if direction == 'obs_to_model' else config['obs_ds_interp']
        
        varname = config['obs_varname'] if direction == 'obs_to_model' else config['model_varname']
        
        source_lat = config['obs_lat'] if direction == 'obs_to_model' else config['model_lat']
        source_lon = config['obs_lon'] if direction == 'obs_to_model' else config['model_lon']
        target_lat = config['model_lat'] if direction == 'obs_to_model' else config['obs_lat']
        target_lon = config['model_lon'] if direction == 'obs_to_model' else config['obs_lon']
        
        # Regrid each depth level
        source_var = source_ds[varname]
        dims = source_var.dims
        
        time_dim = [d for d in dims if 'time' in d.lower()][0]
        depth_dim = 'depth_standard'
        
        n_time = source_var.shape[dims.index(time_dim)]
        n_depth = source_var.shape[dims.index(depth_dim)]
        
        target_lat_data = target_ds[target_lat].values
        target_lon_data = target_ds[target_lon].values
        
        if target_lat_data.ndim == 1:
            target_shape = (len(target_lat_data), len(target_lon_data))
        else:
            target_shape = target_lat_data.shape
        
        output_shape = (n_time, n_depth) + target_shape
        regridded_data = np.full(output_shape, np.nan)
        
        print(f"  Processing {n_time} timesteps, {n_depth} depths")
        
        # Simple nearest neighbor for each level
        source_lat_data = source_ds[source_lat].values
        source_lon_data = source_ds[source_lon].values
        
        if source_lat_data.ndim == 1:
            src_lon, src_lat = np.meshgrid(source_lon_data, source_lat_data)
        else:
            src_lat = source_lat_data
            src_lon = source_lon_data
        
        if target_lat_data.ndim == 1:
            tgt_lon, tgt_lat = np.meshgrid(target_lon_data, target_lat_data)
        else:
            tgt_lat = target_lat_data
            tgt_lon = target_lon_data
        
        src_points = np.column_stack([src_lat.flatten(), src_lon.flatten()])
        tgt_points = np.column_stack([tgt_lat.flatten(), tgt_lon.flatten()])
        
        tree = cKDTree(src_points)
        _, indices = tree.query(tgt_points)
        
        for t in range(n_time):
            for d in range(n_depth):
                data_slice = source_var.isel({time_dim: t, depth_dim: d}).values.flatten()
                regridded_data[t, d] = data_slice[indices].reshape(target_shape)
        
        # Create output
        regridded_ds = xr.Dataset(
            {varname: ([time_dim, depth_dim, target_lat, target_lon], regridded_data)},
            coords={
                time_dim: source_ds[time_dim],
                depth_dim: source_ds[depth_dim],
                target_lat: target_lat_data,
                target_lon: target_lon_data,
            }
        )
        
        return regridded_ds

    def compute_statistics(self):
        """Compute depth-resolved statistics."""
        print("\n" + "=" * 80)
        print("COMPUTING STATISTICS")
        print("=" * 80)
        
        for config in self.datasets:
            print(f"\nComputing for {config['variable']}")
            
            var_key = config['variable']
            
            if self.regrid_direction == 'obs_to_model':
                obs_data = config['regridded_ds'][config['obs_varname']].values
                model_data = config['model_ds_interp'][config['model_varname']].values
            else:
                obs_data = config['obs_ds_interp'][config['obs_varname']].values
                model_data = config['regridded_ds'][config['model_varname']].values
            
            self.stats[var_key] = self._compute_3d_statistics(obs_data, model_data)
            
            config['obs_data_common'] = obs_data
            config['model_data_common'] = model_data

    def _compute_3d_statistics(self, obs_data: np.ndarray, model_data: np.ndarray) -> Dict:
        """Compute 3D statistics."""
        stats = {}
        
        valid = ~(np.isnan(obs_data) | np.isnan(model_data))
        
        stats['obs_mean'] = np.nanmean(obs_data)
        stats['obs_std'] = np.nanstd(obs_data)
        stats['obs_min'] = np.nanmin(obs_data)
        stats['obs_max'] = np.nanmax(obs_data)
        
        stats['model_mean'] = np.nanmean(model_data)
        stats['model_std'] = np.nanstd(model_data)
        stats['model_min'] = np.nanmin(model_data)
        stats['model_max'] = np.nanmax(model_data)
        
        diff = model_data - obs_data
        stats['bias'] = np.nanmean(diff)
        stats['rmse'] = np.sqrt(np.nanmean(diff**2))
        stats['mae'] = np.nanmean(np.abs(diff))
        
        obs_flat = obs_data[valid]
        model_flat = model_data[valid]
        if len(obs_flat) > 0:
            stats['correlation'] = np.corrcoef(obs_flat, model_flat)[0, 1]
        else:
            stats['correlation'] = np.nan
        
        # Depth-dependent
        n_depths = obs_data.shape[1]
        stats['depth_bias'] = np.array([np.nanmean(diff[:, d]) for d in range(n_depths)])
        stats['depth_rmse'] = np.array([np.sqrt(np.nanmean(diff[:, d]**2)) for d in range(n_depths)])
        stats['depth_correlation'] = np.full(n_depths, np.nan)
        
        for d in range(n_depths):
            valid_d = ~(np.isnan(obs_data[:, d]) | np.isnan(model_data[:, d]))
            if valid_d.sum() > 10:
                stats['depth_correlation'][d] = np.corrcoef(
                    obs_data[:, d][valid_d].flatten(),
                    model_data[:, d][valid_d].flatten()
                )[0, 1]
        
        stats['spatial_bias'] = np.nanmean(diff, axis=0)
        
        return stats

    def create_visualizations(self):
        """Create visualizations."""
        print("\n" + "=" * 80)
        print("CREATING VISUALIZATIONS")
        print("=" * 80)
        
        for config in self.datasets:
            var_dir = self.output_dir / config['variable']
            var_dir.mkdir(exist_ok=True)
            
            units = getattr(config['obs_ds'][config['obs_varname']], 'units', '')
            
            self._plot_depth_profiles(config, var_dir, config['variable_label'], units)
            self._plot_depth_statistics(config, var_dir, config['variable_label'], units)
            self._plot_depth_sections(config, var_dir, config['variable_label'], units)

    def _plot_depth_profiles(self, config, output_dir, label, units):
        """Plot vertical profiles."""
        stats = self.stats[config['variable']]
        depths = self.standard_depths
        
        fig, axes = plt.subplots(1, 3, figsize=(15, 8))
        
        axes[0].plot(stats['depth_bias'], depths, 'b-', lw=2)
        axes[0].axvline(0, color='k', linestyle='--')
        axes[0].set_xlabel(f'Bias ({units})')
        axes[0].set_ylabel('Depth (m)')
        axes[0].set_title('Bias Profile')
        axes[0].invert_yaxis()
        axes[0].grid(True, alpha=0.3)
        
        axes[1].plot(stats['depth_rmse'], depths, 'r-', lw=2)
        axes[1].set_xlabel(f'RMSE ({units})')
        axes[1].set_ylabel('Depth (m)')
        axes[1].set_title('RMSE Profile')
        axes[1].invert_yaxis()
        axes[1].grid(True, alpha=0.3)
        
        axes[2].plot(stats['depth_correlation'], depths, 'g-', lw=2)
        axes[2].axvline(0, color='k', linestyle='--')
        axes[2].set_xlabel('Correlation')
        axes[2].set_ylabel('Depth (m)')
        axes[2].set_title('Correlation Profile')
        axes[2].set_xlim([-1, 1])
        axes[2].invert_yaxis()
        axes[2].grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(output_dir / 'depth_profiles.png', dpi=300, bbox_inches='tight')
        plt.close()

    def _plot_depth_statistics(self, config, output_dir, label, units):
        """Plot mean and std profiles."""
        obs_data = config['obs_data_common']
        model_data = config['model_data_common']
        
        obs_profile = np.nanmean(obs_data, axis=(0, 2, 3))
        model_profile = np.nanmean(model_data, axis=(0, 2, 3))
        obs_std = np.nanstd(obs_data, axis=(0, 2, 3))
        model_std = np.nanstd(model_data, axis=(0, 2, 3))
        
        fig, axes = plt.subplots(1, 2, figsize=(12, 8))
        
        axes[0].plot(obs_profile, self.standard_depths, 'b-', lw=2, label='Observed')
        axes[0].plot(model_profile, self.standard_depths, 'r-', lw=2, label='Model')
        axes[0].set_xlabel(f'{label} ({units})')
        axes[0].set_ylabel('Depth (m)')
        axes[0].set_title('Mean Profiles')
        axes[0].invert_yaxis()
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)
        
        axes[1].plot(obs_std, self.standard_depths, 'b-', lw=2, label='Observed')
        axes[1].plot(model_std, self.standard_depths, 'r-', lw=2, label='Model')
        axes[1].set_xlabel(f'Std Dev ({units})')
        axes[1].set_ylabel('Depth (m)')
        axes[1].set_title('Standard Deviation')
        axes[1].invert_yaxis()
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(output_dir / 'vertical_statistics.png', dpi=300, bbox_inches='tight')
        plt.close()

    def _plot_depth_sections(self, config, output_dir, label, units):
        """Plot depth sections."""
        obs_data = config['obs_data_common']
        model_data = config['model_data_common']
        
        obs_mean = np.nanmean(obs_data, axis=0)
        model_mean = np.nanmean(model_data, axis=0)
        diff_mean = model_mean - obs_mean
        
        obs_section = np.nanmean(obs_mean, axis=1)
        model_section = np.nanmean(model_mean, axis=1)
        diff_section = np.nanmean(diff_mean, axis=1)
        
        fig, axes = plt.subplots(3, 1, figsize=(12, 12))
        
        lon = np.arange(obs_section.shape[1])
        
        im0 = axes[0].pcolormesh(lon, self.standard_depths, obs_section, cmap='viridis', shading='auto')
        axes[0].set_ylabel('Depth (m)')
        axes[0].set_title(f'Observed {label}')
        axes[0].invert_yaxis()
        plt.colorbar(im0, ax=axes[0], label=units)
        
        im1 = axes[1].pcolormesh(lon, self.standard_depths, model_section, cmap='viridis', shading='auto')
        axes[1].set_ylabel('Depth (m)')
        axes[1].set_title(f'Model {label}')
        axes[1].invert_yaxis()
        plt.colorbar(im1, ax=axes[1], label=units)
        
        vmax = np.nanpercentile(np.abs(diff_section), 95)
        im2 = axes[2].pcolormesh(lon, self.standard_depths, diff_section, 
                                  cmap='RdBu_r', vmin=-vmax, vmax=vmax, shading='auto')
        axes[2].set_xlabel('Longitude Index')
        axes[2].set_ylabel('Depth (m)')
        axes[2].set_title('Difference (Model - Observed)')
        axes[2].invert_yaxis()
        plt.colorbar(im2, ax=axes[2], label=units)
        
        plt.tight_layout()
        plt.savefig(output_dir / 'depth_sections.png', dpi=300, bbox_inches='tight')
        plt.close()

    def save_statistics_report(self):
        """Save statistics report."""
        report_file = self.output_dir / "statistics_report.txt"
        
        with open(report_file, 'w') as f:
            f.write("=" * 80 + "\n")
            f.write("DEPTH-RESOLVED ANALYSIS REPORT\n")
            f.write("=" * 80 + "\n\n")
            
            for config in self.datasets:
                var_key = config['variable']
                stats = self.stats[var_key]
                
                f.write(f"VARIABLE: {config['variable_label']}\n")
                f.write("-" * 80 + "\n")
                f.write(f"Overall Bias: {stats['bias']:.4f}\n")
                f.write(f"Overall RMSE: {stats['rmse']:.4f}\n")
                f.write(f"Overall Correlation: {stats['correlation']:.4f}\n\n")
                
                f.write("Depth-dependent statistics (every 5th level):\n")
                for i in range(0, len(self.standard_depths), 5):
                    d = self.standard_depths[i]
                    bias = stats['depth_bias'][i]
                    rmse = stats['depth_rmse'][i]
                    f.write(f"  {d:6.0f}m: Bias={bias:8.4f} RMSE={rmse:8.4f}\n")
                
                f.write("\n")

    def save_netcdf_outputs(self):
        """Save NetCDF outputs."""
        print("\nSaving NetCDF outputs...")
        
        for config in self.datasets:
            var_dir = self.output_dir / config['variable']
            var_dir.mkdir(exist_ok=True)
            
            if config['regridded_ds'] is not None:
                file = var_dir / f"{config['variable']}_regridded.nc"
                config['regridded_ds'].to_netcdf(file)
                print(f"  Saved: {file}")
