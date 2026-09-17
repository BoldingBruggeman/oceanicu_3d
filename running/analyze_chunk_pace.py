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


# Fixed baseline: the first 5 simulated years are this run's own
# historical-forced period (2010-2014, before the scenario-forcing
# splice) -- not a tunable parameter, a fact about this experiment. See
# print_report's own comment below for why this has to be a fixed
# year-COUNT, not a calendar cutoff.
_BASELINE_YEARS = 5


@dataclass
class _Trend:
    slope: float
    intercept: float
    r2: float
    t_stat: float
    sig: str
    slope_pct_per_year: float
    span_years: float
    x0: float
    x1: float


def _linear_trend(rest: list[tuple[str, int, float]], base_mean: float) -> Optional[_Trend]:
    """OLS trend of [rest]'s per-year seconds against calendar year.

    Plain stdlib least squares + a t-statistic for significance -- the
    step vs. baseline is one thing (a fixed offset from e.g. a forcing
    splice), but is the pace ALSO still drifting release over release,
    independent of that step? No numpy/scipy dependency (this script is
    deliberately stdlib-only): no incomplete-beta/t-CDF for an exact
    p-value, but for n this size the t-distribution is already close to
    normal, so |t| ~ 2 is the usual ~p<0.05 rule of thumb, ~2.7 for ~p<0.01.
    """
    if len(rest) < 3:
        return None
    xs = [float(year) for _, year, _ in rest]
    ys = [s for _, _, s in rest]
    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    cov = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    var = sum((x - x_mean) ** 2 for x in xs)
    slope = cov / var if var else 0.0
    intercept = y_mean - slope * x_mean
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    slope_pct_per_year = slope / base_mean * 100 if base_mean else 0.0
    se_slope = ((ss_res / (n - 2)) / var) ** 0.5 if var and n > 2 else float("nan")
    t_stat = slope / se_slope if se_slope else float("nan")
    sig = ("p<0.01" if abs(t_stat) > 2.7 else
           "p<0.05" if abs(t_stat) > 2.0 else
           "not significant at p<0.05")
    return _Trend(
        slope=slope, intercept=intercept, r2=r2, t_stat=t_stat, sig=sig,
        slope_pct_per_year=slope_pct_per_year, span_years=xs[-1] - xs[0],
        x0=xs[0], x1=xs[-1],
    )


@dataclass
class PaceSummary:
    baseline: list[tuple[str, int, float]]
    rest: list[tuple[str, int, float]]
    base_mean: float
    trend: Optional[_Trend]


def print_report(results: list[ChunkResult]) -> Optional[PaceSummary]:
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
        return None

    def _stats(rows: list[tuple[str, int, float]]) -> tuple[float, float]:
        vals = [s for _, _, s in rows]
        m = sum(vals) / len(vals)
        var = sum((s - m) ** 2 for s in vals) / len(vals)
        return m, var**0.5

    # The sample is known to fall into two populations, but NOT along a
    # fixed calendar boundary that stays valid forever -- e.g. here,
    # years >= 2015 were slow because of a chunking bug in the scenario
    # forcing files, now fixed. Once the run continues past the fix, NEW
    # years >= 2015 will be fast again, so a calendar cutoff would
    # silently pool old-buggy and new-fixed years back together and hide
    # exactly the change being watched for. The baseline has to be a
    # FIXED anchor instead: the first _BASELINE_YEARS chronological years
    # (by count, not by calendar value) -- whatever comes in later,
    # however many populations it turns out to contain, is reported
    # against that same fixed baseline mean, never against a mean that
    # keeps being recomputed to include new data.
    sorted_years = sorted(complete_years, key=lambda row: row[1])
    baseline = sorted_years[:_BASELINE_YEARS]
    rest = sorted_years[_BASELINE_YEARS:]
    if len(baseline) < _BASELINE_YEARS:
        print(f"warning: only {len(baseline)} complete year(s) available, "
              f"fewer than the fixed {_BASELINE_YEARS}-year baseline -- "
              f"baseline is under-sized.", file=sys.stderr)

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

        trend = _linear_trend(rest, base_mean)
        if trend is not None:
            print(f"  trend within [rest]: {trend.slope:+.2f} s/year ({trend.slope_pct_per_year:+.2f}% of "
                  f"baseline mean per year), R^2={trend.r2:.2f}, t={trend.t_stat:.2f} ({trend.sig}), "
                  f"over {trend.span_years:.0f} years ({int(trend.x0)}-{int(trend.x1)})")
            print(f"    -- i.e. pace is "
                  f"{'still getting worse' if trend.slope > 0 else 'improving' if trend.slope < 0 else 'flat'} "
                  f"release over release, on top of the step already measured above")
        return PaceSummary(baseline=baseline, rest=rest, base_mean=base_mean, trend=trend)

    return PaceSummary(baseline=baseline, rest=rest, base_mean=base_mean, trend=None)


def plot_pace(summary: PaceSummary, out_path: Path) -> None:
    """Plot per-year pace (minutes/simulated year) with the [rest] linear trend."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    base_years = [y for _, y, _ in summary.baseline]
    base_mins = [s / 60 for _, _, s in summary.baseline]
    rest_years = [y for _, y, _ in summary.rest]
    rest_mins = [s / 60 for _, _, s in summary.rest]

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.scatter(base_years, base_mins, color="#4a7ab8", label=f"baseline (first {len(summary.baseline)} yr)", zorder=3)
    ax.scatter(rest_years, rest_mins, color="#d9822b", label="post-baseline", zorder=3)
    ax.axhline(summary.base_mean / 60, color="#4a7ab8", linestyle="--", linewidth=1,
               label=f"baseline mean ({summary.base_mean / 60:.1f} min)")

    if summary.trend is not None:
        t = summary.trend
        xs_line = [t.x0, t.x1]
        ys_line = [(t.slope * x + t.intercept) / 60 for x in xs_line]
        ax.plot(xs_line, ys_line, color="#d9822b", linewidth=2,
                label=f"trend: {t.slope:+.2f} s/yr, R^2={t.r2:.2f}, {t.sig}")

    ax.set_xlabel("simulated year")
    ax.set_ylabel("wall-clock minutes per simulated year")
    ax.set_title("GETM chunk pace vs. fixed baseline")
    ax.legend(loc="best", fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"wrote {out_path}")


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
    parser.add_argument("--plot", type=Path, default=None,
                         help="Also write a PNG plot of per-year pace + the [rest] linear trend")
    args = parser.parse_args()

    if not args.run_dir.is_dir():
        print(f"error: {args.run_dir} is not a directory", file=sys.stderr)
        return 1

    results = analyze_run_dir(args.run_dir)
    if not results:
        print(f"No chunk directories (NNN_STARTDATE_ENDDATE) found under {args.run_dir}")
        return 0

    summary = print_report(results)
    if args.csv:
        write_csv(results, args.csv)
    if args.plot:
        if summary is None:
            print("error: no complete years to plot", file=sys.stderr)
            return 1
        plot_pace(summary, args.plot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
