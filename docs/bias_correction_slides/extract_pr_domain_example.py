"""Real-data example: pr (precipitation) seasonal cycle, domain-mean over the
NSe bias-correction domain (lon -20..30, lat 45..75) -- ERA5 (ref) vs CMIP6
raw (hist) vs CMIP6 bias-corrected (GFDL-ESM4, historical, 2009-2014).
Domain-mean rather than single-point, because precip is spatially noisy and
this is what `bc diagnostics` itself validates against (area-mean per
calendar month, not a single grid cell)."""
import xarray as xr
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

LON_MIN, LON_MAX, LAT_MIN, LAT_MAX = -20.0, 30.0, 45.0, 75.0
YEARS = range(2009, 2015)

cmip_raw = xr.open_dataset(
    "/data/cache/cmip6/cmip6___GFDL-ESM4_historical_pr_day_r1i1_pr_2705c009bc.nc"
)
cmip_box = cmip_raw["precip"].sel(
    lat=slice(LAT_MIN, LAT_MAX), lon=slice(LON_MIN, LON_MAX),
    time=slice("2009-01-01", "2014-12-31"),
).squeeze()
cmip_area = cmip_box.mean(dim=["lat", "lon"])
cmip_clim = cmip_area.groupby("time.month").mean() * 86400.0

era5_list = []
for y in YEARS:
    ds = xr.open_dataset(f"/data/ERA5/kaj/era5_tp_{y}.nc")
    era5_list.append(
        ds["tp"].sel(latitude=slice(LAT_MAX, LAT_MIN), longitude=slice(LON_MIN, LON_MAX))
    )
era5_da = xr.concat(era5_list, dim="time")
era5_daily = era5_da.resample(time="1D").sum() * 1000.0
era5_area = era5_daily.mean(dim=["latitude", "longitude"])
era5_clim = era5_area.groupby("time.month").mean()

bc_list = []
for y in YEARS:
    ds = xr.open_dataset(
        f"/data/BiasCorrected/CMIP6/GFDL-ESM4/historical/meteo/pr_bc_conservative_historical_{y}.nc"
    )
    bc_list.append(ds["pr"].mean(dim=["latitude", "longitude"]))
bc_da = xr.concat(bc_list, dim="time")
bc_clim = bc_da.groupby("time.month").mean() * 86400.0

months = np.arange(1, 13)
era5_v = np.clip(era5_clim.sel(month=months).squeeze().values, 0, None)
raw_v = cmip_clim.sel(month=months).squeeze().values
bc_v = np.clip(bc_clim.sel(month=months).squeeze().values, 0, None)

ratio_before = float(np.mean(raw_v) / np.mean(era5_v))
ratio_after = float(np.mean(bc_v) / np.mean(era5_v))
rmse_before = float(np.sqrt(np.mean((raw_v - era5_v) ** 2)))
rmse_after = float(np.sqrt(np.mean((bc_v - era5_v) ** 2)))
print(f"mean ratio before: {ratio_before:.2f}x ERA5, after: {ratio_after:.2f}x ERA5")
print(f"RMSE before: {rmse_before:.2f} mm/day, after: {rmse_after:.2f} mm/day")
print("era5_v", era5_v)
print("raw_v", raw_v)
print("bc_v", bc_v)

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
        label="CMIP6, bias-corrected (QDM, multiplicative)")
ax.plot(months, era5_v, "-", color=DARK, linewidth=3, marker="s", markersize=6,
        label="ERA5 (reference)")

ax.set_xticks(months)
ax.set_xticklabels(month_names, fontsize=13, color=DARK)
ax.set_ylabel("Precipitation (mm/day)", fontsize=14, color=DARK)
ax.tick_params(axis="y", labelsize=13, colors=DARK)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)
for spine in ("left", "bottom"):
    ax.spines[spine].set_color(MUTED)
ax.grid(axis="y", color=MUTED, alpha=0.25, linewidth=0.8)
ax.set_ylim(bottom=0)

ax.set_title("pr seasonal cycle, domain mean (lon -20..30, lat 45..75) — 2009–2014 mean",
              fontsize=14.5, color=DARK, fontweight="bold", pad=14)
ax.annotate(f"mean ratio to ERA5: {ratio_before:.2f}× → {ratio_after:.2f}×\n"
            f"RMSE: {rmse_before:.2f} → {rmse_after:.2f} mm/day",
            xy=(0.02, 0.86), xycoords="axes fraction", fontsize=12, color=DARK,
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec=MUTED, alpha=0.9))

ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, fontsize=12, frameon=False)
fig.tight_layout()
fig.savefig("/tmp/pr_domain_example.png", dpi=170, bbox_inches="tight", facecolor=LIGHTBG)
print("saved /tmp/pr_domain_example.png")
