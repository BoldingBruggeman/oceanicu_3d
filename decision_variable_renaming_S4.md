# Decision pending: variable renaming in ocean-data (review S4)

## Today

ocean-data renames variables to standard names (thetao -> temp, so -> salt, ...)
from `name_mappings.yaml` (variable name, then the CF `standard_name`, then
`long_name`; exact matches only). It depends on the load path:

| Renamed | Not renamed |
|---|---|
| local NetCDF via `DataLoader.load_data` | Zarr |
| `copernicus://`, `cmip6://` (CMIP6 normaliser) | model output (`load_model_data`) |
| observation loaders (Argo, WOD, GLODAP, ICES, EMODnet) | `ESGFLoader` / `CMIP6Loader` used directly |

CMIP6 atmosphere variables (tas, pr, rsds, rsus, ...) are skipped by a
hard-coded list (`_CMIP6_ATMOS_VARS`, `lib/data_loader_common.py`), because
`name_mappings.yaml` maps both `rsds` and `rsus` to `sw` and `pr` to
`precip`; without the skip, rsus would become `sw` (wrong: upwelling).

## Options

1. **Leave as is.** Works, nothing changes; the inconsistency and the
   skip list stay.
2. **One `standardize=` option on `DataLoader`** (default on), applied once
   at the end of `load_data` for every path; sub-loaders stop renaming
   themselves (observation loaders keep building with standard names).
   Remove the wrong `rsus` alias of `sw`, so the skip list can go.
   - Changes output names: Zarr loads become renamed; a local file with `pr`
     or `rsds` keeps or changes its name depending on the cleaned aliases.
   - Needs: a check of every caller that relies on the renamed names, and a
     byte-compare of the boundary workflows (CMEMS, CMIP6, delta-change,
     tidal) and the bias correction on bb-server1.
3. **Only fix the aliases** (drop `rsus` -> `sw`, decide on `pr` -> `precip`)
   and remove the skip list; leave where renaming happens. Smaller, still
   needs the CMIP6 boundary/bias-correction compare.

Questions to settle: should loaders rename at all (or should analyses ask
for names through aliases, as `extract_dataarray` already does)? If yes,
is the default on or off?
