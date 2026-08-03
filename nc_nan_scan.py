#!/usr/bin/env python3
"""
nc_nan_scan.py — Scan a NetCDF file for NaNs within the computational domain.

Designed for Arakawa C-grid output (e.g. GETM). Each variable's horizontal
dimensions identify which grid it lives on (T/U/V/X), so the matching 2-D
land/sea mask is applied automatically and NaNs are only counted at points the
mask marks as computational (wet). Land / halo points, where fill values are
expected, are ignored.

The file is opened lazily and each variable is walked in blocks along its first
dimension, so memory stays bounded regardless of file size. Summary rows print
as each variable is scanned.

Mask handling
-------------
Masks are auto-detected as 2-D integer variables (read raw, ignoring any
_FillValue so they stay integer). A point is "computational" when
mask != --land-value (default 0). GETM convention: 0 = land/halo,
1 = interior, 2 = open boundary, so the default keeps boundary points.
Override detection with --mask-vars if needed.

When more than one candidate mask shares the same dims — typical for a
C-grid, where T/U/V/X masks are all e.g. (y, x) — auto-detection just
picks the first match, which may not be the variable's own grid (a
V-point variable needs the V-mask, not the T-mask, to correctly identify
its own open-boundary cells). Use --var-mask to pin the right mask per
variable explicitly, e.g. --var-mask advU=au,advV=av. The printed "Masks
in use" block also lists each detected mask's unique values, so you can
confirm your file's actual boundary code (it may not be 2).

Some variables (e.g. advection terms like advV) are legitimately never
computed at open-boundary points, so NaN there is expected, not a bug.
Use --exclude-boundary-vars to also treat --boundary-value (default 2)
as non-computational for just those variables, instead of every variable
in the file (--land-value 2 would do that, and would also suppress real
open-boundary problems in every other variable). --boundary-value accepts
a comma-separated list — some models use more than one code, and the codes
aren't necessarily consistent between grids. Confirmed on a real NSe
pygetm debug dump (getm-dump.nc, written on a NaN crash): maskt uses 2 for
open-boundary T-points (count matched the file's bdyt dimension exactly —
311 both), but masku/maskv use 3 and 4 instead of 2. These don't split by
grid (both codes appear on both masku and maskv) because they encode
boundary *orientation* (west/east- vs north/south-running boundary), not
which C-grid the mask belongs to — some terms (e.g. the Coriolis force)
are computed differently depending on which component is boundary-normal,
so u/v-momentum code needs to know a boundary point's orientation, not
just that it's a boundary. Always check the printed "values=" list per
mask; don't assume 2 holds outside the T-grid.

Usage:
    python nc_nan_scan.py getm-dump.nc
    python nc_nan_scan.py getm-dump.nc --mask-vars az,au,av,ax
    python nc_nan_scan.py getm-dump.nc --land-value 0 --max-list 100
    python nc_nan_scan.py getm-dump.nc --no-mask        # scan full arrays
    python nc_nan_scan.py getm-dump.nc --all            # list every index
    python nc_nan_scan.py getm-dump.nc --vars temp,salt # scan only these variables
                                                         # (indices always listed in full)
    python nc_nan_scan.py getm-dump.nc --exclude-boundary-vars advU,advV
                                                         # NaN at open-boundary points is
                                                         # expected for these vars only
    python nc_nan_scan.py getm-dump.nc --vars advU,advV \
                                        --exclude-boundary-vars advU,advV \
                                        --var-mask advU=au,advV=av
                                                         # pin the correct staggered-grid
                                                         # mask per variable explicitly

Requires: xarray, netCDF4, numpy   (pip install xarray netCDF4 numpy)
"""

import argparse
import sys

import numpy as np

try:
    import xarray as xr
except ImportError:
    sys.exit("xarray is required:  pip install xarray netCDF4 numpy")


SUMMARY_FMT = "{:<24}  {:<8}  {:<22}  {:<10}  {:>12}  {:>8}"
SUMMARY_HEADERS = ("variable", "dtype", "shape", "mask", "nan_count", "nan_%")


def detect_masks(ds_raw, explicit):
    """Return {name: active_DataArray(dims-only, bool)} keyed by variable name.

    A mask is a 2-D integer variable. `active` is True where the point is
    computational. Coordinates are stripped so broadcasting is purely by dim
    name (no coordinate alignment surprises across the two file handles).
    """
    if explicit:
        names = [n.strip() for n in explicit.split(",") if n.strip()]
    else:
        names = [
            n for n, v in ds_raw.variables.items()
            if v.ndim == 2 and np.issubdtype(v.dtype, np.integer)
        ]
    masks = {}
    for n in names:
        if n not in ds_raw.variables:
            print(f"  warning: mask variable '{n}' not found, skipping")
            continue
        v = ds_raw[n]
        masks[n] = xr.DataArray(np.asarray(v.values), dims=v.dims)
    return masks


def derive_face_masks(t_mask, land_value, boundary_values):
    """Approximate U-face ('au') and V-face ('av') masks from a T-mask.

    Some GETM/pygetm output only stores the T-mask (commonly 'az'); the
    U/V-face masks needed to correctly identify a staggered-grid variable's
    own open-boundary cells (e.g. advU/advV) are not saved. This derives
    them with the same shape/dims as t_mask, using the standard C-grid rule:
    a face is land if EITHER neighbouring T-cell is land, open-boundary if
    NEITHER is land but at least one is open-boundary, else interior. This
    is an approximation of GETM's own internal mask propagation, not a
    byte-for-byte reproduction — verify results if they matter for
    correctness-critical use, not just filtering expected NaNs.

    boundary_values : set[int] — a model may use more than one code for
    different boundary segments/types; the derived face is marked with the
    smallest matching code found among the two neighbours.

    Returns {"au": DataArray, "av": DataArray}, dims matching t_mask.
    """
    arr = np.asarray(t_mask.values)
    dims = tuple(t_mask.dims)
    if arr.ndim != 2:
        raise ValueError(f"derive_face_masks: T-mask must be 2-D, got dims {dims}")
    ydim, xdim = dims  # (y, x) order, matching how masks are stored elsewhere in this script
    bdy_vals = sorted(boundary_values)

    def _face(a, b):
        # a, b: neighbouring T-cell values, same shape
        land = (a == land_value) | (b == land_value)
        out = np.ones_like(a)          # interior by default
        for bv in reversed(bdy_vals):  # smallest code wins if multiple match
            out[(a == bv) | (b == bv)] = bv
        out[land] = land_value         # land overrides boundary
        return out

    au = np.full_like(arr, land_value)
    au[:, :-1] = _face(arr[:, :-1], arr[:, 1:])   # U-face between column i and i+1

    av = np.full_like(arr, land_value)
    av[:-1, :] = _face(arr[:-1, :], arr[1:, :])   # V-face between row j and j+1

    return {
        "au": xr.DataArray(au, dims=(ydim, xdim)),
        "av": xr.DataArray(av, dims=(ydim, xdim)),
    }


def match_mask(da, masks, var_name=None, var_mask_override=None):
    """Find the mask for da. Returns (name, raw_mask) where raw_mask is the
    integer mask DataArray (unconverted, so callers can test against multiple
    values — land, boundary, ...), or (None, None).

    If var_name has an explicit entry in var_mask_override, that mask is used
    (dims must still be a subset of da's dims — raises if not, since a
    mismatched-grid mask silently misclassifies every point). Otherwise falls
    back to the first mask whose dims are a subset of da's dims — this is
    ambiguous whenever more than one candidate mask shares the same dims
    (e.g. T/U/V/X masks are all typically (y, x)): use var_mask_override for
    variables living on a staggered grid (U/V/X-points), where the correct
    mask matters for getting boundary cells right, not just land cells.
    """
    if var_mask_override and var_name in var_mask_override:
        mname = var_mask_override[var_name]
        if mname not in masks:
            raise KeyError(f"--var-mask: mask '{mname}' for variable '{var_name}' "
                            f"not found among detected masks: {sorted(masks)}")
        m = masks[mname]
        if not (set(m.dims) <= set(da.dims)):
            raise ValueError(f"--var-mask: mask '{mname}' dims {tuple(m.dims)} are not "
                              f"a subset of '{var_name}' dims {tuple(da.dims)}")
        return mname, m
    for name, m in masks.items():
        if set(m.dims) <= set(da.dims):
            return name, m
    return None, None


def rows_per_block(shape, target_elems):
    if len(shape) <= 1:
        return max(1, int(target_elems))
    per_row = int(np.prod(shape[1:]))
    return max(1, int(target_elems // max(1, per_row)))


def block_along_axis0(da, block_rows):
    """Yield (start, DataArray block) reading only block_rows slices at a time."""
    if da.ndim == 0:
        yield 0, da
        return
    n0 = da.shape[0]
    dim0 = da.dims[0]
    for start in range(0, n0, block_rows):
        stop = min(start + block_rows, n0)
        yield start, da.isel({dim0: slice(start, stop)})


def scan_variable(name, da, masks, target, limit, land_value, use_mask,
                   boundary_values=None, exclude_boundary_vars=None, var_mask_override=None):
    """Stream over one variable. Returns (nan_count, indices, mask_name).

    boundary_values : set[int] | None
        Mask value(s) marking open-boundary points. Some models use more than
        one code for different boundary segments/types (e.g. pygetm may use
        3 and 4 rather than a single value 2) — any value in the set counts.
    """
    if not np.issubdtype(da.dtype, np.floating):
        return 0, [], None

    active = None
    mask_name = None
    if use_mask:
        mask_name, raw_mask = match_mask(da, masks, var_name=name, var_mask_override=var_mask_override)
        if raw_mask is not None:
            active = raw_mask != land_value
            if boundary_values and exclude_boundary_vars and name in exclude_boundary_vars:
                active = active & ~raw_mask.isin(list(boundary_values))

    block_rows = rows_per_block(da.shape, target)
    count = 0
    indices = []
    for start, block in block_along_axis0(da, block_rows):
        isn = block.isnull()
        if active is not None:
            # broadcast 2-D mask across time/z by dim name, restrict to domain
            isn = (isn & active).transpose(*block.dims)
        mask_np = np.asarray(isn.values)
        c = int(mask_np.sum())
        if c == 0:
            continue
        count += c
        if limit is None or len(indices) < limit:
            idx = np.argwhere(mask_np)
            if mask_np.ndim > 0:
                idx[:, 0] += start
            for row in idx:
                indices.append(tuple(int(i) for i in row))
                if limit is not None and len(indices) >= limit:
                    break
    return count, indices, mask_name


def main():
    p = argparse.ArgumentParser(
        description="Scan a C-grid NetCDF file for NaNs inside the computational domain.")
    p.add_argument("ncfile", help="path to the NetCDF file")
    p.add_argument("--vars", default=None,
                   help="comma-separated variable names to scan (default: scan every "
                        "variable in the file). When given, all matching NaN indices "
                        "are listed regardless of --max-list.")
    p.add_argument("--mask-vars", default=None,
                   help="comma-separated mask variable names (default: auto-detect 2-D int vars)")
    p.add_argument("--var-mask", default=None,
                   help="comma-separated var=mask pairs pinning which mask each variable "
                        "uses, e.g. advU=au,advV=av. Needed whenever more than one "
                        "candidate mask shares the same dims (T/U/V/X masks are usually "
                        "all (y, x)) — auto-detection then picks ambiguously, which can "
                        "silently apply the wrong grid's boundary/land cells to a "
                        "staggered-grid variable.")
    p.add_argument("--land-value", type=int, default=0,
                   help="mask value marking non-computational points (default: 0)")
    p.add_argument("--boundary-value", default="2",
                   help="comma-separated mask value(s) marking open-boundary points "
                        "(default: 2, GETM textbook convention — but many pygetm/GETM "
                        "files use different and/or multiple codes, e.g. 3,4 for "
                        "different boundary segment types; check the 'values=' list "
                        "printed per mask and adjust accordingly)")
    p.add_argument("--exclude-boundary-vars", default=None,
                   help="comma-separated variable names for which open-boundary points "
                        "(--boundary-value) are also treated as non-computational, e.g. "
                        "advection terms that are never computed at the boundary")
    p.add_argument("--derive-uv-masks", action="store_true",
                   help="when only a T-mask is available (common — GETM often saves just "
                        "'az'), derive approximate U-face/V-face masks ('au'/'av') from it "
                        "and add them to the detected masks, so --var-mask advU=au,advV=av "
                        "works even without au/av in the file. See derive_face_masks().")
    p.add_argument("--t-mask-name", default="az",
                   help="name of the T-mask to derive au/av from (default: 'az')")
    p.add_argument("--no-mask", action="store_true",
                   help="ignore masks and scan full arrays")
    p.add_argument("--max-list", type=int, default=50,
                   help="max NaN indices to list per variable (default: 50)")
    p.add_argument("--all", action="store_true",
                   help="list every NaN index (overrides --max-list)")
    p.add_argument("--block-elems", type=float, default=1e7,
                   help="approx elements read per block (default: 1e7)")
    args = p.parse_args()

    # --vars restricts which variables are scanned. In that mode indices are
    # always listed in full (the point of naming variables explicitly is to
    # look at them closely), regardless of --max-list / --all.
    only_vars = None
    if args.vars:
        only_vars = [v.strip() for v in args.vars.split(",") if v.strip()]
    exclude_boundary_vars = None
    if args.exclude_boundary_vars:
        exclude_boundary_vars = {v.strip() for v in args.exclude_boundary_vars.split(",") if v.strip()}
    boundary_values = {int(v.strip()) for v in args.boundary_value.split(",") if v.strip()}
    var_mask_override = None
    if args.var_mask:
        var_mask_override = {}
        for pair in args.var_mask.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" not in pair:
                sys.exit(f"--var-mask: expected var=mask, got '{pair}'")
            vname, mname = pair.split("=", 1)
            var_mask_override[vname.strip()] = mname.strip()
    limit = None if (args.all or only_vars) else args.max_list
    target = int(args.block_elems)
    use_mask = not args.no_mask

    try:
        ds = xr.open_dataset(args.ncfile)                      # decoded: fill -> NaN
        ds_raw = xr.open_dataset(args.ncfile, mask_and_scale=False)  # raw ints for masks
    except (OSError, ValueError) as e:
        sys.exit(f"Could not open '{args.ncfile}': {e}")

    if only_vars:
        missing = [v for v in only_vars if v not in ds.variables]
        if missing:
            sys.exit(f"--vars: variable(s) not found in '{args.ncfile}': {', '.join(missing)}")

    print(f"\nNaN scan: {args.ncfile}")

    masks = {}
    derived_names: set = set()
    if use_mask:
        masks = detect_masks(ds_raw, args.mask_vars)
        if args.derive_uv_masks:
            if args.t_mask_name not in masks:
                sys.exit(f"--derive-uv-masks: T-mask '{args.t_mask_name}' not found among "
                          f"detected masks: {sorted(masks)} (use --t-mask-name to point at "
                          f"the right one, or --mask-vars if it wasn't auto-detected)")
            for fname, fmask in derive_face_masks(
                masks[args.t_mask_name], args.land_value, boundary_values
            ).items():
                if fname not in masks:
                    masks[fname] = fmask
                    derived_names.add(fname)
        if masks:
            print("\nMasks in use (computational where value != %d):" % args.land_value)
            for n, m in masks.items():
                uniq = sorted(int(v) for v in np.unique(m.values))
                tag = "  (derived from %s)" % args.t_mask_name if n in derived_names else ""
                print(f"  {n:<12} dims={tuple(m.dims)}  values={uniq}{tag}")
            if var_mask_override:
                print(f"  Explicit var=mask overrides: "
                      f"{', '.join(f'{k}={v}' for k, v in var_mask_override.items())}")
            if exclude_boundary_vars:
                print(f"  Also excluding boundary points (value(s) {sorted(boundary_values)}) for: "
                      f"{', '.join(sorted(exclude_boundary_vars))}")
                all_mask_values = {v for m in masks.values() for v in np.unique(m.values)}
                if not (boundary_values & all_mask_values):
                    print(f"  ⚠ warning: boundary-value(s) {sorted(boundary_values)} do not appear in "
                          f"ANY detected mask's values above — --exclude-boundary-vars will have no "
                          f"effect. Check the 'values=' list per mask and pass --boundary-value "
                          f"if your file uses different code(s).")
        else:
            print("\nNo masks detected — scanning full arrays. "
                  "Use --mask-vars to specify them.")
    print()

    print(SUMMARY_FMT.format(*SUMMARY_HEADERS))
    print("-" * 96)

    all_vars = list(ds.data_vars.items()) + list(ds.coords.items())
    scan_list = (
        [(n, da) for n, da in all_vars if n in only_vars] if only_vars else all_vars
    )

    details = {}
    total_nans = 0
    for name, da in scan_list:
        count, idx, mname = scan_variable(
            name, da, masks, target, limit, args.land_value, use_mask,
            boundary_values=boundary_values,
            exclude_boundary_vars=exclude_boundary_vars,
            var_mask_override=var_mask_override)
        total_nans += count
        total = int(np.prod(da.shape)) if da.shape else 1
        pct = (100.0 * count / total) if total else 0.0
        print(SUMMARY_FMT.format(
            name[:24], str(da.dtype), str(tuple(da.shape))[:22],
            (mname or "—")[:10], count, f"{pct:.2f}%"), flush=True)
        if count:
            details[name] = (da.dims, idx)

    ds.close()
    ds_raw.close()

    print(f"\nTotal NaNs in computational domain: {total_nans}\n")
    if total_nans == 0:
        print("No NaNs found inside the masked domain.")
        return

    print("NaN indices (dimension order shown per variable):\n")
    for name, (dims, idx) in details.items():
        dim_str = "(" + ", ".join(dims) + ")" if dims else "(scalar)"
        print(f"{name}  dims={dim_str}")
        for i, t in enumerate(idx, 1):
            print(f"  {i:>6}  {t}")
        print()

    if limit is not None:
        print(f"(Showing up to {args.max_list} indices per variable; "
              f"use --all or --vars to list every index.)")


if __name__ == "__main__":
    main()
