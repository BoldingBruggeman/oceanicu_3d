"""Real-data example: tas seasonal cycle at one North Sea point,
ERA5 (ref) vs CMIP6 raw (hist) vs CMIP6 bias-corrected (GFDL-ESM4, historical,
2009-2014 -- the real production output window)."""
import xarray as xr
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

LON, LAT = 4.0, 55.0   # central North Sea
YEARS = range(2009, 2015)

cmip_raw = xr.open_dataset(
    "/data/cache/cmip6/cmip6___GFDL-ESM4_historical_tas_day_r1i_tas_745e4caeb4.nc"
)
cmip_pt = cmip_raw["tas"].sel(lat=LAT, lon=LON, method="nearest").sel(
    time=slice("2009-01-01", "2014-12-31")
)
cmip_clim = cmip_pt.groupby("time.month").mean() - 273.15
cmip_lat, cmip_lon = float(cmip_pt.lat), float(cmip_pt.lon)

era5_list = []
for y in YEARS:
    ds = xr.open_dataset(f"/data/ERA5/kaj/era5_t2m_{y}.nc")
    era5_list.append(ds["t2m"].sel(latitude=LAT, longitude=LON, method="nearest"))
era5_da = xr.concat(era5_list, dim="time")
era5_lat, era5_lon = float(era5_da.latitude), float(era5_da.longitude)
era5_clim = era5_da.groupby("time.month").mean() - 273.15

bc_list = []
for y in YEARS:
    ds = xr.open_dataset(
        f"/data/BiasCorrected/CMIP6/GFDL-ESM4/historical/meteo/tas_bc_bilinear_historical_{y}.nc"
    )
    bc_list.append(ds["tas"].sel(latitude=LAT, longitude=LON, method="nearest"))
bc_da = xr.concat(bc_list, dim="time")
bc_lat, bc_lon = float(bc_da.latitude), float(bc_da.longitude)
bc_clim = bc_da.groupby("time.month").mean() - 273.15

print("nearest points -- cmip:", cmip_lat, cmip_lon, "era5:", era5_lat, era5_lon, "bc:", bc_lat, bc_lon)

months = np.arange(1, 13)
era5_v = era5_clim.sel(month=months).squeeze().values
raw_v = cmip_clim.sel(month=months).squeeze().values
bc_v = bc_clim.sel(month=months).squeeze().values

bias_before = float(np.mean(raw_v - era5_v))
bias_after = float(np.mean(bc_v - era5_v))
print(f"mean bias before: {bias_before:+.2f} C, after: {bias_after:+.2f} C")

# ---- figure, styled to match the slide deck palette ----
DARK = "#1B2A33"
AMBER = "#E69F00"  # Okabe-Ito orange (colorblind-safe)
RAW = "#0072B2"    # Okabe-Ito blue (colorblind-safe)
MUTED = "#8C9BA1"
LIGHTBG = "#F6F3EC"

fig, ax = plt.subplots(figsize=(9.5, 5.4), facecolor=LIGHTBG)
ax.set_facecolor(LIGHTBG)

month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

ax.plot(months, raw_v, "--", color=RAW, linewidth=2.5, marker="D", markersize=6,
        label="CMIP6 raw (GFDL-ESM4, historical)")
ax.plot(months, bc_v, "-", color=AMBER, linewidth=3, marker="o", markersize=6,
        label="CMIP6, bias-corrected (QDM)")
ax.plot(months, era5_v, "-", color=DARK, linewidth=3, marker="s", markersize=6,
        label="ERA5 (reference)")

ax.set_xticks(months)
ax.set_xticklabels(month_names, fontsize=13, color=DARK)
ax.set_ylabel("2-m air temperature (°C)", fontsize=14, color=DARK)
ax.tick_params(axis="y", labelsize=13, colors=DARK)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)
for spine in ("left", "bottom"):
    ax.spines[spine].set_color(MUTED)
ax.grid(axis="y", color=MUTED, alpha=0.25, linewidth=0.8)

ax.set_title(f"tas seasonal cycle at 4°E, 55°N (central North Sea) — 2009–2014 mean",
             fontsize=15, color=DARK, fontweight="bold", pad=14)

ax.annotate(f"mean bias: {bias_before:+.1f}°C → {bias_after:+.1f}°C",
            xy=(0.02, 0.03), xycoords="axes fraction", fontsize=12.5, color=DARK,
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec=MUTED, alpha=0.9))

ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, fontsize=12.5, frameon=False)
fig.tight_layout()
fig.savefig("/tmp/tas_point_example.png", dpi=170, bbox_inches="tight", facecolor=LIGHTBG)
print("saved /tmp/tas_point_example.png")
