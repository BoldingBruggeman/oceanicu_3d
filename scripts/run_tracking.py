#!/usr/bin/env python
"""run_tracking.py -- SQLite-backed tracking for chunked production runs.

Two tables:

  runs    One row per simulation (e.g. NSe/CMIP6/CNRM-ESM2-1/ssp126/run01).
          Added/removed by hand (via oceanicu_runs.py) -- this is "the
          table" a human edits to say what should be run.

  chunks  One row per executed chunk, appended automatically by
          run_chunks.py as it runs -- this is the ledger of what HAS been
          run. "What's next" is never stored: it's max(stop) for a run_id,
          computed on demand (see next_chunk_start).

Soft-kill / pause is checked in two independent ways, either one is
sufficient to pause: a `control` column on the run's own row (the normal,
auditable way -- see oceanicu_runs.py pause/resume), and filesystem
sentinel files (PAUSE in the run's own root, or PAUSE_ALL next to the
registry DB file itself -- see pause_all_sentinel_path) that work even if
the DB is unreachable during a real HPC-overload emergency -- `touch` is
always available, no tooling required.

A run's status only ever becomes "complete" (not just "complete_with_
warnings") when every chunk exited 0, none hit a NaN, and the last chunk's
stop reaches the run's own stop_date -- see recompute_run_status.

Working across machines with no direct network path between them
-------------------------------------------------------------------
Runs are often ADDED from one machine (wherever you're planning from) and
RUN on another (the SLURM/production machine) -- and those two often
can't reach each other directly at all. There's no multi-writer sync here
(SQLite doesn't do that safely, and periodically copying the file back
and forth risks one side's writes silently clobbering the other's).
Instead: the registry lives in exactly ONE place, and a THIRD machine
that BOTH sides can reach acts as a relay -- both the add-machine and the
production machine operate on the SAME database, remotely, over SSH to
that relay, via run_tracking_server.py (deployed there once).

Point at a relay instead of a local file by using an `ssh://` DB path:
    OCEANICU_RUN_DB=ssh://user@relay-host/abs/path/to/run_registry.sqlite
    OCEANICU_RELAY_DIR=/abs/path/to/oceanicu_3d   # where *_server.py lives on the relay
connect() below detects the ssh:// prefix and transparently switches to
RemoteConn (an RPC proxy: each call is one `ssh relay-host python
run_tracking_server.py` round trip carrying one JSON request/response) --
callers never need to know which mode they're in. Every function that
does no filesystem I/O of its own (the large majority: get_run,
start_chunk, finish_chunk, ...) is decorated with @_rpc_or_local and
"just works" either way, no per-function remote-handling code needed.

Two functions deliberately DON'T get that blanket treatment, because they
touch a filesystem path that is NOT necessarily where the DB lives:
is_paused's per-run `<run_root>/PAUSE` sentinel is a path on whichever
machine actually has that run's output (normally the production machine,
never the relay), so that one check always runs locally on the CALLING
machine, while the DB-column and PAUSE_ALL checks still go through the
(possibly remote) connection. next_run_to_start follows the same split.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import shlex
import sqlite3
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Union

RUN_CONTROLS = ("run", "pause_requested", "paused")
RUN_STATUSES = ("not_started", "in_progress", "paused", "complete", "complete_with_warnings", "failed")
CHUNK_STATUSES = ("running", "done", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    run_root         TEXT NOT NULL,
    script           TEXT NOT NULL,
    config           TEXT NOT NULL,
    data_roots_file  TEXT,
    initial_date     TEXT NOT NULL,
    stop_date        TEXT NOT NULL,
    chunk_kind       TEXT NOT NULL DEFAULT 'annual',
    chunk_multiplier INTEGER NOT NULL DEFAULT 1,
    np               INTEGER NOT NULL DEFAULT 1,
    launcher         TEXT NOT NULL DEFAULT 'srun',
    fabm             TEXT,
    status           TEXT NOT NULL DEFAULT 'not_started',
    control          TEXT NOT NULL DEFAULT 'run',
    priority         INTEGER NOT NULL DEFAULT 0,
    notes            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    run_id        TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    start         TEXT NOT NULL,
    stop          TEXT NOT NULL,
    chunk_dir     TEXT NOT NULL,
    load_restart  TEXT,
    save_restart  TEXT NOT NULL,
    slurm_job_id  TEXT,
    submit_time   TEXT,
    start_time    TEXT,
    end_time      TEXT,
    exit_code     INTEGER,
    nan_detected  INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'running',
    PRIMARY KEY (run_id, chunk_index),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_run ON chunks(run_id);

-- Append-only audit log: when a run was registered, when each chunk
-- started/finished, when the run itself reached a terminal state, and
-- every pause/resume/rerun/priority/stop-date/chunk-size change --
-- everything oceanicu_runs.py can do to a run, in the order it happened.
-- Deliberately NOT foreign-keyed to runs(run_id): remove_run only ever
-- deletes registry/chunk rows (see its own docstring), never touches
-- files -- history is the same idea applied to the audit trail itself,
-- so it survives a run being removed (and, if the same run_id is ever
-- re-added later, shows its full lifecycle across that gap too).
CREATE TABLE IF NOT EXISTS history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event     TEXT NOT NULL,
    detail    TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_run ON history(run_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_history(conn: sqlite3.Connection, run_id: str, event: str, detail: Optional[str] = None) -> None:
    """Append one audit-log row. Not itself @_rpc_or_local -- it's only
    ever called from inside another already-decorated function's own
    body, with a real conn either way (locally, or on the relay's own
    side of an RPC dispatch), so it needs no remote-transparency of its
    own. Caller is responsible for the surrounding conn.commit()."""
    conn.execute(
        "INSERT INTO history (run_id, timestamp, event, detail) VALUES (?, ?, ?, ?)",
        (run_id, _now(), event, detail),
    )


# ---------------------------------------------------------------------------
# Remote (relay) access -- see the module docstring's "Working across
# machines" section for why this exists and how the pieces fit together.
# ---------------------------------------------------------------------------

class RemoteSpec:
    """Parsed ssh://user@host/abs/path/to/db.sqlite. *relay_dir* (where
    run_tracking_server.py lives on the relay) always comes from
    OCEANICU_RELAY_DIR -- never encoded in the URI itself, since it's a
    property of the relay's own deployment, not of any one database."""

    def __init__(self, host: str, db_path: str, relay_dir: str):
        self.host = host
        self.db_path = db_path
        self.relay_dir = relay_dir

    def __repr__(self) -> str:
        return f"ssh://{self.host}{self.db_path} (via {self.relay_dir})"


def _parse_db_spec(spec: Union[str, Path, "RemoteSpec"]) -> Union[Path, "RemoteSpec"]:
    if isinstance(spec, RemoteSpec):
        return spec
    s = str(spec)
    if not s.startswith("ssh://"):
        return Path(s)
    rest = s[len("ssh://"):]
    if "/" not in rest:
        raise RuntimeError(f"malformed ssh:// DB path (expected ssh://host/abs/path): {s!r}")
    host, db_path = rest.split("/", 1)
    db_path = "/" + db_path
    relay_dir = os.environ.get("OCEANICU_RELAY_DIR")
    if not relay_dir:
        raise RuntimeError(
            f"{s!r} is a remote (ssh://) DB path, but OCEANICU_RELAY_DIR is not set -- "
            f"it must point at the directory on {host!r} where run_tracking_server.py lives."
        )
    return RemoteSpec(host, db_path, relay_dir)


class RemoteConn:
    """Stands in for a real sqlite3.Connection when the registry lives
    behind a relay. Every @_rpc_or_local-decorated function, called with
    one of these as *conn*, turns into exactly one `ssh host python
    run_tracking_server.py` round trip carrying its name + arguments as
    JSON and getting the (JSON-decoded) return value back -- the function
    body itself never runs locally in this case. Plain functions that
    aren't decorated (is_paused, next_run_to_start) call .call() directly
    for just the piece of themselves that's DB-shaped, and do their own
    filesystem checks locally regardless of conn's type -- see the module
    docstring."""

    def __init__(self, spec: RemoteSpec):
        self.spec = spec

    def call(self, func_name: str, kwargs: dict) -> Any:
        payload = json.dumps({"db": self.spec.db_path, "func": func_name, "kwargs": kwargs})
        remote_cmd = (
            f"cd {shlex.quote(self.spec.relay_dir)} && "
            f"python3 run_tracking_server.py"
        )
        try:
            result = subprocess.run(
                ["ssh", self.spec.host, remote_cmd],
                input=payload, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"relay call to {self.spec.host!r} timed out: {func_name}") from exc
        if result.returncode != 0 and not result.stdout.strip():
            raise RuntimeError(
                f"relay call to {self.spec.host!r} failed (ssh/transport error): "
                f"{result.stderr.strip() or 'no output'}"
            )
        try:
            response = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise RuntimeError(
                f"relay call to {self.spec.host!r} returned unparseable output: "
                f"{result.stdout!r} / stderr: {result.stderr!r}"
            ) from exc
        if not response.get("ok"):
            raise RuntimeError(f"relay call {func_name} failed on {self.spec.host!r}: "
                                f"{response.get('error', 'unknown error')}")
        return response.get("result")


def _rpc_or_local(func):
    """Decorator: functions with no filesystem I/O of their own (the
    large majority) become remote-transparent for free. When *conn* is a
    RemoteConn, forwards (func's own name, its bound kwargs minus conn)
    to conn.call() instead of running the function body; when *conn* is a
    real sqlite3.Connection (including on the relay's OWN side, inside
    run_tracking_server.py's dispatch -- nested calls between decorated
    functions Just Work, no double-dispatch, since conn there is real),
    runs the body exactly as before."""
    sig = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(conn, *args, **kwargs):
        if isinstance(conn, RemoteConn):
            bound = sig.bind_partial(conn, *args, **kwargs)
            bound.apply_defaults()
            call_kwargs = {k: v for k, v in bound.arguments.items() if k != "conn"}
            return conn.call(func.__name__, call_kwargs)
        return func(conn, *args, **kwargs)

    # Marks this specific function as safe to dispatch by name over RPC --
    # run_tracking_server.py's allow-list checks for this attribute, not a
    # naming convention, so it stays correct as functions are added/
    # renamed and isn't fooled by a leading underscore on an internal
    # helper (_control_or_pause_all, _not_started_candidates) that still
    # legitimately needs to be dispatchable.
    wrapper._is_rpc_dispatchable = True  # type: ignore[attr-defined]
    return wrapper


@_rpc_or_local
def list_history(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    """Full audit trail for one run, oldest first -- registration, every
    chunk start/finish, the run itself reaching a terminal status, and
    every pause/resume/rerun/priority/stop-date/chunk-size change. Shown
    by oceanicu_runs.py show."""
    return conn.execute(
        "SELECT * FROM history WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()


@contextmanager
def connect(
    db_path: Optional[Union[str, Path, "RemoteSpec"]] = None,
) -> Iterator[Union[sqlite3.Connection, "RemoteConn"]]:
    """Open the shared DB (WAL mode, for safe concurrent writers across
    simultaneously-running SLURM jobs on different runs), creating the
    schema on first use.

    *db_path* (or OCEANICU_RUN_DB if db_path isn't given) is required --
    deliberately no hardcoded default path. This code runs on whatever
    machine/cluster the job lands on, with whatever folder layout that
    machine has -- SQLite will happily CREATE a new, empty DB file at any
    writable path, so a wrong guess wouldn't even fail loudly, it would
    just silently start populating an unrelated registry. Point at a
    scratch DB during testing (OCEANICU_RUN_DB=/tmp/test.sqlite) with zero
    risk of touching the real one because a flag was forgotten.

    An `ssh://host/abs/path` value (or a RemoteSpec) yields a RemoteConn
    instead of opening anything locally -- see the module docstring's
    "Working across machines" section. Every @_rpc_or_local-decorated
    function accepts either kind of *conn* transparently.
    """
    env_path = os.environ.get("OCEANICU_RUN_DB")
    if db_path is None and not env_path:
        raise RuntimeError("No database path given: pass --db, or set OCEANICU_RUN_DB.")
    resolved = db_path if db_path is not None else env_path
    assert resolved is not None  # guaranteed by the check above
    spec = _parse_db_spec(resolved)
    if isinstance(spec, RemoteSpec):
        yield RemoteConn(spec)
        return
    path = spec
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        _ensure_group_writable(path)
        yield conn
    finally:
        conn.close()


def _ensure_group_writable(db_path: Path) -> None:
    """Make the DB file (and its WAL-mode -wal/-shm sidecars, once they
    exist) group-writable, regardless of whichever process's umask
    happened to create them. Matters specifically for a shared relay:
    SQLite creates new files honoring the CREATING process's umask, so
    without this, whichever of two different users (add-machine vs.
    production machine, see the "Working across machines" module
    docstring section) happens to touch a given file FIRST silently
    locks the other one out of writing to it -- even with correct shared
    -group membership, a common umask like 022 still drops the group-
    write bit on creation. Ownership/group itself is left alone (that's
    what the relay directory's own setgid bit + shared group are for --
    see RUN_TRACKING.md); this only ever adds permission bits, never
    narrows them, so it can't make anything LESS accessible than it
    already was. Best-effort: a PermissionError here (e.g. this process
    isn't the file's owner and isn't in its group) is not fatal --
    it just means whoever DOES need this fixed has to run `chmod`
    manually once, same as if this helper didn't exist at all.
    """
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        if not candidate.exists():
            continue
        try:
            candidate.chmod(candidate.stat().st_mode | 0o664)
        except PermissionError:
            pass


# ---------------------------------------------------------------------------
# runs table
# ---------------------------------------------------------------------------

@_rpc_or_local
def add_run(
    conn: sqlite3.Connection, *, run_id: str, run_root: str, script: str, config: str,
    initial_date: str, stop_date: str, data_roots_file: Optional[str] = None,
    chunk_kind: str = "annual", chunk_multiplier: int = 1, np: int = 1,
    launcher: str = "srun", priority: int = 0, notes: Optional[str] = None,
    fabm: Optional[str] = None,
) -> None:
    if launcher not in ("srun", "mpiexec"):
        raise ValueError(f"launcher must be 'srun' or 'mpiexec', got {launcher!r}")
    now = _now()
    conn.execute(
        """INSERT INTO runs (run_id, run_root, script, config, data_roots_file,
               initial_date, stop_date, chunk_kind, chunk_multiplier, np, launcher, fabm,
               status, control, priority, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'not_started', 'run', ?, ?, ?, ?)""",
        (run_id, run_root, script, config, data_roots_file, initial_date, stop_date,
         chunk_kind, chunk_multiplier, np, launcher, fabm, priority, notes, now, now),
    )
    _log_history(
        conn, run_id, "added",
        f"{chunk_multiplier} {chunk_kind} chunk(s) per step, {initial_date} -> {stop_date}, "
        f"priority={priority}, script={script}",
    )
    conn.commit()


@_rpc_or_local
def remove_run(conn: sqlite3.Connection, run_id: str, *, force: bool = False) -> None:
    row = get_run(conn, run_id)
    if row is None:
        raise KeyError(f"no such run_id: {run_id!r}")
    if row["status"] == "in_progress" and not force:
        raise ValueError(
            f"{run_id!r} is in_progress -- pause it first, or pass force=True "
            f"if you're sure (this only removes registry/chunk-history rows, "
            f"it never touches files on disk)."
        )
    # Logged before the delete, not after -- history itself is never
    # deleted (no FK to runs, see the schema's own comment), but it would
    # be a strange audit trail if its very last entry for a removed run
    # didn't mention the removal at all.
    _log_history(conn, run_id, "removed", "forced (was in_progress)" if force and row["status"] == "in_progress" else None)
    conn.execute("DELETE FROM chunks WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
    conn.commit()


@_rpc_or_local
def get_run(conn: sqlite3.Connection, run_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()


@_rpc_or_local
def list_runs(
    conn: sqlite3.Connection, *, status: Optional[str] = None,
) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT * FROM runs WHERE status = ? ORDER BY priority DESC, run_id", (status,)
        ).fetchall()
    return conn.execute("SELECT * FROM runs ORDER BY priority DESC, run_id").fetchall()


@_rpc_or_local
def set_priority(conn: sqlite3.Connection, run_id: str, priority: int) -> None:
    old = get_run(conn, run_id)
    conn.execute(
        "UPDATE runs SET priority = ?, updated_at = ? WHERE run_id = ?",
        (priority, _now(), run_id),
    )
    if old is not None:
        _log_history(conn, run_id, "priority_changed", f"{old['priority']} -> {priority}")
    conn.commit()


@_rpc_or_local
def set_stop_date(conn: sqlite3.Connection, run_id: str, stop_date: str) -> None:
    """Change a run's target end date, including while it's mid-run (e.g.
    currently at 2035, decide to stop at 2050 instead of 2099). Nothing
    caches stop_date -- chunk_runner.py and recompute_run_status both read
    it fresh from this row every time, so this takes effect on the very
    next chunk with no other bookkeeping needed. Shrinking it below a
    date already reached just means the run is already 'done' towards its
    (revised) goal -- it does not undo or delete any chunk already run
    past the new date."""
    old = get_run(conn, run_id)
    conn.execute(
        "UPDATE runs SET stop_date = ?, updated_at = ? WHERE run_id = ?",
        (stop_date, _now(), run_id),
    )
    if old is not None:
        _log_history(conn, run_id, "stop_date_changed", f"{old['stop_date']} -> {stop_date}")
    conn.commit()
    recompute_run_status(conn, run_id)


@_rpc_or_local
def pause_all_sentinel_path(conn: sqlite3.Connection) -> Path:
    """Path of the PAUSE_ALL sentinel: always right next to whatever
    registry DB file *conn* actually has open (via PRAGMA database_list,
    not a hardcoded location) -- so it's automatically correct on any
    machine/folder layout the DB happens to live on, no matter which one
    that is. `touch <this path>` pauses every run; `rm` resumes them.

    Dispatched wholesale like most functions here (PAUSE_ALL always lives
    with the DB, so relay-side execution is correct no matter which
    machine is asking) -- unlike is_paused/next_run_to_start, which also
    need a per-run filesystem check that is NOT relay-side, and so can't
    use this same blanket treatment for themselves as a whole."""
    row = conn.execute("PRAGMA database_list").fetchone()
    db_file = row["file"] if row is not None else None
    if not db_file:
        raise RuntimeError("could not determine the open database's file path")
    return Path(db_file).parent / "PAUSE_ALL"


@_rpc_or_local
def _control_or_pause_all(conn: sqlite3.Connection, run_id: str) -> bool:
    """The half of is_paused that lives wherever the DB lives (the DB
    control column, and the PAUSE_ALL sentinel next to the DB file) --
    dispatched as ONE round trip when conn is remote. is_paused adds the
    per-run PAUSE-file check on top of this, always locally."""
    if Path(pause_all_sentinel_path(conn)).exists():
        return True
    row = get_run(conn, run_id)
    return row is not None and row["control"] in ("pause_requested", "paused")


def is_paused(conn, run_id: str, run_root: Optional[str] = None) -> bool:
    """True if EITHER the DB control column, the PAUSE_ALL sentinel
    (both checked wherever the DB actually lives -- see
    _control_or_pause_all, one RPC round trip if conn is remote), OR the
    per-run `<run_root>/PAUSE` file (ALWAYS checked locally on whichever
    machine is calling this -- run_root is a path on whichever machine
    actually has that run's output, which is normally the production
    machine and is NEVER the relay) says to pause. Checked before every
    chunk launch and before every self-resubmission -- never mid-chunk,
    so a currently-running chunk always finishes cleanly first."""
    if _control_or_pause_all(conn, run_id):
        return True
    if run_root and (Path(run_root) / "PAUSE").exists():
        return True
    return False


@_rpc_or_local
def _not_started_candidates(conn: sqlite3.Connection) -> list:
    """run_id + run_root for every not_started, control='run' run,
    priority order -- the pure-DB half of next_run_to_start, dispatched
    as one round trip; the per-candidate pause check (which needs a
    local filesystem look at each candidate's OWN run_root) happens
    afterwards, one candidate at a time, always on the calling machine."""
    rows = conn.execute(
        "SELECT run_id, run_root FROM runs WHERE status = 'not_started' AND control = 'run' "
        "ORDER BY priority DESC, run_id"
    ).fetchall()
    return [dict(r) for r in rows]


def next_run_to_start(conn) -> Optional[str]:
    """Highest-priority run_id that hasn't started yet and isn't paused --
    what a run_chunk.slurm job chain should pick up next once its current
    run reaches stop_date. None if the queue is empty. Always called by
    whichever machine is about to actually pick up the next run (the
    production machine), so per-candidate PAUSE-file checks (via
    is_paused, see there) correctly use ITS OWN filesystem view of each
    run_root."""
    if Path(pause_all_sentinel_path(conn)).exists():
        return None
    for row in _not_started_candidates(conn):
        if not is_paused(conn, row["run_id"], row["run_root"]):
            return row["run_id"]
    return None


@_rpc_or_local
def set_chunk_settings(
    conn: sqlite3.Connection, run_id: str, *, chunk_kind: Optional[str] = None,
    chunk_multiplier: Optional[int] = None,
) -> None:
    """Change chunk size for the REMAINING (not-yet-run) part of a run.
    Already-completed chunks and their date-based restart filenames are
    untouched -- the next chunk simply picks up the new size."""
    old = get_run(conn, run_id)
    fields, params = [], []
    if chunk_kind is not None:
        fields.append("chunk_kind = ?"); params.append(chunk_kind)
    if chunk_multiplier is not None:
        fields.append("chunk_multiplier = ?"); params.append(chunk_multiplier)
    if not fields:
        return
    fields.append("updated_at = ?"); params.append(_now())
    params.append(run_id)
    conn.execute(f"UPDATE runs SET {', '.join(fields)} WHERE run_id = ?", params)
    if old is not None:
        changes = []
        if chunk_kind is not None and chunk_kind != old["chunk_kind"]:
            changes.append(f"chunk_kind: {old['chunk_kind']} -> {chunk_kind}")
        if chunk_multiplier is not None and chunk_multiplier != old["chunk_multiplier"]:
            changes.append(f"chunk_multiplier: {old['chunk_multiplier']} -> {chunk_multiplier}")
        if changes:
            _log_history(conn, run_id, "chunk_settings_changed", "; ".join(changes))
    conn.commit()


@_rpc_or_local
def set_control(conn: sqlite3.Connection, run_id: str, control: str) -> None:
    if control not in RUN_CONTROLS:
        raise ValueError(f"control must be one of {RUN_CONTROLS}, got {control!r}")
    conn.execute(
        "UPDATE runs SET control = ?, updated_at = ? WHERE run_id = ?",
        (control, _now(), run_id),
    )
    # "pause_requested"/"run" are what oceanicu_runs.py pause/resume
    # actually set -- named for what the user DID, not the raw column
    # value, so the history reads like an audit trail rather than a
    # column dump. "paused" is only ever set some other way (e.g. a
    # future direct API caller), kept as its own event for that case.
    event = {"pause_requested": "pause_requested", "run": "resumed", "paused": "paused"}[control]
    _log_history(conn, run_id, event)
    conn.commit()
    # Real, pre-existing inconsistency found 2026-08-28 while adding this
    # history log: neither this function nor chunk_runner.py's own
    # "already paused, not starting a new chunk" bail-out ever called
    # recompute_run_status, so the `status` column could lag `control`
    # indefinitely (is_paused() -- the check everything else actually
    # uses -- was always correct; only the displayed `status` enum was
    # stale). Fixed here since it's a one-line, clearly-safe addition
    # exactly where the inconsistency lives, not a separate unrelated
    # change.
    recompute_run_status(conn, run_id)


# ---------------------------------------------------------------------------
# chunks table
# ---------------------------------------------------------------------------

@_rpc_or_local
def next_chunk_index(conn: sqlite3.Connection, run_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(chunk_index), -1) + 1 AS n FROM chunks WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row["n"])


@_rpc_or_local
def next_chunk_start(conn: sqlite3.Connection, run_id: str, initial_date: str) -> str:
    """Resume point: the stop of the last chunk on record, or the run's
    own initial_date if nothing has run yet. This is the ONLY source of
    truth for 'what's next' -- no separately-stored plan to drift from
    reality."""
    row = conn.execute(
        "SELECT MAX(stop) AS s FROM chunks WHERE run_id = ? AND status = 'done'",
        (run_id,),
    ).fetchone()
    return row["s"] if row and row["s"] else initial_date


@_rpc_or_local
def start_chunk(
    conn: sqlite3.Connection, *, run_id: str, chunk_index: int, start: str, stop: str,
    chunk_dir: str, load_restart: Optional[str], save_restart: str,
    slurm_job_id: Optional[str] = None,
) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO chunks (run_id, chunk_index, start, stop, chunk_dir,
               load_restart, save_restart, slurm_job_id, submit_time,
               start_time, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')""",
        (run_id, chunk_index, start, stop, chunk_dir, load_restart, save_restart,
         slurm_job_id, now, now),
    )
    conn.execute(
        "UPDATE runs SET status = 'in_progress', updated_at = ? WHERE run_id = ? "
        "AND status != 'in_progress'",
        (now, run_id),
    )
    detail = f"chunk {chunk_index}: {start} -> {stop}"
    if load_restart:
        detail += f" (load_restart={load_restart})"
    _log_history(conn, run_id, "chunk_started", detail)
    conn.commit()


@_rpc_or_local
def finish_chunk(
    conn: sqlite3.Connection, *, run_id: str, chunk_index: int, exit_code: int,
    nan_detected: bool = False,
) -> None:
    # status reflects only whether the chunk's process itself completed --
    # nan_detected is tracked as an independent quality flag (see
    # recompute_run_status), not folded into status. In practice a live
    # NaN detection also scancels the job (so exit_code != 0 anyway), but
    # a chunk that completes cleanly (exit 0) and is flagged nan_detected
    # by some other check afterwards should still count as reached/done,
    # landing the RUN in complete_with_warnings rather than failed.
    status = "done" if exit_code == 0 else "failed"
    conn.execute(
        """UPDATE chunks SET end_time = ?, exit_code = ?, nan_detected = ?, status = ?
           WHERE run_id = ? AND chunk_index = ?""",
        (_now(), exit_code, int(nan_detected), status, run_id, chunk_index),
    )
    event = "chunk_finished" if exit_code == 0 else "chunk_failed"
    detail = f"chunk {chunk_index} exit_code={exit_code}" + (" nan_detected" if nan_detected else "")
    _log_history(conn, run_id, event, detail)
    conn.commit()
    new_status = recompute_run_status(conn, run_id)
    # "a full simulation stops" (user, 2026-08-28): a terminal status --
    # reached stop_date (clean or with warnings) or failed outright --
    # gets its own top-level history entry distinct from the per-chunk
    # one above, so scanning history for "did this run ever finish" (or
    # "when did it finish") doesn't require re-deriving it from chunk
    # rows every time.
    if new_status in ("complete", "complete_with_warnings", "failed"):
        _log_history(conn, run_id, f"run_{new_status}", f"after chunk {chunk_index}")
        conn.commit()


@_rpc_or_local
def get_running_chunk(conn: sqlite3.Connection, run_id: str) -> Optional[sqlite3.Row]:
    """The chunk currently marked 'running' for this run, if any -- used
    as a lock: chunk_runner.py refuses to start a new chunk while one is
    already recorded as running (see chunk_runner.py's own liveness/
    staleness check for what happens if that recorded chunk turns out to
    be a crashed/orphaned job rather than a genuinely active one)."""
    return conn.execute(
        "SELECT * FROM chunks WHERE run_id = ? AND status = 'running' "
        "ORDER BY chunk_index DESC LIMIT 1",
        (run_id,),
    ).fetchone()


@_rpc_or_local
def get_done_chunk_restart(conn: sqlite3.Connection, run_id: str, chunk_index: int) -> Optional[str]:
    """save_restart path of a specific completed chunk -- what the NEXT
    chunk's --load-restart should point at. Exists as its own named,
    dispatchable function (rather than chunk_runner.py running this
    query directly against conn) specifically so it keeps working when
    conn is a RemoteConn: raw ad-hoc SQL outside this module has no way
    to cross the relay, only named functions do."""
    row = conn.execute(
        "SELECT save_restart FROM chunks WHERE run_id = ? AND chunk_index = ? AND status = 'done'",
        (run_id, chunk_index),
    ).fetchone()
    return row["save_restart"] if row else None


@_rpc_or_local
def list_chunks(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chunks WHERE run_id = ? ORDER BY chunk_index", (run_id,)
    ).fetchall()


@_rpc_or_local
def rerun_from(conn: sqlite3.Connection, run_id: str, *, chunk_index: Optional[int] = None) -> int:
    """Rewind a run's tracked history so its NEXT execution redoes chunk
    *chunk_index* onward (dropping any record of it and everything after).
    Chunks before it are untouched, so the redo naturally resumes from the
    same load_restart as before -- next_chunk_start/next_chunk_index just
    read whatever's left. Three cases, all this one primitive:

      chunk_index=None  "from the present chunk" -- redo the last chunk on
                         record (whether it failed or you just want it
                         redone), keep everything before it.
      chunk_index=N      "from a set chunk" -- redo chunk N onward.
      chunk_index=0      "from scratch" -- drops every chunk; the next run
                         starts at initial_date with no load_restart.

    Does NOT touch files on disk (no chunk_dir is deleted) -- run_chunks.py
    archives a pre-existing chunk_dir aside before reusing it, so a
    previous attempt's logs/output are never silently overwritten.
    """
    if chunk_index is None:
        row = conn.execute(
            "SELECT MAX(chunk_index) AS n FROM chunks WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or row["n"] is None:
            return 0  # nothing has run yet, nothing to rewind
        chunk_index = int(row["n"])

    cur = conn.execute(
        "DELETE FROM chunks WHERE run_id = ? AND chunk_index >= ?", (run_id, chunk_index)
    )
    n_dropped = cur.rowcount
    conn.execute(
        "UPDATE runs SET control = 'run', updated_at = ? WHERE run_id = ?",
        (_now(), run_id),
    )
    _log_history(conn, run_id, "rerun", f"dropped {n_dropped} chunk(s) from index {chunk_index} onward")
    conn.commit()
    recompute_run_status(conn, run_id)
    return n_dropped


@_rpc_or_local
def recompute_run_status(conn: sqlite3.Connection, run_id: str) -> str:
    """Derive the run-level status from its chunk history -- 'complete' is
    reserved for a genuinely clean finish (see module docstring); a run
    that reached its stop_date via a retried or NaN-recovered chunk lands
    in 'complete_with_warnings' instead, so a clean pass is visible at a
    glance without reading chunk history."""
    run = get_run(conn, run_id)
    if run is None:
        raise KeyError(f"no such run_id: {run_id!r}")
    chunks = list_chunks(conn, run_id)
    now = _now()

    if not chunks:
        new_status = "not_started"
    elif any(c["status"] == "running" for c in chunks):
        new_status = "in_progress"
    elif any(c["status"] == "failed" for c in chunks) and chunks[-1]["status"] == "failed":
        new_status = "failed"
    else:
        done = [c for c in chunks if c["status"] == "done"]
        reached_end = bool(done) and done[-1]["stop"] >= run["stop_date"]
        any_warning = any(c["status"] == "failed" or c["nan_detected"] for c in chunks)
        if reached_end and not any_warning:
            new_status = "complete"
        elif reached_end:
            new_status = "complete_with_warnings"
        elif run["control"] in ("paused", "pause_requested"):
            new_status = "paused"
        else:
            new_status = "in_progress"

    conn.execute(
        "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
        (new_status, now, run_id),
    )
    conn.commit()
    return new_status
