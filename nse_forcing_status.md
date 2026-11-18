# NSe external forcing: configuration and file status (2026-11-15)

What `NSe/config/nse_cmems.yaml` and `NSe/config/nse_cmip6.yaml` read as
external forcing, and whether those files exist. Checked on **bb-server1**
(the source scylla's sync scripts pull from) with its
`experiments/NSe/bb-server1_data_roots.yaml`, by
`/data/BiasCorrected/diag_v2/forcing_check.py <data_roots.yaml>`. scylla
itself was not checked; `running/check_inputs.py` on a generated script does
that there, including time coverage.

File names follow the hooks and providers: `driver/oceanicu_providers.py`
(ERA5, boundaries, DIC/TA bridge), `driver/scripts/meteo.py`, `rivers.py`,
`hydrography.py`, `fabm.py`.

## nse_cmems.yaml (hindcast; boundary files 2010-01-01 .. 2026-08-17)

| Forcing | Configured as | File(s) | bb-server1 |
|---|---|---|---|
| Bathymetry | BathymetryFile | `NSe/Bathymetry/bathymetry_nse.nc` | ok |
| Initial T/S | hydrography CMEMS | `CMEMS/init/{so,thetao}_2025_monthly_ic.nc` | ok |
| Sea level, currents | barotropic CMEMS (`zos_datum_corrected`) | `CMEMS/bdy/hourly_2010-01-01_to_2026-08-17.nc` | ok |
| T/S boundaries | baroclinic CMEMS | `CMEMS/bdy/daily_2010-01-01_to_2026-08-17.nc` | ok |
| Bio boundaries no3/po4/si/o2 | fabm CMEMS, weak sponge | `bio_daily_2010-01-01_to_2026-08-17.nc` | ok |
| Bio boundaries DIC/TA | `bio_carbon_new` from 2024-07-29, `pml_trend` bridge before | `bio_carbon_new_2024-07-29_to_2026-08-17.nc`, `bio_carbon_pml_trend_20100101_20240728.nc` | ok |
| Detritus/DOC boundaries | constants, strong sponge | none | - |
| Meteo | ERA5: t2m d2m u10 v10 sp tp tcc ssr ssrd str strd | `ERA5/NA/era5_<var>_YYYY.nc` | 2004-2024 only (2025 only in `ERA5/kaj`) |
| Light kc1/kc2 | constants 1.85 / 0 | none | - |
| Rivers | EMORID | `EMORID/EMORID_1993_2024_conc_nemo_TALK_DIC.nc` | ok |
| FABM | `fabm_ersem.yaml`, `ADY_gle.nc`, no restart | | ok |
| CO2/N2O | `ghg_scenario: ssp245` | `{co2,n2o}_{historical,ssp245}_15deg.nc` | ok |
| N-deposition | follows ghg_scenario: `Ndep/AMM7_Ndep_BC-EMEP_{historical,ssp245}_y*` | | historical ok; **ssp245 missing** (see 3) |

## nse_cmip6.yaml (scenario runs 2010-2099; GFDL-ESM4, CNRM-ESM2-1, MPI-ESM1-2-HR x ssp126/ssp370)

| Forcing | Configured as | File(s) | bb-server1 |
|---|---|---|---|
| Bathymetry | as above | | ok |
| Initial T/S | hydrography WOA | `WOA/woa_{s,t}.nc` | ok |
| FABM initial state | restart_file | `NSe/FABM/init/restart_ersem.nc` | ok (2026-09-29) |
| Sea level, currents | barotropic CMIP6 | `{model}/historical/bdy/bdy_2d_20100101_20141231.nc` + `{model}/{ssp}/bdy/bdy_2d_20150101_21001231.nc` | ok, all 6 |
| T/S boundaries | baroclinic CMIP6 | `bdy_3d_{thetao,so}_...`, historical + scenario | ok, all 6 |
| Bio boundaries 2010-2014 | CMEMS reanalysis; DIC/TA `pml_trend` | `bio_daily_20100101_20141231.nc`, `bio_carbon_pml_trend_20100101_20141231.nc` | ok |
| Bio boundaries 2015- | delta-change, weak sponge | `bdy_3d_{no3,po4,si,o2,dissic,talk}_deltachange_20150101_21001231.nc` | ok, all 6 |
| Detritus/DOC boundaries | constants, strong sponge | none | - |
| Meteo | bias-corrected (QDM v2 + regrid fix), `radiation_source: net`: tas huss uas vas psl pr net_sw net_lw | `BiasCorrected/CMIP6/{model}/{historical,ssp}/meteo/<var>_bc_*_disagg_YYYY.nc`, 2010-14 + 2015-99 | ok (2026-11-15) except 1 and 2 |
| Rivers | CMIP6 daily projection; EMORID before 2015; 5-year nutrient climatology | `rivers/river_flows_future_{ssp}_daily.nc`, EMORID record, `EMORID_nutrient_climatology_monthly_5yr.nc` | ok (2026-11-15) |
| Light kc2 | file, climatology | `Kd490/KD490_clim.nc` | ok |
| CO2/N2O | boundary scenario | `{co2,n2o}_{historical,ssp}_15deg.nc` | ok |
| N-deposition | `Ndep/AMM7_Ndep_BC-EMEP_{historical,ssp}_y*` | 2010-14 + 2015-99 | ok, all 6 |

## Findings

1. **CNRM-ESM2-1 cannot run with this config.** `radiation_source: net`
   needs net_sw, which is not produced for CNRM ssp126/ssp370 (ESGF has
   CNRM's rsus only as monthly means). A CNRM run needs another radiation
   option.
2. **MPI-ESM1-2-HR ssp126 net_lw** was skipped in the QDM v2 regeneration
   (`cmip6.skip_variables`, carried over from `bc_correct_core7_fixed.yaml`
   without a recorded reason), so its files were the old, north-south
   mirrored product of 2026-09-18. Regenerated 2026-11-15 with
   `/data/BiasCorrected/bc_meteo_core7_v2_noskip.yaml` (85 daily + 85
   hourly files, verified). Change 2020s->2090s over the BC domain: raw
   model +0.61 W/m2, new product +0.55, old product +0.30. Re-sync MPI
   ssp126 to scylla before running it.
3. **N-deposition ssp245 for nse_cmems.yaml.** `fabm.ERSEM.ghg_scenario:
   ssp245` makes `configure_fabm` read `AMM7_Ndep_BC-EMEP_ssp245_y*` after
   2014. Only historical, ssp126 and ssp370 exist in `/data/FABM/Ndep` on
   bb-server1. On scylla the ssp245 names are soft links (to which scenario:
   TO FILL IN); bb-server1 has no such links, so a setup checked or run
   there fails after 2014.
4. **ERA5 ends 2024** in the configured `ERA5/NA`, so CMEMS hindcasts stop
   there although the boundaries reach 2026-08-17.
5. **N-deposition source.** The model reads the EMEP/PML product in
   `/data/FABM/Ndep` (`N3_flux_ff`/`N4_flux_ff`, flood-filled), not the
   ISIMIP-based bias correction in `/data/NDEP` (ocean-prep
   `config/ndep_bc.yaml`). Regenerating `/data/NDEP` with QDM v2 (done
   2026-11-15, April files in `/data/NDEP/old_qdm_20260414/`) therefore
   changes no NSe run.

## Regenerated with the regrid fix (2026-11-14/15)

Bias-corrected meteo (all models/scenarios), P-E composite (MPI, CNRM) and
river projections (all six, monthly and daily). BC-forced runs made before
this (CMIP6/GFDL-ESM4/ssp126 p01E_avc, t01EM0xF, nse-ssp126-ssp126) used
north-south mirrored meteo and rivers; re-sync with
`running/bin/sync_biascorrected_from_bbserver1.sh <MODEL> <SCENARIO>` before
any rerun. Ocean boundaries are not affected.
