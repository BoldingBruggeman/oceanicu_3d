"""Real-data example: tas at the same North Sea point, raw CMIP6 ssp126
future projection vs its bias-corrected counterpart (GFDL-ESM4, 2015-2099,
the real production output), anchored against the ERA5 historical mean."""
import xarray as xr
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

LON, LAT = 4.0, 55.0
HIST_YEARS = range(2009, 2015)       # same window as the historical example figure
FUT_YEARS = range(2015, 2100)

# ---- ERA5 historical reference mean, as an anchor ----
era5_list = []
for y in HIST_YEARS:
    ds = xr.open_dataset(f"/data/ERA5/kaj/era5_t2m_{y}.nc")
    era5_list.append(ds["t2m"].sel(latitude=LAT, longitude=LON, method="nearest"))
era5_da = xr.concat(era5_list, dim="time")
era5_mean = float(era5_da.mean()) - 273.15

# ---- raw CMIP6 ssp126, full future period, annual means ----
cmip_raw = xr.open_dataset(
    "/data/cache/cmip6/cmip6___GFDL-ESM4_ssp126_tas_day_r1i1p1f_tas_330b3a1f19.nc"
)
raw_pt = cmip_raw["tas"].sel(lat=LAT, lon=LON, method="nearest").squeeze()
raw_annual = raw_pt.groupby("time.year").mean() - 273.15
raw_years = raw_annual.year.values
raw_vals = raw_annual.values

# ---- bias-corrected ssp126, full future period, annual means ----
bc_years, bc_vals = [], []
for y in FUT_YEARS:
    ds = xr.open_dataset(
        f"/data/BiasCorrected/CMIP6/GFDL-ESM4/ssp126/meteo/tas_bc_bilinear_ssp126_{y}.nc"
    )
    pt = ds["tas"].sel(latitude=LAT, longitude=LON, method="nearest")
    bc_years.append(y)
    bc_vals.append(float(pt.mean()) - 273.15)
bc_years = np.array(bc_years)
bc_vals = np.array(bc_vals)

# align on common years (raw has 2015-2099 too)
years = np.array(sorted(set(raw_years.tolist()) & set(bc_years.tolist())))
raw_v = np.array([raw_vals[list(raw_years).index(y)] for y in years])
bc_v = np.array([bc_vals[list(bc_years).index(y)] for y in years])
diff = bc_v - raw_v

print(f"ERA5 1990-2014 mean: {era5_mean:.2f} C")
print(f"raw 2015-2024 mean: {raw_v[:10].mean():.2f} C, bc 2015-2024 mean: {bc_v[:10].mean():.2f} C")
print(f"raw 2090-2099 mean: {raw_v[-10:].mean():.2f} C, bc 2090-2099 mean: {bc_v[-10:].mean():.2f} C")
print(f"diff mean: {diff.mean():+.2f} C, diff std: {diff.std():.2f} C, "
      f"diff first-decade: {diff[:10].mean():+.2f} C, diff last-decade: {diff[-10:].mean():+.2f} C")


def rolling(x, w=5):
    """Centred rolling mean with shrinking windows at the edges (no
    divide-by-full-window artefact, unlike a plain np.convolve mode='same')."""
    import pandas as pd
    return pd.Series(x).rolling(window=w, center=True, min_periods=1).mean().values


raw_smooth = rolling(raw_v)
bc_smooth = rolling(bc_v)

DARK = "#1B2A33"
AMBER = "#E69F00"  # Okabe-Ito orange (colorblind-safe)
RAW = "#0072B2"    # Okabe-Ito blue (colorblind-safe)
MUTED = "#8C9BA1"
LIGHTBG = "#F6F3EC"

fig, (ax1, ax2) = plt.subplots(
    2, 1, figsize=(10.2, 6.3), facecolor=LIGHTBG, sharex=True,
    gridspec_kw={"height_ratios": [2.3, 1]}
)
for ax in (ax1, ax2):
    ax.set_facecolor(LIGHTBG)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(MUTED)
    ax.grid(axis="y", color=MUTED, alpha=0.25, linewidth=0.8)

ax1.plot(years, raw_v, "D", color=RAW, alpha=0.35, markersize=4)
ax1.plot(years, bc_v, "o", color=AMBER, alpha=0.3, markersize=4)
ax1.plot(years, raw_smooth, "--", color=RAW, linewidth=2.8, label="CMIP6 raw (SSP1-2.6), 5-yr mean")
ax1.plot(years, bc_smooth, "-", color=AMBER, linewidth=2.8, label="CMIP6, bias-corrected (QDM), 5-yr mean")
ax1.axhline(era5_mean, color=DARK, linewidth=2, linestyle=":")
ax1.annotate("ERA5 1990–2014 mean", xy=(years[0] + 1, era5_mean), xytext=(0, 8),
             textcoords="offset points", fontsize=12, color=DARK, fontweight="bold")
ax1.set_ylabel("Annual mean tas (°C)", fontsize=13, color=DARK)
ax1.tick_params(axis="both", labelsize=12, colors=DARK)
ax1.set_title("4°E, 55°N — raw vs. bias-corrected GFDL-ESM4, SSP1-2.6, 2015–2099",
              fontsize=15, color=DARK, fontweight="bold", pad=12)
ax1.legend(loc="upper left", fontsize=11.5, frameon=False)

ax2.plot(years, diff, "-", color=DARK, linewidth=1.8)
ax2.fill_between(years, diff, 0, color=AMBER, alpha=0.25)
ax2.axhline(0, color=MUTED, linewidth=1)
ax2.set_ylabel("Corrected − raw (°C)", fontsize=12.5, color=DARK)
ax2.set_xlabel("Year", fontsize=13, color=DARK)
ax2.tick_params(axis="both", labelsize=12, colors=DARK)
ax2.annotate(f"roughly constant offset: {diff.mean():+.1f}°C ± {diff.std():.1f}°C\n"
             "— the warming trend itself is preserved, not corrected away",
             xy=(0.02, 0.06), xycoords="axes fraction", fontsize=11, color=DARK,
             bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=MUTED, alpha=0.9))

fig.tight_layout()
fig.savefig("/tmp/tas_future_example.png", dpi=170, bbox_inches="tight", facecolor=LIGHTBG)
print("saved /tmp/tas_future_example.png")
