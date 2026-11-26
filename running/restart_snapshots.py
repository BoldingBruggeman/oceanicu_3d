"""restart_snapshots.py -- which snapshots of a pyGETM restart file can be resumed from.

A chunk whose restart is written with an interval (save_restart_interval, e.g. every
model year) holds several snapshots. If the chunk is interrupted (scancel, HPC
maintenance, node failure), the snapshots it wrote before that are normally intact:
pyGETM syncs a NetCDF file after every record. The record being written at the moment
of the kill may not be. last_complete_snapshot() finds the newest snapshot that is
complete, so `rerun --from-snapshot` (chunk_runner.py) can continue from there instead
of redoing the whole chunk.

A record counts as complete when every time-dependent variable can be read at that
index and has as many missing (masked or NaN) values as in the first record -- an
unwritten or half-written variable has more. The first record has nothing to compare
against: it counts as complete when no time-dependent variable is missing entirely.

Needs netCDF4 (the pygetm environment chunk_runner.py runs in), imported lazily so the
stdlib-only registry tools can import this module's callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class Snapshot:
    index: int
    time: Any  # cftime.datetime (None if its time value was never written)
    complete: bool
    reason: str = ""

    @property
    def iso(self) -> str:
        return self.time.strftime("%Y-%m-%dT%H:%M:%S")

    @property
    def date(self) -> str:
        return self.time.strftime("%Y-%m-%d")

    @property
    def at_midnight(self) -> bool:
        return (self.time.hour, self.time.minute, self.time.second, self.time.microsecond) == (0, 0, 0, 0)


@dataclass
class Inspection:
    path: str
    readable: bool
    error: str = ""
    snapshots: list[Snapshot] = field(default_factory=list)

    def summary(self) -> str:
        if not self.readable:
            return f"{self.path}: unreadable ({self.error})"
        if not self.snapshots:
            return f"{self.path}: no snapshots"
        parts = [s.date + ("" if s.complete else f" incomplete: {s.reason}") for s in self.snapshots]
        return f"{Path(self.path).name}: {len(self.snapshots)} snapshot(s): " + ", ".join(parts)


def _n_missing(values) -> int:
    import numpy as np

    data = np.ma.asarray(values)
    n = int(np.ma.count_masked(data))
    if np.issubdtype(data.dtype, np.floating):
        n += int(np.isnan(data.filled(0.0)).sum())
    return n


def inspect_restart(path: str | Path) -> Inspection:
    """Every snapshot in *path*, each checked for completeness (see module docstring)."""
    import netCDF4

    path = str(path)
    if not Path(path).is_file():
        return Inspection(path, readable=False, error="file does not exist")
    try:
        nc = netCDF4.Dataset(path)
    except Exception as exc:  # HDF5 errors come as OSError/RuntimeError with no common base
        return Inspection(path, readable=False, error=f"{type(exc).__name__}: {exc}")
    try:
        if "time" not in nc.variables:
            return Inspection(path, readable=False, error="no time variable")
        timevar = nc.variables["time"]
        try:
            raw = timevar[:]
            times = netCDF4.num2date(raw, units=timevar.units,
                                     calendar=getattr(timevar, "calendar", "standard"),
                                     only_use_cftime_datetimes=True)
        except Exception as exc:
            return Inspection(path, readable=False, error=f"time not readable: {type(exc).__name__}: {exc}")
        import numpy as np

        times_masked = np.ma.getmaskarray(raw)
        timed = [v for v in nc.variables.values() if v.dimensions and v.dimensions[0] == "time" and v.name != "time"]
        result = Inspection(path, readable=True)
        reference: dict[str, int] = {}
        for i in range(len(raw)):
            if times_masked[i]:
                result.snapshots.append(Snapshot(i, None, False, "time not written"))
                continue
            reason = ""
            for v in timed:
                try:
                    n = _n_missing(v[i])
                except Exception as exc:
                    reason = f"{v.name} not readable ({type(exc).__name__})"
                    break
                size = int(np.prod(v.shape[1:])) if v.ndim > 1 else 1
                if i == 0:
                    reference[v.name] = n
                    if n == size:
                        reason = f"{v.name} not written"
                        break
                elif n != reference.get(v.name, n):
                    reason = f"{v.name} has {n - reference[v.name]:+d} missing values"
                    break
            result.snapshots.append(Snapshot(i, times[i], not reason, reason))
        return result
    finally:
        nc.close()


def last_complete_snapshot(inspection: Inspection, requested: Optional[str] = None) -> tuple[Optional[Snapshot], str]:
    """The snapshot to resume from, and why when there is none: *requested* (an ISO
    date/time) if given and complete, else the newest complete one."""
    if not inspection.readable:
        return None, inspection.summary()
    candidates = [s for s in inspection.snapshots if s.time is not None]
    if requested:
        wanted = requested if "T" in requested else requested + "T00:00:00"
        match = [s for s in candidates if s.iso == wanted]
        if not match:
            return None, f"snapshot {requested} not in {inspection.summary()}"
        s = match[0]
        if not s.complete:
            return None, f"snapshot {requested} is incomplete ({s.reason})"
        return s, ""
    complete = [s for s in candidates if s.complete]
    if not complete:
        return None, f"no complete snapshot -- {inspection.summary()}"
    return complete[-1], ""


def output_file_report(chunk_dir: str | Path, exclude: tuple[str, ...] = ()) -> list[str]:
    """One line per NetCDF output file in *chunk_dir*: readable or not, and the time
    span it holds -- so a human can see what an interrupted chunk's output kept."""
    import netCDF4

    lines = []
    for p in sorted(Path(chunk_dir).glob("*.nc")):
        if p.name in exclude:
            continue
        try:
            with netCDF4.Dataset(p) as nc:
                if "time" not in nc.variables or len(nc.variables["time"]) == 0:
                    lines.append(f"{p.name}: readable, no time records")
                    continue
                t = nc.variables["time"]
                ends = netCDF4.num2date(t[[0, -1]], units=t.units, calendar=getattr(t, "calendar", "standard"),
                                        only_use_cftime_datetimes=True)
                lines.append(f"{p.name}: readable, {len(t)} records, {ends[0]} - {ends[1]}")
        except Exception as exc:
            lines.append(f"{p.name}: unreadable ({type(exc).__name__}: {exc})")
    return lines
