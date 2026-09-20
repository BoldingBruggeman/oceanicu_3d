#!/usr/bin/env python
"""data_manifest.py -- generate and compare a manifest of the real input
data (bathymetry, boundary conditions, meteo, rivers) so a copy on
another machine (the HPC) can be checked against a known-good one
without re-deriving which folders matter by hand.

Folders come from a data_roots.yaml-style file (same convention as
oceanicu_driver.py's own --data-roots-file / driver/data_roots.yaml.example)
-- never hardcoded, same reasoning machines.yaml was removed for this
session: a per-machine path baked in here would go stale exactly the
same way.

Why not a full md5sum of every file unconditionally: checked directly
against bb-server1's real data, 2026-09-20 --

    /data/TPXO9                                     17G   (25 files)
    .../NSe/CMEMS/bdy                               3.9G   (5 files)
    .../NSe/CMIP6 (boundary, delta-corrected)        30G  (44 files)
    /data/WOA                                       606M   (3 files)
    .../NSe/CMEMS/init                               3.1G   (4 files)
    /data/ERA5/NA                                   143G (233 files)
    /data/BiasCorrected (meteo + rivers, shared)    4.9T (15389 files)
    /data/CMIP6 (raw meteo)                          54G  (55 files)
    /data/EMORID (rivers)                           1.4G (982 files)

Every one of those is small enough that a full-content md5sum finishes
in minutes -- except /data/BiasCorrected, where hashing 4.9T would take
hours each run. Its own files aren't individually large either (median
48MB, max 0.71GB, checked directly) -- the cost is pure volume, so a
per-file size cutoff wouldn't trigger on any single file there. A
per-FOLDER rollup hash doesn't actually help either: computing one
still means reading every file's content once, so it saves output size,
not I/O time -- the real lever is skipping the content read entirely
for a folder whose TOTAL size crosses --max-hash-gb (default 200,
comfortably above ERA5's 143G, well below BiasCorrected's 4.9T): its
files get a cheap (size, mtime) record instead of md5, no content read
at all. Everything else gets a real md5sum. --compare re-checks each
entry the SAME way it was recorded (hash vs. stat), so comparing against
a manifest generated on one machine behaves consistently regardless of
how large the LOCAL copy of a given folder happens to be.

Usage:
    # on the machine holding the reference data (e.g. bb-server1):
    python data_manifest.py generate --data-roots-file bb-server1_data_roots.yaml \\
        -o data_manifest.txt

    # on the machine to check (e.g. the HPC), after copying data_manifest.txt over:
    python data_manifest.py compare --data-roots-file scylla_data_roots.yaml \\
        data_manifest.txt
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

# --- category -> data_roots.yaml variable(s), in the order they're listed
# to a reader (see module docstring). One physical folder can legitimately
# be named by more than one variable (CMIP6_FOLDER/RIVER_FOLDER_CMIP6 both
# point at /data/BiasCorrected) -- resolved by real path, not name, so it's
# only ever walked once regardless of how many variables name it.
_CATEGORY_VARS = {
    "bathymetry": ["BATHYMETRY_FOLDER"],
    "boundary_conditions": [
        "TPXO_FOLDER",
        "HYDROGRAPHY_FOLDER_WOA", "HYDROGRAPHY_FOLDER_CMEMS",
        "BOUNDARY_FOLDER_BAROTROPIC_CMEMS", "BOUNDARY_FOLDER_BAROTROPIC_CMIP6",
        "BOUNDARY_FOLDER_BAROCLINIC_WOA", "BOUNDARY_FOLDER_BAROCLINIC_CMEMS",
        "BOUNDARY_FOLDER_BAROCLINIC_CMIP6",
    ],
    "meteo": ["ERA5_FOLDER", "CMIP6_FOLDER", "CMIP6_RAW_FOLDER"],
    "rivers": ["RIVER_FOLDER", "RIVER_FOLDER_CMIP6"],
}

_MANIFEST_COLUMNS = ("category", "relpath", "size", "mtime", "method", "value")

# Vars naming a PURE DOWNLOAD from an external source, where a different
# machine legitimately having a different regional extract/subset (not a
# real data problem) is expected, not an error -- ERA5 specifically, per
# user, 2026-09-20: "HPC might use a different ERA5 folder for a slightly
# different area -- but since these are pure download it should not
# matter and not regarded as an error." A mismatch here is still printed
# (so it's visible, not silently swallowed) but never counted toward
# `problems`/the exit code. Extendable via --advisory on the CLI for any
# other var that turns out to have the same property (e.g. CMIP6_RAW_FOLDER
# is also a pure download, but not yet confirmed to vary by machine the
# same way -- not defaulted here until it actually comes up).
_DEFAULT_ADVISORY_VARS = {"ERA5_FOLDER"}


def _load_data_roots(path: str) -> dict:
    """Same tiny, self-contained parse as check_inputs.py's own
    _apply_data_roots -- duplicated rather than imported for the same
    reason (this script may run in an environment without pygetm_config
    installed, e.g. the bare HPC)."""
    import yaml

    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return {k: str(v) for k, v in raw.items()}


def _resolve_category_folders(data_roots: dict) -> dict[str, list[tuple[str, Path]]]:
    """{category: [(var_name, resolved_path), ...]}, only for vars that are
    actually set and exist as a real directory -- missing/placeholder
    entries (e.g. FABM's, per data_roots.yaml.example's own comment) are
    silently skipped rather than treated as an error, since this tool
    only ever reports on what's actually there."""
    out: dict[str, list[tuple[str, Path]]] = {}
    for category, var_names in _CATEGORY_VARS.items():
        found = []
        for var in var_names:
            raw = data_roots.get(var)
            if not raw:
                continue
            p = Path(os.path.expandvars(raw))
            if p.is_dir():
                found.append((var, p))
        if found:
            out[category] = found
    return out


def _dedupe_by_real_path(folders: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    """Keep the first (var_name, path) for each distinct resolved real
    path -- CMIP6_FOLDER and RIVER_FOLDER_CMIP6 both naming
    /data/BiasCorrected must only ever be walked once, not twice under
    two different category labels."""
    seen: set = set()
    deduped = []
    for var, p in folders:
        real = p.resolve()
        if real in seen:
            continue
        seen.add(real)
        deduped.append((var, p))
    return deduped


def _md5_of(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(root: Path) -> Iterator[Path]:
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            yield Path(dirpath) / name


def generate(data_roots_file: str, out_path: str, max_hash_gb: float) -> None:
    data_roots = _load_data_roots(data_roots_file)
    by_category = _resolve_category_folders(data_roots)
    max_hash_bytes = max_hash_gb * (1024 ** 3)

    rows: list[tuple] = []
    for category, folders in by_category.items():
        for var, root in _dedupe_by_real_path(folders):
            files = list(_iter_files(root))
            total_size = sum(f.stat().st_size for f in files)
            use_hash = total_size <= max_hash_bytes
            print(
                f"{category}/{var}: {root} -- {len(files)} file(s), "
                f"{total_size / (1024**3):.1f}G -- "
                f"{'md5' if use_hash else 'stat-only (over --max-hash-gb)'}",
                file=sys.stderr,
            )
            for f in sorted(files):
                st = f.stat()
                relpath = f"{var}/{f.relative_to(root)}"
                mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
                if use_hash:
                    rows.append((category, relpath, st.st_size, mtime, "md5", _md5_of(f)))
                else:
                    rows.append((category, relpath, st.st_size, mtime, "stat", ""))

    with open(out_path, "w") as f:
        f.write("\t".join(_MANIFEST_COLUMNS) + "\n")
        for row in rows:
            f.write("\t".join(str(c) for c in row) + "\n")
    print(f"wrote {out_path}: {len(rows)} entries", file=sys.stderr)


def _load_manifest(path: str) -> dict[str, tuple]:
    """{relpath: (category, size, mtime, method, value)}."""
    entries = {}
    with open(path) as f:
        header = f.readline().rstrip("\n").split("\t")
        assert tuple(header) == _MANIFEST_COLUMNS, f"unexpected manifest header: {header!r}"
        for line in f:
            category, relpath, size, mtime, method, value = line.rstrip("\n").split("\t")
            entries[relpath] = (category, int(size), mtime, method, value)
    return entries


def compare(data_roots_file: str, manifest_path: str, advisory_vars: set) -> int:
    """Re-check each manifest entry the SAME way it was recorded (hash
    vs. stat) against the local data_roots -- returns the number of
    real problems found (0 = clean), same exit-code convention as
    check_inputs.py's own report.ok. A mismatch under a var in
    *advisory_vars* is still printed, marked [ADVISORY], but never
    counted -- see _DEFAULT_ADVISORY_VARS' own comment."""
    data_roots = _load_data_roots(data_roots_file)
    by_category = _resolve_category_folders(data_roots)
    roots_by_var = {var: root for folders in by_category.values() for var, root in folders}

    manifest = _load_manifest(manifest_path)
    problems = 0
    advisories = 0
    seen_relpaths: set = set()

    def _report(var: str, tag: str, message: str) -> None:
        nonlocal problems, advisories
        if var in advisory_vars:
            print(f"[ADVISORY {tag}] {message}", file=sys.stderr)
            advisories += 1
        else:
            print(f"[{tag}] {message}", file=sys.stderr)
            problems += 1

    for relpath, (_category, exp_size, _exp_mtime, method, exp_value) in sorted(manifest.items()):
        var, _, sub = relpath.partition("/")
        root = roots_by_var.get(var)
        if root is None:
            _report(var, "MISSING FOLDER", f"{var} (needed for {relpath}) not resolved on this machine")
            continue
        local_path = root / sub
        seen_relpaths.add(relpath)
        if not local_path.is_file():
            _report(var, "MISSING", relpath)
            continue
        st = local_path.stat()
        if st.st_size != exp_size:
            _report(var, "SIZE MISMATCH", f"{relpath}: expected {exp_size}, got {st.st_size}")
            continue
        if method == "md5":
            actual = _md5_of(local_path)
            if actual != exp_value:
                _report(var, "CONTENT MISMATCH", f"{relpath}: md5 {exp_value} -> {actual}")
        # method == "stat": size already checked above; mtime is
        # informational only (a legitimate re-transfer can change it
        # without the content actually differing), never itself a failure.

    for folders in by_category.values():
        for var, root in _dedupe_by_real_path(folders):
            for f in _iter_files(root):
                relpath = f"{var}/{f.relative_to(root)}"
                if relpath not in manifest and relpath not in seen_relpaths:
                    _report(var, "EXTRA, NOT IN MANIFEST", relpath)

    summary = "ALL CHECKS PASSED" if problems == 0 else f"{problems} PROBLEM(S) FOUND"
    if advisories:
        summary += f" ({advisories} advisory notice(s), not counted)"
    print(summary, file=sys.stderr)
    return 0 if problems == 0 else 2


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="write a manifest for this machine's own data")
    g.add_argument("--data-roots-file", required=True)
    g.add_argument("-o", "--output", required=True, metavar="PATH")
    g.add_argument("--max-hash-gb", type=float, default=200.0,
                   help="folders at or under this total size get a real md5sum per file; "
                        "larger ones get a cheap (size, mtime) record instead (default: 200)")

    c = sub.add_parser("compare", help="check this machine's own data against a manifest")
    c.add_argument("--data-roots-file", required=True)
    c.add_argument("manifest", metavar="MANIFEST_PATH")
    c.add_argument("--advisory", action="append", default=[], metavar="VAR_NAME",
                    help="treat a mismatch under this data_roots.yaml variable as informational, "
                         f"not a real problem (default: {', '.join(sorted(_DEFAULT_ADVISORY_VARS))} -- "
                         "pure downloads that legitimately differ by machine/region). Repeatable; "
                         "adds to, doesn't replace, the defaults.")
    c.add_argument("--no-default-advisory", action="store_true",
                    help="don't apply the default advisory set above -- only --advisory vars (if any) count")

    args = p.parse_args()
    if args.cmd == "generate":
        generate(args.data_roots_file, args.output, args.max_hash_gb)
        return 0
    advisory_vars = set(args.advisory) | (set() if args.no_default_advisory else _DEFAULT_ADVISORY_VARS)
    return compare(args.data_roots_file, args.manifest, advisory_vars)


if __name__ == "__main__":
    sys.exit(main())
