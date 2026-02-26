#!/usr/bin/env python3
"""
Comprehensive Statistical Analysis of Observed and Modeled Data
Handles multiple files per variable with wildcard support and advanced regridding.
Supports xESMF regridding methods and bidirectional regridding.
"""

import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from scipy.spatial import cKDTree
import pandas as pd
from pathlib import Path
import warnings
from typing import List, Dict, Optional, Tuple
import glob

warnings.filterwarnings("ignore")

# Check for xESMF availability
try:
    import xesmf as xe
    XESMF_AVAILABLE = True
except ImportError:
    XESMF_AVAILABLE = False
    print("Warning: xESMF not available. Only 'nearest' and 'bilinear' methods will work.")
    print("Install xESMF for conservative regridding: pip install xesmf")


class MultiVariableAnalyzer:
    """
    Statistical analyzer for observed and modeled data on different grids.
    Supports multiple files per variable with wildcard patterns and advanced regridding.
    """

    def __init__(self, output_dir="./analysis_output"):
        """
        Initialize the analyzer.

        Parameters:
        -----------
        output_dir : str
            Directory for output files
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)

        # Storage for multiple datasets
        self.obs_datasets = []
        
        # Statistics storage
        self.stats = {}
        
        # Common unit conversions
        self.unit_conversions = {
            'kelvin': {'offset': -273.15, 'target': 'celsius'},
            'k': {'offset': -273.15, 'target': 'celsius'},
        }
        
        # Regridding method and direction
        self.regrid_method = 'nearest'
        self.regrid_direction = 'obs_to_model'

    def add_dataset_pair(
        self,
        obs_folder: str,
        model_folder: str,
        obs_pattern: str,
        model_pattern: str,
        variable: str,
        obs_varname: Optional[str] = None,
        model_varname: Optional[str] = None,
        obs_lat: Optional[str] = None,
        obs_lon: Optional[str] = None,
        model_lat: Optional[str] = None,
        model_lon: Optional[str] = None,
        variable_label: Optional[str] = None,
        expected_units: Optional[str] = None,
    ):
        """
        Add a pair of observed and model file patterns for analysis.

        Parameters:
        -----------
        obs_folder : str
            Folder containing observed data files
        model_folder : str
            Folder containing model data files
        obs_pattern : str
            File pattern for observed files (e.g., 'sst_*.nc' or 'sst_2020.nc sst_2021.nc')
        model_pattern : str
            File pattern for model files (e.g., 'model_sst_*.nc')
        variable : str
            Variable identifier (e.g., 'sst', 'salinity', 'temperature')
        obs_varname : str, optional
            Variable name in observed file (auto-detected if None)
        model_varname : str, optional
            Variable name in model file (auto-detected if None)
        obs_lat : str, optional
            Latitude coordinate name in observed data
        obs_lon : str, optional
            Longitude coordinate name in observed data
        model_lat : str, optional
            Latitude coordinate name in model data
        model_lon : str, optional
            Longitude coordinate name in model data
        variable_label : str, optional
            Display label for the variable (e.g., 'Sea Surface Temperature')
        expected_units : str, optional
            Expected units for the variable (e.g., 'degrees_celsius', 'psu')
        """
        # Expand file patterns to get list of files
        obs_files = self._expand_file_pattern(obs_folder, obs_pattern)
        model_files = self._expand_file_pattern(model_folder, model_pattern)
        
        if len(obs_files) == 0:
            raise ValueError(f"No observed files found matching: {obs_folder}/{obs_pattern}")
        if len(model_files) == 0:
            raise ValueError(f"No model files found matching: {model_folder}/{model_pattern}")
        
        dataset_config = {
            'obs_files': obs_files,
            'model_files': model_files,
            'variable': variable,
            'obs_varname': obs_varname,
            'model_varname': model_varname,
            'obs_lat': obs_lat,
            'obs_lon': obs_lon,
            'model_lat': model_lat,
            'model_lon': model_lon,
            'variable_label': variable_label or variable.upper(),
            'expected_units': expected_units,
            'obs_ds': None,
            'model_ds': None,
            'regridded_ds': None,  # Will hold the regridded dataset
        }
        
        self.obs_datasets.append(dataset_config)
    
    def _expand_file_pattern(self, folder: str, pattern: str) -> List[str]:
        """
        Expand file pattern to list of files.
        
        Parameters:
        -----------
        folder : str
            Base folder path
        pattern : str
            File pattern (can be wildcard like '*.nc' or space-separated list)
            
        Returns:
        --------
        List[str] : Sorted list of matching file paths
        """
        folder_path = Path(folder)
        
        # Handle space-separated list of patterns
        patterns = pattern.split()
        
        all_files = []
        for pat in patterns:
            # Construct full path pattern
            full_pattern = str(folder_path / pat)
            
            # Expand wildcards
            matched_files = glob.glob(full_pattern)
            all_files.extend(matched_files)
        
        # Remove duplicates and sort
        all_files = sorted(set(all_files))
        
        return all_files
        
    def load_all_data(self):
        """Load all dataset pairs."""
        print("\n" + "=" * 80)
        print("LOADING DATASETS")
        print("=" * 80)
        
        for idx, config in enumerate(self.obs_datasets):
            print(f"\nDataset pair {idx + 1}: {config['variable']}")
            print(f"  Observed files ({len(config['obs_files'])}):")
            for f in config['obs_files']:
                print(f"    - {f}")
            print(f"  Model files ({len(config['model_files'])}):")
            for f in config['model_files']:
                print(f"    - {f}")
            
            # Load and concatenate datasets
            print(f"  Loading observed files...")
            config['obs_ds'] = self._load_and_concatenate(
                config['obs_files'], 
                "observed"
            )
            
            print(f"  Loading model files...")
            config['model_ds'] = self._load_and_concatenate(
                config['model_files'],
                "model"
            )
            
            # Auto-detect variable names if not provided
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
            print(f"  Observed shape: {config['obs_ds'][config['obs_varname']].shape}")
            print(f"  Model shape: {config['model_ds'][config['model_varname']].shape}")
            
            # Check and convert units
            self._check_and_convert_units(config)
    
    def _load_and_concatenate(self, file_list: List[str], data_type: str) -> xr.Dataset:
        """
        Load multiple NetCDF files and concatenate along time dimension.
        
        Parameters:
        -----------
        file_list : List[str]
            List of file paths
        data_type : str
            Description for progress messages
            
        Returns:
        --------
        xr.Dataset : Concatenated dataset
        """
        if len(file_list) == 1:
            return xr.open_dataset(file_list[0])
        
        # Open all files as a multi-file dataset
        try:
            # Try using open_mfdataset for efficiency
            ds = xr.open_mfdataset(
                file_list,
                combine='by_coords',
                parallel=False,
                chunks=None
            )
            print(f"    Successfully opened {len(file_list)} files with open_mfdataset")
            return ds
        except Exception as e:
            print(f"    open_mfdataset failed ({str(e)}), trying sequential loading...")
            
            # Fallback: load and concatenate manually
            datasets = []
            for i, file_path in enumerate(file_list):
                print(f"    Loading file {i+1}/{len(file_list)}: {Path(file_path).name}")
                ds = xr.open_dataset(file_path)
                datasets.append(ds)
            
            # Concatenate along time dimension
            # First, identify the time dimension
            time_dims = []
            for dim in datasets[0].dims:
                if 'time' in dim.lower():
                    time_dims.append(dim)
            
            if len(time_dims) == 0:
                raise ValueError("Could not identify time dimension for concatenation")
            
            time_dim = time_dims[0]
            print(f"    Concatenating along dimension: {time_dim}")
            
            combined_ds = xr.concat(datasets, dim=time_dim)
            
            # Sort by time if possible
            try:
                combined_ds = combined_ds.sortby(time_dim)
            except:
                print(f"    Warning: Could not sort by {time_dim}")
            
            return combined_ds

    def _detect_variable(self, ds: xr.Dataset, variable_hint: str) -> str:
        """
        Auto-detect variable name from dataset.
        
        Parameters:
        -----------
        ds : xr.Dataset
            Dataset to search
        variable_hint : str
            Hint for variable name (e.g., 'sst', 'salinity')
        
        Returns:
        --------
        str : Variable name
        """
        # Common variable name patterns
        variable_patterns = {
            'sst': ['sst', 'SST', 'sea_surface_temperature', 'tos', 'analysed_sst', 'temp', 'temperature'],
            'salinity': ['salinity', 'sal', 'so', 'salt', 'salin'],
            'temperature': ['temp', 'temperature', 'thetao', 't', 'theta'],
            'ssh': ['ssh', 'SSH', 'sea_surface_height', 'zos', 'eta'],
            'velocity_u': ['u', 'uo', 'u_velocity', 'uvel', 'U'],
            'velocity_v': ['v', 'vo', 'v_velocity', 'vvel', 'V'],
            'chlorophyll': ['chl', 'chlor', 'chlorophyll', 'chla', 'CHL'],
        }
        
        # Get patterns for this variable type
        patterns = variable_patterns.get(variable_hint.lower(), [variable_hint])
        
        # First try exact matches
        for pattern in patterns:
            if pattern in ds.variables:
                return pattern
        
        # Then try case-insensitive search
        for var in ds.variables:
            for pattern in patterns:
                if pattern.lower() in var.lower():
                    # Check if it has time dimension (likely the main variable)
                    if 'time' in ds[var].dims:
                        return var
        
        raise ValueError(
            f"Could not auto-detect variable matching '{variable_hint}'. "
            f"Available variables: {list(ds.variables.keys())}"
        )

    def _check_and_convert_units(self, config: Dict):
        """
        Check and convert variable units if needed.
        
        Parameters:
        -----------
        config : dict
            Dataset configuration dictionary
        """
        obs_var = config['obs_ds'][config['obs_varname']]
        model_var = config['model_ds'][config['model_varname']]
        
        # Get units
        obs_units = getattr(obs_var, 'units', 'unknown')
        model_units = getattr(model_var, 'units', 'unknown')
        
        print(f"  Units - Observed: {obs_units}, Model: {model_units}")
        
        # Check for temperature conversion (Kelvin to Celsius)
        if obs_units.lower() in ['kelvin', 'k']:
            print("  Converting observed data from Kelvin to Celsius...")
            config['obs_ds'][config['obs_varname']] = (
                config['obs_ds'][config['obs_varname']] - 273.15
            )
            config['obs_ds'][config['obs_varname']].attrs['units'] = 'degrees_Celsius'
            
        if model_units.lower() in ['kelvin', 'k']:
            print("  Converting model data from Kelvin to Celsius...")
            config['model_ds'][config['model_varname']] = (
                config['model_ds'][config['model_varname']] - 273.15
            )
            config['model_ds'][config['model_varname']].attrs['units'] = 'degrees_Celsius'

    def _identify_coordinates(self, ds: xr.Dataset) -> Tuple[str, str]:
        """
        Identify latitude and longitude coordinate names.

        Parameters:
        -----------
        ds : xr.Dataset
            Dataset to search

        Returns:
        --------
        tuple : (lat_name, lon_name)
        """
        lat_names = [
            "lat", "latitude", "y", "nav_lat", "LATITUDE", "Latitude",
            "lat_rho", "lat_u", "lat_v", "TLAT", "ULAT", "geolat",
            "yt_ocean", "ylat",
        ]
        lon_names = [
            "lon", "longitude", "x", "nav_lon", "LONGITUDE", "Longitude",
            "lon_rho", "lon_u", "lon_v", "TLONG", "ULONG", "geolon",
            "xt_ocean", "xlon",
        ]

        lat_name = None
        lon_name = None

        # Check for latitude
        for name in lat_names:
            if name in ds.coords or name in ds.variables:
                lat_name = name
                break

        # If not found, look for variables with 'lat' in the name
        if lat_name is None:
            for var in list(ds.coords) + list(ds.variables):
                if "lat" in var.lower() and var.lower() not in ["latitude_longitude"]:
                    if hasattr(ds[var], "dims") and len(ds[var].dims) in [1, 2]:
                        lat_name = var
                        break

        # Check for longitude
        for name in lon_names:
            if name in ds.coords or name in ds.variables:
                lon_name = name
                break

        # If not found, look for variables with 'lon' in the name
        if lon_name is None:
            for var in list(ds.coords) + list(ds.variables):
                if "lon" in var.lower() and var.lower() not in ["latitude_longitude"]:
                    if hasattr(ds[var], "dims") and len(ds[var].dims) in [1, 2]:
                        lon_name = var
                        break

        if lat_name is None or lon_name is None:
            raise ValueError(
                f"Could not identify lat/lon coordinates. "
                f"Available coords: {list(ds.coords.keys())}, "
                f"Available vars: {list(ds.variables.keys())}"
            )

        return lat_name, lon_name

    def regrid_data(self, method: str = "nearest", direction: str = "obs_to_model"):
        """
        Regrid datasets based on specified direction.
        
        Parameters:
        -----------
        method : str
            Regridding method:
            - 'nearest': Nearest neighbor (scipy-based, fast)
            - 'bilinear': Bilinear interpolation (scipy-based)
            - 'conservative': Conservative regridding (xESMF, preserves integrals)
            - 'conservative_normed': Normalized conservative (xESMF)
            - 'patch': Patch recovery (xESMF, higher order)
        direction : str
            Regridding direction:
            - 'obs_to_model': Regrid observations to model grid
            - 'model_to_obs': Regrid model to observation grid
        """
        self.regrid_method = method
        self.regrid_direction = direction
        
        # Validate method
        if method in ['conservative', 'conservative_normed', 'patch'] and not XESMF_AVAILABLE:
            raise ValueError(
                f"Method '{method}' requires xESMF. Install with: pip install xesmf\n"
                "Available methods without xESMF: 'nearest', 'bilinear'"
            )
        
        print("\n" + "=" * 80)
        print(f"REGRIDDING DATA: {direction.upper()}")
        print(f"Method: {method}")
        print("=" * 80)
        
        for idx, config in enumerate(self.obs_datasets):
            print(f"\nRegridding dataset {idx + 1}: {config['variable']}")
            
            # Identify coordinates if not provided
            if config['obs_lat'] is None or config['obs_lon'] is None:
                obs_lat, obs_lon = self._identify_coordinates(config['obs_ds'])
                config['obs_lat'] = obs_lat
                config['obs_lon'] = obs_lon
                
            if config['model_lat'] is None or config['model_lon'] is None:
                model_lat, model_lon = self._identify_coordinates(config['model_ds'])
                config['model_lat'] = model_lat
                config['model_lon'] = model_lon
            
            print(f"  Observed coords: {config['obs_lat']}, {config['obs_lon']}")
            print(f"  Model coords: {config['model_lat']}, {config['model_lon']}")
            
            # Perform regridding based on direction
            if direction == 'obs_to_model':
                print(f"  Regridding observations to model grid...")
                config['regridded_ds'] = self._regrid_dataset(
                    config['obs_ds'],
                    config['model_ds'],
                    config['obs_varname'],
                    config['obs_lat'],
                    config['obs_lon'],
                    config['model_lat'],
                    config['model_lon'],
                    method=method
                )
                print(f"  Regridded shape: {config['regridded_ds'][config['obs_varname']].shape}")
            else:  # model_to_obs
                print(f"  Regridding model to observation grid...")
                config['regridded_ds'] = self._regrid_dataset(
                    config['model_ds'],
                    config['obs_ds'],
                    config['model_varname'],
                    config['model_lat'],
                    config['model_lon'],
                    config['obs_lat'],
                    config['obs_lon'],
                    method=method
                )
                print(f"  Regridded shape: {config['regridded_ds'][config['model_varname']].shape}")

    def _regrid_dataset(
        self,
        source_ds: xr.Dataset,
        target_ds: xr.Dataset,
        varname: str,
        source_lat: str,
        source_lon: str,
        target_lat: str,
        target_lon: str,
        method: str = "nearest"
    ) -> xr.Dataset:
        """
        Regrid source dataset to target grid.
        
        Uses xESMF for conservative methods, scipy-based for nearest/bilinear.
        """
        if method in ['conservative', 'conservative_normed', 'patch']:
            return self._regrid_xesmf(
                source_ds, target_ds, varname,
                source_lat, source_lon, target_lat, target_lon,
                method
            )
        else:
            return self._regrid_scipy(
                source_ds, target_ds, varname,
                source_lat, source_lon, target_lat, target_lon,
                method
            )

    def _regrid_xesmf(
        self,
        source_ds: xr.Dataset,
        target_ds: xr.Dataset,
        varname: str,
        source_lat: str,
        source_lon: str,
        target_lat: str,
        target_lon: str,
        method: str
    ) -> xr.Dataset:
        """
        Regrid using xESMF for conservative methods.
        """
        print(f"  Using xESMF {method} regridding...")
        
        # Get time dimension
        time_dim = None
        for dim in source_ds[varname].dims:
            if 'time' in dim.lower():
                time_dim = dim
                break
        
        if time_dim is None:
            raise ValueError(f"Could not find time dimension in {varname}")
        
        # Prepare source grid
        source_grid = xr.Dataset({
            'lat': source_ds[source_lat],
            'lon': source_ds[source_lon],
        })
        
        # Prepare target grid
        target_grid = xr.Dataset({
            'lat': target_ds[target_lat],
            'lon': target_ds[target_lon],
        })
        
        # Create regridder
        print(f"  Building regridder...")
        regridder = xe.Regridder(
            source_grid, 
            target_grid, 
            method,
            periodic=False,
            ignore_degenerate=True
        )
        
        # Get target coordinate arrays
        target_lat_data = target_ds[target_lat].values
        target_lon_data = target_ds[target_lon].values
        
        # Regrid the data
        print(f"  Regridding variable {varname}...")
        source_data = source_ds[varname]
        regridded_data = regridder(source_data)
        
        # Create output dataset
        regridded_ds = xr.Dataset(
            {varname: regridded_data},
            coords={
                time_dim: source_ds[time_dim],
                target_lat: target_lat_data,
                target_lon: target_lon_data,
            }
        )
        
        # Copy attributes
        regridded_ds[varname].attrs = source_ds[varname].attrs
        
        # Clean up regridder
        regridder.clean_weight_file()
        
        return regridded_ds

    def _regrid_scipy(
        self,
        source_ds: xr.Dataset,
        target_ds: xr.Dataset,
        varname: str,
        source_lat: str,
        source_lon: str,
        target_lat: str,
        target_lon: str,
        method: str = "nearest"
    ) -> xr.Dataset:
        """
        Regrid using scipy-based interpolation (nearest, bilinear).
        """
        print(f"  Using scipy {method} regridding...")
        
        # Get coordinate arrays
        source_lat_data = source_ds[source_lat].values
        source_lon_data = source_ds[source_lon].values
        target_lat_data = target_ds[target_lat].values
        target_lon_data = target_ds[target_lon].values
        
        # Handle different coordinate shapes
        if source_lat_data.ndim == 1 and source_lon_data.ndim == 1:
            source_lon_grid, source_lat_grid = np.meshgrid(source_lon_data, source_lat_data)
        else:
            source_lat_grid = source_lat_data
            source_lon_grid = source_lon_data
            
        if target_lat_data.ndim == 1 and target_lon_data.ndim == 1:
            target_lon_grid, target_lat_grid = np.meshgrid(target_lon_data, target_lat_data)
        else:
            target_lat_grid = target_lat_data
            target_lon_grid = target_lon_data
        
        # Flatten coordinates
        source_points = np.column_stack([
            source_lat_grid.flatten(),
            source_lon_grid.flatten()
        ])
        target_points = np.column_stack([
            target_lat_grid.flatten(),
            target_lon_grid.flatten()
        ])
        
        # Build KD-tree
        tree = cKDTree(source_points)
        distances, indices = tree.query(target_points, k=1 if method == "nearest" else 4)
        
        # Get time dimension
        time_dim = None
        for dim in source_ds[varname].dims:
            if 'time' in dim.lower():
                time_dim = dim
                break
        
        if time_dim is None:
            raise ValueError(f"Could not find time dimension in {varname}")
        
        # Regrid each time step
        source_data = source_ds[varname].values
        n_times = source_data.shape[0]
        regridded_shape = (n_times,) + target_lat_grid.shape
        regridded_data = np.zeros(regridded_shape)
        
        print(f"  Processing {n_times} time steps...")
        for t in range(n_times):
            if (t + 1) % 50 == 0 or t == 0:
                print(f"    Time step {t + 1}/{n_times}")
            
            source_flat = source_data[t].flatten()
            
            if method == "nearest":
                target_flat = source_flat[indices]
            else:  # bilinear
                weights = 1.0 / (distances + 1e-10)
                weights = weights / weights.sum(axis=1, keepdims=True)
                target_flat = np.sum(source_flat[indices] * weights, axis=1)
            
            regridded_data[t] = target_flat.reshape(target_lat_grid.shape)
        
        # Create regridded dataset
        regridded_ds = xr.Dataset(
            {varname: ([time_dim, target_lat, target_lon], regridded_data)},
            coords={
                time_dim: source_ds[time_dim],
                target_lat: target_lat_data,
                target_lon: target_lon_data,
            }
        )
        
        # Copy attributes
        regridded_ds[varname].attrs = source_ds[varname].attrs
        
        return regridded_ds

    def compute_statistics(self):
        """Compute statistics for all dataset pairs."""
        print("\n" + "=" * 80)
        print("COMPUTING STATISTICS")
        print("=" * 80)
        
        for idx, config in enumerate(self.obs_datasets):
            print(f"\nComputing statistics for dataset {idx + 1}: {config['variable']}")
            
            var_key = config['variable']
            obs_var = config['obs_varname']
            model_var = config['model_varname']
            
            # Get data arrays based on regridding direction
            if self.regrid_direction == 'obs_to_model':
                obs_data = config['regridded_ds'][obs_var].values
                model_data = config['model_ds'][model_var].values
                reference_grid = 'model'
            else:  # model_to_obs
                obs_data = config['obs_ds'][obs_var].values
                model_data = config['regridded_ds'][model_var].values
                reference_grid = 'observation'
            
            print(f"  Comparison on {reference_grid} grid")
            
            # Ensure same shape
            if obs_data.shape != model_data.shape:
                raise ValueError(
                    f"Shape mismatch after regridding: "
                    f"obs {obs_data.shape} vs model {model_data.shape}"
                )
            
            # Create mask for valid data
            valid_mask = ~(np.isnan(obs_data) | np.isnan(model_data))
            
            # Global statistics
            self.stats[var_key] = {}
            self.stats[var_key]['obs_mean'] = np.nanmean(obs_data)
            self.stats[var_key]['obs_std'] = np.nanstd(obs_data)
            self.stats[var_key]['obs_min'] = np.nanmin(obs_data)
            self.stats[var_key]['obs_max'] = np.nanmax(obs_data)
            self.stats[var_key]['obs_quantiles'] = {
                q: np.nanpercentile(obs_data, q * 100)
                for q in [0.05, 0.25, 0.5, 0.75, 0.95]
            }
            
            self.stats[var_key]['model_mean'] = np.nanmean(model_data)
            self.stats[var_key]['model_std'] = np.nanstd(model_data)
            self.stats[var_key]['model_min'] = np.nanmin(model_data)
            self.stats[var_key]['model_max'] = np.nanmax(model_data)
            self.stats[var_key]['model_quantiles'] = {
                q: np.nanpercentile(model_data, q * 100)
                for q in [0.05, 0.25, 0.5, 0.75, 0.95]
            }
            
            # Difference statistics
            diff = model_data - obs_data
            self.stats[var_key]['bias'] = np.nanmean(diff)
            self.stats[var_key]['rmse'] = np.sqrt(np.nanmean(diff**2))
            self.stats[var_key]['mae'] = np.nanmean(np.abs(diff))
            
            # Correlation
            obs_flat = obs_data[valid_mask].flatten()
            model_flat = model_data[valid_mask].flatten()
            if len(obs_flat) > 0:
                self.stats[var_key]['correlation'] = np.corrcoef(obs_flat, model_flat)[0, 1]
            else:
                self.stats[var_key]['correlation'] = np.nan
            
            # Spatial statistics (time-averaged)
            obs_mean_spatial = np.nanmean(obs_data, axis=0)
            model_mean_spatial = np.nanmean(model_data, axis=0)
            diff_spatial = model_mean_spatial - obs_mean_spatial
            
            config['spatial_bias'] = diff_spatial
            config['spatial_rmse'] = np.sqrt((diff**2).mean(axis=0))
            
            # Temporal correlation at each grid point
            n_times, n_lat, n_lon = obs_data.shape
            spatial_corr = np.zeros((n_lat, n_lon))
            for i in range(n_lat):
                for j in range(n_lon):
                    obs_ts = obs_data[:, i, j]
                    model_ts = model_data[:, i, j]
                    valid = ~(np.isnan(obs_ts) | np.isnan(model_ts))
                    if valid.sum() > 2:
                        spatial_corr[i, j] = np.corrcoef(obs_ts[valid], model_ts[valid])[0, 1]
                    else:
                        spatial_corr[i, j] = np.nan
            
            config['spatial_corr'] = spatial_corr
            
            # Monthly statistics
            if self.regrid_direction == 'obs_to_model':
                time_coord = list(config['regridded_ds'].dims.keys())[0]
                times = config['regridded_ds'][time_coord].values
            else:
                time_coord = list(config['obs_ds'].dims.keys())[0]
                times = config['obs_ds'][time_coord].values
            
            try:
                months = pd.to_datetime(times).month
                monthly_bias = {}
                monthly_rmse = {}
                
                for month in range(1, 13):
                    mask = months == month
                    if mask.sum() > 0:
                        monthly_diff = diff[mask]
                        monthly_bias[month] = np.nanmean(monthly_diff)
                        monthly_rmse[month] = np.sqrt(np.nanmean(monthly_diff**2))
                    else:
                        monthly_bias[month] = np.nan
                        monthly_rmse[month] = np.nan
                
                self.stats[var_key]['monthly_bias'] = monthly_bias
                self.stats[var_key]['monthly_rmse'] = monthly_rmse
            except:
                print("  Warning: Could not compute monthly statistics")
                self.stats[var_key]['monthly_bias'] = {}
                self.stats[var_key]['monthly_rmse'] = {}
            
            print(f"  Mean bias: {self.stats[var_key]['bias']:.4f}")
            print(f"  RMSE: {self.stats[var_key]['rmse']:.4f}")
            print(f"  Correlation: {self.stats[var_key]['correlation']:.4f}")

    def create_visualizations(self):
        """Create visualization plots for all variables."""
        print("\n" + "=" * 80)
        print("CREATING VISUALIZATIONS")
        print("=" * 80)
        
        for idx, config in enumerate(self.obs_datasets):
            print(f"\nCreating plots for dataset {idx + 1}: {config['variable']}")
            
            var_key = config['variable']
            var_label = config['variable_label']
            
            # Create variable-specific output directory
            var_output_dir = self.output_dir / var_key
            var_output_dir.mkdir(exist_ok=True)
            
            # Get units for labels
            if self.regrid_direction == 'obs_to_model':
                units = getattr(config['obs_ds'][config['obs_varname']], 'units', '')
            else:
                units = getattr(config['model_ds'][config['model_varname']], 'units', '')
            
            # 1. Spatial maps
            self._plot_spatial_maps(config, var_output_dir, var_label, units)
            
            # 2. Time series
            self._plot_time_series(config, var_output_dir, var_label, units)
            
            # 3. Statistics plots
            self._plot_statistics(config, var_output_dir, var_label, units)
            
            print(f"  Plots saved to: {var_output_dir}")

    def _plot_spatial_maps(self, config, output_dir, var_label, units):
        """Create spatial map visualizations."""
        obs_var = config['obs_varname']
        model_var = config['model_varname']
        
        # Time-averaged maps - use data on common grid
        if self.regrid_direction == 'obs_to_model':
            time_dim = list(config['regridded_ds'].dims.keys())[0]
            obs_mean = config['regridded_ds'][obs_var].mean(dim=time_dim)
            model_mean = config['model_ds'][model_var].mean(dim=list(config['model_ds'].dims.keys())[0])
        else:
            time_dim = list(config['obs_ds'].dims.keys())[0]
            obs_mean = config['obs_ds'][obs_var].mean(dim=time_dim)
            model_mean = config['regridded_ds'][model_var].mean(dim=list(config['regridded_ds'].dims.keys())[0])
        
        fig, axes = plt.subplots(2, 2, figsize=(15, 12))
        
        # Observed
        im1 = axes[0, 0].pcolormesh(obs_mean, cmap='coolwarm')
        axes[0, 0].set_title(f'Observed {var_label} (Time Mean)')
        plt.colorbar(im1, ax=axes[0, 0], label=units)
        
        # Model
        im2 = axes[0, 1].pcolormesh(model_mean, cmap='coolwarm')
        axes[0, 1].set_title(f'Model {var_label} (Time Mean)')
        plt.colorbar(im2, ax=axes[0, 1], label=units)
        
        # Bias
        im3 = axes[1, 0].pcolormesh(config['spatial_bias'], cmap='RdBu_r', 
                                     vmin=-np.nanstd(config['spatial_bias'])*3,
                                     vmax=np.nanstd(config['spatial_bias'])*3)
        axes[1, 0].set_title(f'Bias (Model - Observed)')
        plt.colorbar(im3, ax=axes[1, 0], label=units)
        
        # RMSE
        im4 = axes[1, 1].pcolormesh(config['spatial_rmse'], cmap='Reds')
        axes[1, 1].set_title('Root Mean Square Error')
        plt.colorbar(im4, ax=axes[1, 1], label=units)
        
        plt.tight_layout()
        plt.savefig(output_dir / 'spatial_maps.png', dpi=300, bbox_inches='tight')
        plt.close()

    def _plot_time_series(self, config, output_dir, var_label, units):
        """Create time series plots."""
        obs_var = config['obs_varname']
        model_var = config['model_varname']
        
        # Get lat/lon coordinate names based on grid
        if self.regrid_direction == 'obs_to_model':
            lat_coord = config['model_lat']
            lon_coord = config['model_lon']
            obs_ts = config['regridded_ds'][obs_var].mean(dim=[lat_coord, lon_coord])
            model_ts = config['model_ds'][model_var].mean(dim=[lat_coord, lon_coord])
            time_coord = list(config['regridded_ds'].dims.keys())[0]
            times = config['regridded_ds'][time_coord].values
        else:
            lat_coord = config['obs_lat']
            lon_coord = config['obs_lon']
            obs_ts = config['obs_ds'][obs_var].mean(dim=[lat_coord, lon_coord])
            model_ts = config['regridded_ds'][model_var].mean(dim=[lat_coord, lon_coord])
            time_coord = list(config['obs_ds'].dims.keys())[0]
            times = config['obs_ds'][time_coord].values
        
        fig, axes = plt.subplots(2, 1, figsize=(12, 8))
        
        # Time series comparison
        axes[0].plot(times, obs_ts, label='Observed', linewidth=1, alpha=0.7)
        axes[0].plot(times, model_ts, label='Model', linewidth=1, alpha=0.7)
        axes[0].set_ylabel(f'{var_label} ({units})')
        axes[0].set_title(f'Spatial Mean {var_label} Time Series')
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)
        
        # Difference
        diff_ts = model_ts - obs_ts
        axes[1].plot(times, diff_ts, color='red', linewidth=1)
        axes[1].axhline(y=0, color='black', linestyle='--', linewidth=0.5)
        axes[1].set_ylabel(f'Bias ({units})')
        axes[1].set_title('Model - Observed Difference')
        axes[1].grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(output_dir / 'time_series.png', dpi=300, bbox_inches='tight')
        plt.close()

    def _plot_statistics(self, config, output_dir, var_label, units):
        """Create statistical comparison plots."""
        obs_var = config['obs_varname']
        model_var = config['model_varname']
        
        # Get data based on regridding direction
        if self.regrid_direction == 'obs_to_model':
            obs_data = config['regridded_ds'][obs_var].values.flatten()
            model_data = config['model_ds'][model_var].values.flatten()
        else:
            obs_data = config['obs_ds'][obs_var].values.flatten()
            model_data = config['regridded_ds'][model_var].values.flatten()
        
        # Remove NaN values
        valid = ~(np.isnan(obs_data) | np.isnan(model_data))
        obs_data = obs_data[valid]
        model_data = model_data[valid]
        
        fig, axes = plt.subplots(2, 2, figsize=(12, 10))
        
        # Scatter plot
        axes[0, 0].hexbin(obs_data, model_data, gridsize=50, cmap='Blues', mincnt=1)
        lims = [min(obs_data.min(), model_data.min()), 
                max(obs_data.max(), model_data.max())]
        axes[0, 0].plot(lims, lims, 'r--', linewidth=2, label='1:1 line')
        axes[0, 0].set_xlabel(f'Observed {var_label} ({units})')
        axes[0, 0].set_ylabel(f'Model {var_label} ({units})')
        axes[0, 0].set_title('Scatter Plot')
        axes[0, 0].legend()
        
        # Distributions
        axes[0, 1].hist(obs_data, bins=50, alpha=0.5, label='Observed', density=True)
        axes[0, 1].hist(model_data, bins=50, alpha=0.5, label='Model', density=True)
        axes[0, 1].set_xlabel(f'{var_label} ({units})')
        axes[0, 1].set_ylabel('Density')
        axes[0, 1].set_title('Distribution Comparison')
        axes[0, 1].legend()
        
        # Q-Q plot
        quantiles = np.linspace(0, 100, 100)
        obs_quantiles = np.percentile(obs_data, quantiles)
        model_quantiles = np.percentile(model_data, quantiles)
        axes[1, 0].scatter(obs_quantiles, model_quantiles, alpha=0.5, s=10)
        lims = [min(obs_quantiles.min(), model_quantiles.min()),
                max(obs_quantiles.max(), model_quantiles.max())]
        axes[1, 0].plot(lims, lims, 'r--', linewidth=2)
        axes[1, 0].set_xlabel(f'Observed Quantiles ({units})')
        axes[1, 0].set_ylabel(f'Model Quantiles ({units})')
        axes[1, 0].set_title('Q-Q Plot')
        
        # Error distribution
        errors = model_data - obs_data
        axes[1, 1].hist(errors, bins=50, edgecolor='black', alpha=0.7)
        axes[1, 1].axvline(x=0, color='red', linestyle='--', linewidth=2)
        axes[1, 1].set_xlabel(f'Error (Model - Observed) ({units})')
        axes[1, 1].set_ylabel('Frequency')
        axes[1, 1].set_title('Error Distribution')
        
        plt.tight_layout()
        plt.savefig(output_dir / 'statistics.png', dpi=300, bbox_inches='tight')
        plt.close()

    def save_statistics_report(self):
        """Save comprehensive statistics report."""
        report_file = self.output_dir / "statistics_report.txt"
        
        print(f"\nSaving statistics report to: {report_file}")
        
        with open(report_file, 'w') as f:
            f.write("=" * 80 + "\n")
            f.write("MULTI-VARIABLE STATISTICAL ANALYSIS REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Regridding method: {self.regrid_method}\n")
            f.write(f"Regridding direction: {self.regrid_direction}\n")
            f.write("=" * 80 + "\n\n")
            
            for idx, config in enumerate(self.obs_datasets):
                var_key = config['variable']
                var_label = config['variable_label']
                
                f.write("=" * 80 + "\n")
                f.write(f"VARIABLE: {var_label}\n")
                f.write("=" * 80 + "\n\n")
                
                f.write("Observed Files:\n")
                for fpath in config['obs_files']:
                    f.write(f"  {fpath}\n")
                f.write("\nModel Files:\n")
                for fpath in config['model_files']:
                    f.write(f"  {fpath}\n")
                f.write("\n")
                
                stats = self.stats[var_key]
                
                f.write("OBSERVED STATISTICS\n")
                f.write("-" * 80 + "\n")
                f.write(f"Mean:                           {stats['obs_mean']:12.4f}\n")
                f.write(f"Standard Deviation:             {stats['obs_std']:12.4f}\n")
                f.write(f"Minimum:                        {stats['obs_min']:12.4f}\n")
                f.write(f"Maximum:                        {stats['obs_max']:12.4f}\n\n")
                
                f.write("Quantiles:\n")
                for q, val in stats['obs_quantiles'].items():
                    f.write(f"  {q:5.2%}:                         {val:12.4f}\n")
                f.write("\n")
                
                f.write("MODEL STATISTICS\n")
                f.write("-" * 80 + "\n")
                f.write(f"Mean:                           {stats['model_mean']:12.4f}\n")
                f.write(f"Standard Deviation:             {stats['model_std']:12.4f}\n")
                f.write(f"Minimum:                        {stats['model_min']:12.4f}\n")
                f.write(f"Maximum:                        {stats['model_max']:12.4f}\n\n")
                
                f.write("Quantiles:\n")
                for q, val in stats['model_quantiles'].items():
                    f.write(f"  {q:5.2%}:                         {val:12.4f}\n")
                f.write("\n")
                
                f.write("COMPARISON STATISTICS\n")
                f.write("-" * 80 + "\n")
                f.write(f"Bias (Model - Observed):        {stats['bias']:12.4f}\n")
                f.write(f"Root Mean Square Error:         {stats['rmse']:12.4f}\n")
                f.write(f"Mean Absolute Error:            {stats['mae']:12.4f}\n")
                f.write(f"Correlation:                    {stats['correlation']:12.4f}\n\n")
                
                if stats.get('monthly_bias') and len(stats['monthly_bias']) > 0:
                    f.write("MONTHLY STATISTICS\n")
                    f.write("-" * 80 + "\n")
                    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                    f.write(f"{'Month':<10} {'Bias':>15} {'RMSE':>15}\n")
                    f.write("-" * 40 + "\n")
                    for i, month in enumerate(month_names, 1):
                        bias = stats['monthly_bias'].get(i, np.nan)
                        rmse = stats['monthly_rmse'].get(i, np.nan)
                        f.write(f"{month:<10} {bias:15.4f} {rmse:15.4f}\n")
                
                f.write("\n\n")

    def save_netcdf_outputs(self):
        """Save regridded data and statistics as NetCDF."""
        print("\nSaving NetCDF outputs...")
        
        for config in self.obs_datasets:
            var_key = config['variable']
            var_output_dir = self.output_dir / var_key
            var_output_dir.mkdir(exist_ok=True)
            
            # Save regridded dataset
            regridded_file = var_output_dir / f"{var_key}_regridded.nc"
            config['regridded_ds'].to_netcdf(regridded_file)
            print(f"  Saved: {regridded_file}")
            
            # Determine which grid we're on for spatial statistics
            if self.regrid_direction == 'obs_to_model':
                lat_coord = config['model_lat']
                lon_coord = config['model_lon']
                lat_data = config['model_ds'][lat_coord].values
                lon_data = config['model_ds'][lon_coord].values
            else:
                lat_coord = config['obs_lat']
                lon_coord = config['obs_lon']
                lat_data = config['obs_ds'][lat_coord].values
                lon_data = config['obs_ds'][lon_coord].values
            
            # Save spatial statistics
            if lat_data.ndim == 1:
                coords = {
                    lat_coord: lat_data,
                    lon_coord: lon_data
                }
            else:
                coords = {
                    lat_coord: ([lat_coord, lon_coord], lat_data),
                    lon_coord: ([lat_coord, lon_coord], lon_data)
                }
            
            stats_ds = xr.Dataset(
                {
                    'bias': ([lat_coord, lon_coord], config['spatial_bias']),
                    'rmse': ([lat_coord, lon_coord], config['spatial_rmse']),
                    'correlation': ([lat_coord, lon_coord], config['spatial_corr']),
                },
                coords=coords
            )
            
            stats_file = var_output_dir / f"{var_key}_spatial_statistics.nc"
            stats_ds.to_netcdf(stats_file)
            print(f"  Saved: {stats_file}")


def main():
    """Main execution function."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Statistical analysis with xESMF regridding and bidirectional support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Regridding Methods:
  nearest           : Nearest neighbor (scipy-based, fast)
  bilinear          : Bilinear interpolation (scipy-based)
  conservative      : Conservative regridding (xESMF, preserves integrals)
  conservative_normed : Normalized conservative (xESMF, recommended for fluxes)
  patch             : Patch recovery (xESMF, higher order accuracy)

Regridding Direction:
  obs_to_model      : Regrid observations to model grid (default)
  model_to_obs      : Regrid model to observation grid

Examples:
  # Conservative regridding, obs to model grid
  python multi_variable_analysis.py \\
      --obs-folder ./obs --obs-pattern "sst_*.nc" \\
      --model-folder ./model --model-pattern "model_*.nc" \\
      --variable sst \\
      --regrid-method conservative \\
      --regrid-direction obs_to_model
  
  # Bilinear regridding, model to obs grid
  python multi_variable_analysis.py \\
      --obs-folder ./obs --obs-pattern "*.nc" \\
      --model-folder ./model --model-pattern "*.nc" \\
      --variable sst \\
      --regrid-method bilinear \\
      --regrid-direction model_to_obs
        """
    )
    
    parser.add_argument(
        '--obs-folder', nargs='+', required=True,
        help='Folders containing observed data files (one per variable)'
    )
    parser.add_argument(
        '--obs-pattern', nargs='+', required=True,
        help='File patterns for observed files (wildcards or space-separated list)'
    )
    parser.add_argument(
        '--model-folder', nargs='+', required=True,
        help='Folders containing model data files (one per variable)'
    )
    parser.add_argument(
        '--model-pattern', nargs='+', required=True,
        help='File patterns for model files (wildcards or space-separated list)'
    )
    parser.add_argument(
        '--variable', nargs='+', required=True,
        help='Variable identifiers (e.g., sst, salinity, ssh)'
    )
    parser.add_argument(
        '--obs-var', nargs='+',
        help='Variable names in observed files (auto-detected if not provided)'
    )
    parser.add_argument(
        '--model-var', nargs='+',
        help='Variable names in model files (auto-detected if not provided)'
    )
    parser.add_argument(
        '--variable-label', nargs='+',
        help='Display labels for variables (optional)'
    )
    parser.add_argument(
        '--output-dir', default='./analysis_output',
        help='Output directory for results'
    )
    parser.add_argument(
        '--regrid-method', 
        default='nearest', 
        choices=['nearest', 'bilinear', 'conservative', 'conservative_normed', 'patch'],
        help='Regridding method (default: nearest)'
    )
    parser.add_argument(
        '--regrid-direction',
        default='obs_to_model',
        choices=['obs_to_model', 'model_to_obs'],
        help='Regridding direction (default: obs_to_model)'
    )
    parser.add_argument(
        '--inspect', action='store_true',
        help='Inspect files and print available variables/coordinates'
    )
    
    args = parser.parse_args()
    
    # Validate inputs
    n_vars = len(args.variable)
    if len(args.obs_folder) != n_vars:
        parser.error(f"Number of --obs-folder ({len(args.obs_folder)}) must match number of variables ({n_vars})")
    if len(args.obs_pattern) != n_vars:
        parser.error(f"Number of --obs-pattern ({len(args.obs_pattern)}) must match number of variables ({n_vars})")
    if len(args.model_folder) != n_vars:
        parser.error(f"Number of --model-folder ({len(args.model_folder)}) must match number of variables ({n_vars})")
    if len(args.model_pattern) != n_vars:
        parser.error(f"Number of --model-pattern ({len(args.model_pattern)}) must match number of variables ({n_vars})")
    
    if args.obs_var and len(args.obs_var) != n_vars:
        parser.error(f"Number of --obs-var ({len(args.obs_var)}) must match number of variables ({n_vars})")
    if args.model_var and len(args.model_var) != n_vars:
        parser.error(f"Number of --model-var ({len(args.model_var)}) must match number of variables ({n_vars})")
    if args.variable_label and len(args.variable_label) != n_vars:
        parser.error(f"Number of --variable-label ({len(args.variable_label)}) must match number of variables ({n_vars})")
    
    # Create analyzer
    analyzer = MultiVariableAnalyzer(output_dir=args.output_dir)
    
    # Expand patterns and check files for each variable
    print("\n" + "=" * 80)
    print("FILE DISCOVERY")
    print("=" * 80)
    
    for i in range(n_vars):
        print(f"\nVariable {i+1}: {args.variable[i]}")
        print(f"  Obs folder: {args.obs_folder[i]}")
        print(f"  Obs pattern: {args.obs_pattern[i]}")
        print(f"  Model folder: {args.model_folder[i]}")
        print(f"  Model pattern: {args.model_pattern[i]}")
        
        # Check if folders exist
        obs_folder = Path(args.obs_folder[i])
        model_folder = Path(args.model_folder[i])
        
        if not obs_folder.exists():
            print(f"  ERROR: Observed folder does not exist: {obs_folder}")
            continue
        if not model_folder.exists():
            print(f"  ERROR: Model folder does not exist: {model_folder}")
            continue
        
        # Expand patterns
        obs_files = analyzer._expand_file_pattern(args.obs_folder[i], args.obs_pattern[i])
        model_files = analyzer._expand_file_pattern(args.model_folder[i], args.model_pattern[i])
        
        print(f"  Found {len(obs_files)} observed files")
        print(f"  Found {len(model_files)} model files")
        
        if len(obs_files) == 0 or len(model_files) == 0:
            print(f"  WARNING: No files found for variable {args.variable[i]}")
            continue
    
    # Inspection mode
    if args.inspect:
        print("\n" + "=" * 80)
        print("INSPECTION MODE")
        print("=" * 80)
        
        for i in range(n_vars):
            print(f"\n{'='*80}")
            print(f"INSPECTING VARIABLE {i+1}: {args.variable[i]}")
            print('='*80)
            
            obs_files = analyzer._expand_file_pattern(args.obs_folder[i], args.obs_pattern[i])
            model_files = analyzer._expand_file_pattern(args.model_folder[i], args.model_pattern[i])
            
            if len(obs_files) > 0:
                print(f"\nOBSERVED FILE (first of {len(obs_files)}): {obs_files[0]}")
                print("-" * 80)
                try:
                    obs_ds = xr.open_dataset(obs_files[0])
                    print(f"Dimensions: {dict(obs_ds.dims)}")
                    print(f"Coordinates: {list(obs_ds.coords.keys())}")
                    print(f"Variables: {list(obs_ds.data_vars.keys())}")
                except Exception as e:
                    print(f"Error loading file: {e}")
            
            if len(model_files) > 0:
                print(f"\nMODEL FILE (first of {len(model_files)}): {model_files[0]}")
                print("-" * 80)
                try:
                    model_ds = xr.open_dataset(model_files[0])
                    print(f"Dimensions: {dict(model_ds.dims)}")
                    print(f"Coordinates: {list(model_ds.coords.keys())}")
                    print(f"Variables: {list(model_ds.data_vars.keys())}")
                except Exception as e:
                    print(f"Error loading file: {e}")
        
        return
    
    # Add all dataset pairs
    for i in range(n_vars):
        try:
            analyzer.add_dataset_pair(
                obs_folder=args.obs_folder[i],
                model_folder=args.model_folder[i],
                obs_pattern=args.obs_pattern[i],
                model_pattern=args.model_pattern[i],
                variable=args.variable[i],
                obs_varname=args.obs_var[i] if args.obs_var else None,
                model_varname=args.model_var[i] if args.model_var else None,
                variable_label=args.variable_label[i] if args.variable_label else None,
            )
        except Exception as e:
            print(f"\nERROR adding variable {args.variable[i]}: {e}")
            continue
    
    if len(analyzer.obs_datasets) == 0:
        print("\nERROR: No valid datasets to analyze. Exiting.")
        return
    
    # Run analysis
    analyzer.load_all_data()
    analyzer.regrid_data(method=args.regrid_method, direction=args.regrid_direction)
    analyzer.compute_statistics()
    analyzer.create_visualizations()
    analyzer.save_statistics_report()
    analyzer.save_netcdf_outputs()
    
    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE!")
    print(f"All outputs saved to: {analyzer.output_dir}")
    print("=" * 80)


if __name__ == "__main__":
    main()
