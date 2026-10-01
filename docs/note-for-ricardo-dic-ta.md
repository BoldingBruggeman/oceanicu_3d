# Historical DIC/TA boundary (2010-2014) — now using your AMM7 ERSEM trend method

Hi Ricardo,

For the NSe FABM boundary's pre-2015 "historical bridge" period (no real
CMEMS DIC/TA product exists before Feb 2024), we'd been using a flat
day-of-year climatology with no trend. I've replaced it with a method
based on your AMM7 configuration paper (Partridge et al., PML Dec 2025):

- **Non-Baltic segments**: your published Atlantic secular-trend
  coefficients (`DIC_trend = 0.96805*exp(-0.000519414*h)`,
  `TA_trend = 0.18086*exp(-0.000181059*h)`) applied to our own real
  2024+ CMEMS ANFC record as the baseline, salinity-normalized/
  extrapolated backward to 2010-2014 the same way your paper does it,
  then de-normalized with real historical salinity.
- **Baltic boundary (segment 7)**: your salinity regression
  (`DIC = 23.767*S + 1388`, `TA = 25.406*S + 1410.15`) applied directly
  to our real historical Baltic salinity -- no extrapolation needed
  there since salinity itself is real for the whole period.

One thing worth flagging: we first tried independently re-fitting our
own regional trend from the cached GLODAPv2.2023 cruise data, filtered
to our own NSe boundary box -- but it's far too sparse for that (~4300
good-QC rows from only 15 cruises, clustered in a handful of years),
giving physically nonsensical depth-binned trends. So we're reusing
your published Atlantic coefficients directly rather than re-deriving
our own from a much smaller regional subset -- would value your take on
whether that's reasonable, or whether there's a reason your fit
wouldn't transfer well to our specific domain.

Not yet implemented: the nitrate-anomaly seasonality layer from your
paper (Eq. 7-8) -- deferred for now, trend-only first.

Happy to share the script if useful.

See also `docs/pml-amm7-ersem-comparison.md` (the fuller write-up this
note is drawn from) and `ocean-prep/cli/derive_historical_dic_ta.py`
(the actual implementation).
