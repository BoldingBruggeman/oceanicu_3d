import numpy as np
import matplotlib.pyplot as plt
import cartopy.crs as ccrs

import pygetm

gebco_src = "https://dap.ceda.ac.uk/thredds/dodsC/bodc/gebco/global/gebco_2025/ice_surface_elevation/netcdf/GEBCO_2025.nc"


def _mask(setup):
    if setup == "ns":
        # North west corner
        domain.mask_indices(0, 23 + 1, 116, 124 + 1)
        domain.mask_indices(0, 0 + 1, 101, 102 + 1)

        # Norwegian coast
        domain.mask_indices(62, 66 + 1, 108, 123 + 1)
        domain.mask_indices(63, 66 + 1, 107, 107 + 1)
        domain.mask_indices(64, 66 + 1, 106, 106 + 1)
        domain.mask_indices(64, 64 + 1, 105, 105 + 1)
        
        # Skagen
        domain.mask_indices(93, 93 + 1, 93, 93 + 1)
        
        # English coast
        domain.mask_indices(64, 64 + 1, 105, 105 + 1)
        domain.mask_indices(32, 32 + 1, 45, 46 + 1)
        domain.mask_indices(35, 35 + 1, 30, 31 + 1)
        
        # German coast
        # domain.mask[123, 167] = 0
        # domain.mask[109 : 113 + 1, 167 : 171 + 1] = 0
        #
        # Dutch coast
        domain.mask_indices(60, 60 + 1, 46, 46 + 1)

        # Belt sea - open the straits
        domain.mask[143:144, 179:180] = 1
        domain.H[143:144, 179:180] = 12.0
        domain.mask[143:144, 181:183] = 1
        domain.H[143:144, 181:183] = 13.0
        domain.mask[131:132, 195:196] = 1
        domain.H[131:132, 195:196] = 13.0

        # English channel
        domain.mask_indices(0, 3 + 1, 0, 0 + 1)

def plot(title, fn):
    fig = plt.figure(figsize=(10, 5))
    ax = plt.axes(projection=ccrs.PlateCarree())
    ax.coastlines(resolution="50m", linewidth=1.0)
    ax.set_title(title)
    domain.plot(fig=fig)
    fig.savefig(fn)
    fig.show()
    plt.close(fig)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "setup",
        type=str,
        help="Specify the setup name (ns, ena4, ena8)",
    )
    parser.add_argument(
        "--local_gebco",
        type=str,
        help="Path to local GEBCO file",
        default=f"{gebco_src}",
    )
    parser.add_argument(
        "--rx0",
        type=float,
        help="The rx0 smoothing parameter",
        default=0.2,
    )

    args = parser.parse_args()

    if args.setup == "ns":
        min_lon = -5.125
        max_lon = 13.375
        min_lat = 48.39167
        max_lat = 60.79167
        lon = np.linspace(min_lon, max_lon, num=112, endpoint=True)
        lat = np.linspace(min_lat, max_lat, num=125, endpoint=True)
    if args.setup == "ena4":
        min_lon = -20
        max_lon = 15.5
        min_lat = 40
        max_lat = 70
        res_degs = 15.0 / 60.0
        lon_scale = 1.0 / np.cos(np.deg2rad(max_lat - min_lat))
        lon_scale = 1.75
        lon = np.arange(min_lon, max_lon, lon_scale * res_degs)
        n = int((max_lat - min_lat) / res_degs) + 1
        lat = np.linspace(min_lat, max_lat, n, endpoint=True)
    if args.setup == "ena8":
        min_lon = -20
        max_lon = 15.5
        min_lat = 40
        max_lat = 70
        res_degs = 7.5 / 60.0
        lon_scale = 4.0 / 3.0
        lon_scale = 1.0 / np.cos(np.deg2rad(max_lat - min_lat))
        lon_scale = 1.75
        lon = np.arange(min_lon, max_lon, lon_scale * res_degs)
        n = int((max_lat - min_lat) / res_degs) + 1
        lat = np.linspace(min_lat, max_lat, n, endpoint=True)

    if args.local_gebco:
        gebco_src = args.local_gebco

    domain = pygetm.domain.create_spherical(lon, lat)

    elev = pygetm.input.from_nc(
        gebco_src,
        "elevation",
    )
    elev_src = pygetm.input.limit_region(
        elev,
        domain.lon.min(),
        domain.lon.max(),
        domain.lat.min(),
        domain.lat.max(),
        periodic_lon=True,
    )

    da = -pygetm.input.horizontal_interpolation(
        elev_src, domain.lon[1::2, 1::2], domain.lat[1::2, 1::2]
    )

    domain.H = np.ma.masked_where(da[...] < 0, da[...])
    plot("Bathymetry: raw", f"Hraw_{args.setup}.png")
    
    _mask(args.setup)

    domain.mask_shallow(2.0)
    
    domain.mask_subbasins()

    if args.rx0 > 0.:
        Hcor = domain.smooth(rx0=args.rx0)
        plot("Bathymetry: masked, subbassin removal and smoothed", f"H_{args.setup}.png")

    print(f"Maxium timestep: {domain.cfl_check()}")

    domain.to_xarray().to_netcdf(f"bathymetry_{args.setup}.nc")
    # domain = pygetm.domain.from_xarray(xr.open_dataset("bathymetry.nc"))
