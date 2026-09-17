#!/usr/bin/env python3
"""Evaluate per-chunk / per-simulated-year wall-clock pace from GETM logs.

Reads getm-0000.log under each chunk directory of a run, e.g.:

    <run_dir>/000_20100101_20130101/getm-0000.log
    <run_dir>/001_20130101_20160101/getm-0000.log
    ...

matching the pattern <run_dir>/00?_*/getm-0000.log (chunk directories are
named "NNN_STARTDATE_ENDDATE", optionally with a ".attempt-TIMESTAMP"
suffix for a retried chunk -- see run_chunk.slurm).

Designed to be re-run repeatedly as more log data arrives (these logs are
rsynced down from the HPC, not written locally) -- every chunk is
classified as one of:

    complete    "Time spent in main loop" line present -- the chunk
                actually finished.
    in_progress at least one simulated-date checkpoint line found, but no
                "Time spent in main loop" yet -- still running, or the
                rsync just hasn't caught up to the finish line yet.
    no_data     directory exists but getm-0000.log is missing, empty, or
                has no simulated-date checkpoint lines at all.

A chunk classified `no_data` is NOT necessarily a failed run -- it may
simply not have started yet, or the log hasn't been rsynced down. Re-run
this script after the next sync to get an updated picture.

Usage
-----
    python running/analyze_chunk_pace.py <run_dir> [--csv OUT.csv]

    # e.g. against a local rsync mirror of
    # bb-server1:/data/OceanICU/oceanicu_3d/experiments/NSe/CMIP6_raw/run01
    python running/analyze_chunk_pace.py /path/to/local/mirror/run01
"""

from __future__ import annotations

import argparse
import csv as csv_module
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# "2026-09-14 17:25:21,630 - 2016-01-01 00:00:00"  (wall ts) - (sim date/time)
# The " - " right-hand side must be ONLY a date+time -- distinguishes a
# simulated-time progress report from any other log message.
_CHECKPOINT_RE = re.compile(
    r"^(?P<wall>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - "
    r"(?P<simdate>\d{4}-\d{2}-\d{2}) (?P<simtime>\d{2}:\d{2}:\d{2})$"
)
_MAIN_LOOP_RE = re.compile(
    r"^(?P<wall>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - "
    r"Time spent in main loop: (?P<seconds>[\d.]+) s$"
)
_START_RE = re.compile(
    r"^(?P<wall>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - "
    r"Starting simulation at (?P<simdate>\d{4}-\d{2}-\d{2}) (?P<simtime>\d{2}:\d{2}:\d{2})$"
)
# Chunk directory name: NNN_STARTDATE_ENDDATE[.attempt-TIMESTAMP]
_CHUNK_DIR_RE = re.compile(r"^(?P<num>\d{3})_(?P<start>\d{8})_(?P<end>\d{8})(?:\.attempt-(?P<attempt>\d{8}T\d{6}))?$")

_WALL_FMT = "%Y-%m-%d %H:%M:%S"
_SIM_FMT = "%Y-%m-%d %H:%M:%S"


@dataclass
class ChunkResult:
    num: str
    dirname: str
    path: Path
    status: str = "no_data"  # complete | in_progress | no_data
    is_attempt_fallback: bool = False
    start_wall: Optional[datetime] = None
    sim_start: Optional[datetime] = None
    sim_last: Optional[datetime] = None
    wall_last: Optional[datetime] = None
    main_loop_seconds: Optional[float] = None
    year_seconds: dict = field(default_factory=dict)  # {year: wall seconds spent simulating that year}
    year_partial: set = field(default_factory=set)    # years whose data may be incomplete so far


def _parse_log(path: Path) -> tuple[list[tuple[datetime, datetime]], Optional[datetime], Optional[float]]:
    """Return (checkpoints, sim_start, main_loop_seconds).

    checkpoints is a list of (wall_dt, sim_dt) pairs in file order, taken
    from every simulated-date progress line found (not just year
    boundaries) -- finer granularity makes per-year splits robust even if
    a chunk's report interval never happens to land exactly on Jan 1.
    """
    checkpoints: list[tuple[datetime, datetime]] = []
    sim_start: Optional[datetime] = None
    main_loop_seconds: Optional[float] = None

    try:
        text = path.read_text(errors="replace")
    except OSError:
        return checkpoints, sim_start, main_loop_seconds

    for line in text.splitlines():
        m = _START_RE.match(line)
        if m:
            sim_start = datetime.strptime(f"{m['simdate']} {m['simtime']}", _SIM_FMT)
            continue
        m = _MAIN_LOOP_RE.match(line)
        if m:
            main_loop_seconds = float(m["seconds"])
            continue
        m = _CHECKPOINT_RE.match(line)
        if m:
            wall_dt = datetime.strptime(m["wall"], _WALL_FMT)
            sim_dt = datetime.strptime(f"{m['simdate']} {m['simtime']}", _SIM_FMT)
            checkpoints.append((wall_dt, sim_dt))

    return checkpoints, sim_start, main_loop_seconds


def _bucket_by_year(checkpoints: list[tuple[datetime, datetime]]) -> dict[int, float]:
    """Sum wall-clock seconds elapsed between consecutive checkpoints,
    attributed to the year of the EARLIER checkpoint (s0), not the later
    one (s1). A step from e.g. 2012-12-31 to 2013-01-01 simulates the
    last day of 2012, landing exactly on the 2013 boundary -- attributing
    it to s1.year would create a spurious near-zero "2013" entry (just
    that one final boundary instant) for a chunk that never actually
    simulates any of 2013. Using s0.year instead means a chunk's terminal
    boundary checkpoint (its very last line) never becomes anyone's s0,
    so it naturally contributes nothing on its own -- no artificial
    sliver, and every real simulated day still lands in the correct year.
    """
    year_seconds: dict[int, float] = {}
    for (w0, s0), (w1, _s1) in zip(checkpoints[:-1], checkpoints[1:]):
        delta = (w1 - w0).total_seconds()
        if delta < 0:
            continue  # clock oddity / log corruption -- skip rather than poison the average
        year_seconds[s0.year] = year_seconds.get(s0.year, 0.0) + delta
    return year_seconds


def analyze_run_dir(run_dir: Path) -> list[ChunkResult]:
    """Discover chunk directories under run_dir and analyze each one's log.

    For a chunk number with both a final directory and one or more
    ".attempt-*" retry directories, the final (non-attempt) directory is
    used whenever it has any data at all; an attempt directory is only
    used as a fallback when the final directory is missing/empty --
    flagged via is_attempt_fallback so the report can call that out.
    """
    candidates: dict[str, list[Path]] = {}
    is_attempt: dict[Path, bool] = {}
    for child in sorted(run_dir.iterdir()) if run_dir.is_dir() else []:
        if not child.is_dir():
            continue
        m = _CHUNK_DIR_RE.match(child.name)
        if not m:
            continue
        candidates.setdefault(m["num"], []).append(child)
        is_attempt[child] = m["attempt"] is not None

    results: list[ChunkResult] = []
    for num, dirs in sorted(candidates.items()):
        # Prefer the plain (non-attempt) directory; fall back to the most
        # recent attempt (by directory name, which sorts by timestamp).
        plain = [d for d in dirs if not is_attempt[d]]
        attempts = sorted(d for d in dirs if d not in plain)
        chosen = plain[0] if plain else (attempts[-1] if attempts else None)
        is_fallback = chosen is not None and chosen not in plain

        if chosen is None:
            continue
        result = ChunkResult(num=num, dirname=chosen.name, path=chosen, is_attempt_fallback=is_fallback)

        log_path = chosen / "getm-0000.log"
        if log_path.exists() and log_path.stat().st_size > 0:
            checkpoints, sim_start, main_loop_seconds = _parse_log(log_path)
            result.sim_start = sim_start
            result.main_loop_seconds = main_loop_seconds
            if checkpoints:
                result.start_wall = checkpoints[0][0]
                result.wall_last, result.sim_last = checkpoints[-1]
                result.year_seconds = _bucket_by_year(checkpoints)
                if main_loop_seconds is not None:
                    result.status = "complete"
                else:
                    result.status = "in_progress"
                    if result.year_seconds:
                        result.year_partial.add(max(result.year_seconds))
                # A plain fallback to an attempt dir that never got past
                # setup (no checkpoints) still counts as no_data below.
        # else: stays "no_data" (missing or empty log)

        # If the chosen (possibly fallback) dir has no data at all but a
        # sibling plain/attempt dir DOES, prefer that one instead -- e.g.
        # several failed attempts followed by a working final run.
        if result.status == "no_data":
            for alt in dirs:
                if alt is chosen:
                    continue
                alt_log = alt / "getm-0000.log"
                if alt_log.exists() and alt_log.stat().st_size > 0:
                    checkpoints, sim_start, main_loop_seconds = _parse_log(alt_log)
                    if checkpoints:
                        result = ChunkResult(
                            num=num, dirname=alt.name, path=alt,
                            is_attempt_fallback=(alt not in plain),
                        )
                        result.sim_start = sim_start
                        result.main_loop_seconds = main_loop_seconds
                        result.start_wall = checkpoints[0][0]
                        result.wall_last, result.sim_last = checkpoints[-1]
                        result.year_seconds = _bucket_by_year(checkpoints)
                        result.status = "complete" if main_loop_seconds is not None else "in_progress"
                        if result.status == "in_progress" and result.year_seconds:
                            result.year_partial.add(max(result.year_seconds))
                        break

        results.append(result)

    return results


def print_report(results: list[ChunkResult], baseline_years: Optional[int] = None) -> None:
    n_complete = sum(1 for r in results if r.status == "complete")
    n_progress = sum(1 for r in results if r.status == "in_progress")
    n_none = sum(1 for r in results if r.status == "no_data")
    print(f"{len(results)} chunk(s) found: {n_complete} complete, {n_progress} in progress, {n_none} no data yet")
    print()

    all_year_rows: list[tuple[str, int, float, bool]] = []  # (chunk, year, seconds, partial)

    for r in results:
        flag = "  [from retry attempt]" if r.is_attempt_fallback else ""
        if r.status == "no_data":
            print(f"chunk {r.num} ({r.dirname}): no data yet{flag}")
            continue

        status_label = "COMPLETE" if r.status == "complete" else "IN PROGRESS"
        total = f", main loop = {r.main_loop_seconds:.1f} s" if r.main_loop_seconds is not None else ""
        print(f"chunk {r.num} ({r.dirname}): {status_label}{flag}{total}")
        for year in sorted(r.year_seconds):
            secs = r.year_seconds[year]
            partial = year in r.year_partial
            mark = " (partial so far)" if partial else ""
            print(f"    year {year}: {secs:8.1f} s = {secs / 60:6.2f} min{mark}")
            all_year_rows.append((r.num, year, secs, partial))
        print()

    complete_years = [(c, y, s) for c, y, s, partial in all_year_rows if not partial]
    if not complete_years:
        print("No fully-complete simulated years yet -- nothing to summarize.")
        return

    def _stats(rows: list[tuple[str, int, float]]) -> tuple[float, float]:
        vals = [s for _, _, s in rows]
        m = sum(vals) / len(vals)
        var = sum((s - m) ** 2 for s in vals) / len(vals)
        return m, var**0.5

    if baseline_years is None:
        secs = [s for _, _, s in complete_years]
        mean = sum(secs) / len(secs)
        variance = sum((s - mean) ** 2 for s in secs) / len(secs)
        stdev = variance**0.5
        spread_pct = (max(secs) - min(secs)) / mean * 100 if mean else 0.0
        print("--- Pace summary across complete simulated years ---")
        print(f"  n years          : {len(secs)}")
        print(f"  min / max        : {min(secs):.1f} s ({min(secs)/60:.2f} min) / "
              f"{max(secs):.1f} s ({max(secs)/60:.2f} min)")
        print(f"  mean / stdev     : {mean:.1f} s / {stdev:.1f} s")
        print(f"  spread           : {spread_pct:.1f}% of mean")
        print()
        print("  by year:")
        for chunk, year, s in sorted(complete_years, key=lambda row: row[1]):
            dev_pct = (s - mean) / mean * 100 if mean else 0.0
            print(f"    {year} (chunk {chunk}): {s/60:6.2f} min  ({dev_pct:+5.1f}% vs mean)")
        return

    # --baseline-years given: the sample is known to fall into two
    # populations, but NOT along a fixed calendar boundary that stays
    # valid forever -- e.g. here, years >= 2015 were slow because of a
    # chunking bug in the scenario forcing files, now fixed. Once the run
    # continues past the fix, NEW years >= 2015 will be fast again, so a
    # calendar `--split-year 2015` would silently pool old-buggy and
    # new-fixed years back together and hide exactly the change being
    # watched for. The baseline has to be a FIXED anchor instead: the
    # first N chronological years (by count, not by calendar value) --
    # whatever comes in later, however many populations it turns out to
    # contain, is reported against that same fixed baseline mean, never
    # against a mean that keeps being recomputed to include new data.
    sorted_years = sorted(complete_years, key=lambda row: row[1])
    baseline = sorted_years[:baseline_years]
    rest = sorted_years[baseline_years:]
    if len(baseline) < baseline_years:
        print(f"warning: only {len(baseline)} complete year(s) available, "
              f"fewer than --baseline-years {baseline_years} -- baseline is "
              f"under-sized.", file=sys.stderr)

    print(f"--- Pace summary, fixed baseline = first {len(baseline)} years ---")
    print(f"  n years          : {len(complete_years)}  ({len(baseline)} baseline, {len(rest)} compared against it)")
    print()

    base_mean, base_stdev = _stats(baseline)
    print(f"  [baseline]  n={len(baseline)}  mean={base_mean:.1f} s ({base_mean/60:.2f} min)  stdev={base_stdev:.1f} s")
    for chunk, year, s in baseline:
        dev_pct = (s - base_mean) / base_mean * 100 if base_mean else 0.0
        print(f"    {year} (chunk {chunk}): {s/60:6.2f} min  ({dev_pct:+5.1f}% vs baseline mean)")
    print()

    if rest:
        rest_mean, rest_stdev = _stats(rest)
        print(f"  [rest]  n={len(rest)}  mean={rest_mean:.1f} s ({rest_mean/60:.2f} min)  stdev={rest_stdev:.1f} s "
              f"(shown for context only -- each year below is compared against the FIXED baseline mean above, not this one)")
        for chunk, year, s in rest:
            dev_pct = (s - base_mean) / base_mean * 100 if base_mean else 0.0
            print(f"    {year} (chunk {chunk}): {s/60:6.2f} min  ({dev_pct:+5.1f}% vs baseline mean)")
        print()
        diff_pct = (rest_mean - base_mean) / base_mean * 100 if base_mean else 0.0
        print(f"  overall          : [rest] mean is {diff_pct:+.1f}% vs. [baseline] mean "
              f"({rest_mean:.1f} s vs {base_mean:.1f} s) -- watch individual years above move "
              f"back toward 0% once data simulated with the fix in place lands")


def write_csv(results: list[ChunkResult], out_path: Path) -> None:
    with open(out_path, "w", newline="") as f:
        writer = csv_module.writer(f)
        writer.writerow(["chunk", "dirname", "status", "attempt_fallback", "year", "wall_seconds", "partial"])
        for r in results:
            if not r.year_seconds:
                writer.writerow([r.num, r.dirname, r.status, r.is_attempt_fallback, "", "", ""])
                continue
            for year in sorted(r.year_seconds):
                writer.writerow([
                    r.num, r.dirname, r.status, r.is_attempt_fallback,
                    year, f"{r.year_seconds[year]:.1f}", year in r.year_partial,
                ])
    print(f"wrote {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, help="Local directory containing 00?_* chunk subdirectories")
    parser.add_argument("--csv", type=Path, default=None, help="Also write per-chunk/per-year rows to this CSV file")
    parser.add_argument("--baseline-years", type=int, default=None,
                         help="Fix a baseline of the first N chronological complete years (by "
                              "count, not calendar value) and compare every other year against "
                              "that FIXED mean, instead of one pooled mean. Deliberately "
                              "count-based, not a calendar cutoff (e.g. --split-year 2015): once "
                              "a bug affecting years >= some calendar year is fixed, new data for "
                              "those same calendar years needs to be compared against the SAME "
                              "unchanging baseline, not silently pooled back in with old data")
    args = parser.parse_args()

    if not args.run_dir.is_dir():
        print(f"error: {args.run_dir} is not a directory", file=sys.stderr)
        return 1

    results = analyze_run_dir(args.run_dir)
    if not results:
        print(f"No chunk directories (NNN_STARTDATE_ENDDATE) found under {args.run_dir}")
        return 0

    print_report(results, baseline_years=args.baseline_years)
    if args.csv:
        write_csv(results, args.csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
