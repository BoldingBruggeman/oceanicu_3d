"""Real-data example: tas seasonal cycle at one North Sea point,
ERA5 (ref) vs CMIP6 raw (hist) vs CMIP6 bias-corrected (GFDL-ESM4, historical,
2009-2014 -- the real production output window).

Also includes an inset showing the actual binned daily data for one
calendar month (January) at this point -- the real empirical
distributions the quantile mapping is built from, not just the smoothed
monthly-mean climatology in the main panel."""
import xarray as xr
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

LON, LAT = 4.0, 55.0   # central North Sea
YEARS = range(2009, 2015)
INSET_MONTH = 1  # January

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

# ---- the actual daily samples for the inset month, in degC ----
# These are exactly the per-calendar-month pools _correct_per_month() draws
# ref/hist from -- real daily values, not a histogram mock-up.
era5_jan = era5_da.sel(time=era5_da["time"].dt.month == INSET_MONTH).values - 273.15
raw_jan = cmip_pt.sel(time=cmip_pt["time"].dt.month == INSET_MONTH).values - 273.15
bc_jan = bc_da.sel(time=bc_da["time"].dt.month == INSET_MONTH).values - 273.15
era5_jan = era5_jan[np.isfinite(era5_jan)]
raw_jan = raw_jan[np.isfinite(raw_jan)]
bc_jan = bc_jan[np.isfinite(bc_jan)]
print(f"January daily sample counts -- era5: {era5_jan.size}, raw: {raw_jan.size}, bc: {bc_jan.size}")

# ---- figure, styled to match the slide deck palette ----
DARK = "#1B2A33"
AMBER = "#E69F00"  # Okabe-Ito orange (colorblind-safe)
RAW = "#0072B2"    # Okabe-Ito blue (colorblind-safe)
MUTED = "#8C9BA1"
LIGHTBG = "#F6F3EC"

fig = plt.figure(figsize=(10.2, 6.1), facecolor=LIGHTBG)
ax = fig.add_axes([0.09, 0.14, 0.88, 0.62])
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

fig.text(0.09, 0.965, "tas seasonal cycle at 4°E, 55°N (central North Sea)\n2009–2014 mean",
         fontsize=14.5, color=DARK, fontweight="bold", ha="left", va="top", linespacing=1.3)

ax.annotate(f"mean bias: {bias_before:+.1f}°C → {bias_after:+.1f}°C",
            xy=(0.02, 0.03), xycoords="axes fraction", fontsize=12.5, color=DARK,
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec=MUTED, alpha=0.9))

ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=3, fontsize=12.5, frameon=False)

# ---- inset: actual binned daily values for January, all 2009-2014 ----
# Placed in its own band above the main axes so it never overlaps the
# seasonal-cycle curves or the title.
axins = fig.add_axes([0.64, 0.80, 0.33, 0.155])
axins.set_facecolor("white")
bins = np.linspace(
    min(era5_jan.min(), raw_jan.min(), bc_jan.min()),
    max(era5_jan.max(), raw_jan.max(), bc_jan.max()),
    18,
)
axins.hist(raw_jan, bins=bins, color=RAW, alpha=0.55, label="raw", density=True)
axins.hist(bc_jan, bins=bins, color=AMBER, alpha=0.55, label="corrected", density=True)
axins.hist(era5_jan, bins=bins, histtype="step", color=DARK, linewidth=2.2,
           label="ERA5", density=True)
axins.set_title(f"January — actual samples\n"
                f"(n={era5_jan.size} ERA5 hourly, n={raw_jan.size} CMIP6 daily)",
                fontsize=8.8, color=DARK, pad=4, linespacing=1.3)
axins.set_xlabel("tas (°C)", fontsize=9.5, color=DARK, labelpad=2)
axins.set_yticks([])
axins.tick_params(axis="x", labelsize=8.5, colors=DARK)
for spine in ("top", "right", "left"):
    axins.spines[spine].set_visible(False)
axins.spines["bottom"].set_color(MUTED)
axins.legend(loc="upper left", fontsize=8, frameon=False, handlelength=1.2,
             bbox_to_anchor=(-0.02, 1.08))

fig.savefig("/tmp/tas_point_example.png", dpi=170, bbox_inches="tight", facecolor=LIGHTBG)
print("saved /tmp/tas_point_example.png")
