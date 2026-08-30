#!/usr/bin/env python
"""experiment_tracking.py -- SQLite-backed tracking for chunked production experiments.

Two tables:

  experiments   One row per simulation (e.g. NSe/CMIP6/CNRM-ESM2-1/ssp126/run01).
                Added/removed by hand (via oceanicu_experiments.py) -- this
                is "the table" a human edits to say what should be run.

  chunks        One row per executed chunk, appended automatically by
                chunk_runner.py as it runs -- this is the ledger of what HAS
                been run. "What's next" is never stored: it's max(stop) for
                an experiment_id, computed on demand (see next_chunk_start).

Soft-kill / pause is checked in two independent ways, either one is
sufficient to pause: a `control` column on the experiment's own row (the
normal, auditable way -- see oceanicu_experiments.py pause/resume), and
filesystem sentinel files (PAUSE in the experiment's own root, or PAUSE_ALL
next to the registry DB file itself -- see pause_all_sentinel_path) that
work even if the DB is unreachable during a real HPC-overload emergency --
`touch` is always available, no tooling required.

An experiment's status only ever becomes "complete" (not just "complete_with_
warnings") when every chunk exited 0, none hit a NaN, and the last chunk's
stop reaches the experiment's own stop_date -- see recompute_experiment_status.

Working across machines with no direct network path between them
-------------------------------------------------------------------
Experiments are often ADDED from one machine (wherever you're planning from)
and RUN on another (the SLURM/production machine) -- and those two often
can't reach each other directly at all. There's no multi-writer sync here
(SQLite doesn't do that safely, and periodically copying the file back
and forth risks one side's writes silently clobbering the other's).
Instead: the registry lives in exactly ONE place, and a THIRD machine
that BOTH sides can reach acts as a relay -- both the add-machine and the
production machine operate on the SAME database, remotely, over SSH to
that relay, via experiment_tracking_server.py (deployed there once).

Point at a relay instead of a local file by using an `ssh://` DB path:
    OCEANICU_EXPERIMENT_DB=ssh://user@relay-host/abs/path/to/experiment_registry.sqlite
    OCEANICU_RELAY_DIR=/abs/path/to/oceanicu_3d   # where *_server.py lives on the relay
connect() below detects the ssh:// prefix and transparently switches to
RemoteConn (an RPC proxy: each call is one `ssh relay-host python
experiment_tracking_server.py` round trip carrying one JSON request/response) --
callers never need to know which mode they're in. Every function that
does no filesystem I/O of its own (the large majority: get_experiment,
start_chunk, finish_chunk, ...) is decorated with @_rpc_or_local and
"just works" either way, no per-function remote-handling code needed.

Two functions deliberately DON'T get that blanket treatment, because they
touch a filesystem path that is NOT necessarily where the DB lives:
is_paused's per-experiment `<experiment_root>/PAUSE` sentinel is a path on
whichever machine actually has that experiment's output (normally the
production machine, never the relay), so that one check always runs locally
on the CALLING
machine, while the DB-column and PAUSE_ALL checks still go through the
(possibly remote) connection. next_experiment_to_start follows the same split.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Union

EXPERIMENT_CONTROLS = ("run", "pause_requested", "paused")
EXPERIMENT_STATUSES = ("not_started", "in_progress", "paused", "complete", "complete_with_warnings", "failed")
CHUNK_STATUSES = ("running", "done", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id           TEXT PRIMARY KEY,
    experiment_root         TEXT NOT NULL,
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
    -- chunk_delay_seconds added 2026-08-28 (see _migrate_schema for an
    -- already-existing experiments table -- CREATE TABLE IF NOT EXISTS
    -- alone won't add it there). Persistent, per-experiment pacing: wait
    -- this many seconds before EACH resubmission of this experiment's own
    -- chunks, or before picking this experiment up as the next queued one
    -- -- unlike DELAY_ALL (a global, one-shot TIMED pause), this is an
    -- ongoing setting for this experiment specifically, exactly the same shape as
    -- chunk_multiplier/priority: default 0 (no delay, previous
    -- behaviour), settable at `add` time, changeable live via
    -- set-chunk-delay -- takes effect on the very next hand-off, never
    -- retroactively, same as chunk_multiplier/stop_date already do.
    chunk_delay_seconds INTEGER NOT NULL DEFAULT 0,
    notes            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    experiment_id        TEXT NOT NULL,
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
    -- script_sha256/config_sha256 added 2026-08-28 (see _migrate_schema --
    -- CREATE TABLE IF NOT EXISTS alone won't add columns to an
    -- already-existing chunks table). Content hash of experiment['script']/
    -- ['config'] AT THE MOMENT this chunk started -- lets a human editing
    -- the driver script after a failure (a real, expected workflow: chunk
    -- blows up, fix a bug in the script, `rerun --from-current`) actually
    -- SEE that the script changed between attempts, rather than the DB
    -- only ever recording the unchanging path string. See
    -- chunk_runner.py's own "script_changed"/"config_changed" history
    -- event, logged automatically by comparing this chunk's hash against
    -- the experiment's previous chunk.
    script_sha256 TEXT,
    config_sha256 TEXT,
    -- submitted_host added 2026-08-28 (see _migrate_schema). Hostname of
    -- whichever machine actually issued this chunk's submission --
    -- normally the production machine's own sbatch self-resubmission,
    -- but chunk_runner.py can also be invoked by hand for testing, so
    -- this records reality per chunk rather than assuming.
    submitted_host TEXT,
    PRIMARY KEY (experiment_id, chunk_index),
    FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_experiment ON chunks(experiment_id);

-- Append-only audit log: when an experiment was registered, when each chunk
-- started/finished, when the experiment itself reached a terminal state, and
-- every pause/resume/rerun/priority/stop-date/chunk-size change --
-- everything oceanicu_experiments.py can do to an experiment, in the order
-- it happened. Deliberately NOT foreign-keyed to experiments(experiment_id):
-- remove_experiment only ever deletes registry/chunk rows (see its own
-- docstring), never touches
-- files -- history is the same idea applied to the audit trail itself,
-- so it survives an experiment being removed (and, if the same experiment_id is ever
-- re-added later, shows its full lifecycle across that gap too).
-- `user` added 2026-08-28 (see _migrate_schema -- CREATE TABLE IF NOT
-- EXISTS alone won't add a column to an already-existing history table
-- on a registry that predates this, hence the separate ALTER TABLE
-- migration rather than just listing it here).
CREATE TABLE IF NOT EXISTS history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id    TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event     TEXT NOT NULL,
    detail    TEXT,
    user      TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_run ON history(experiment_id);
"""


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Additive, idempotent schema fixups that CREATE TABLE IF NOT EXISTS
    alone can't express -- new columns on a table that may already exist
    from before the column was added. Safe to run on every connect(): each
    check is cheap and a no-op once already applied."""
    history_cols = {row["name"] for row in conn.execute("PRAGMA table_info(history)")}
    if "user" not in history_cols:
        conn.execute("ALTER TABLE history ADD COLUMN user TEXT")
        conn.commit()

    chunk_cols = {row["name"] for row in conn.execute("PRAGMA table_info(chunks)")}
    if "script_sha256" not in chunk_cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN script_sha256 TEXT")
        conn.commit()
    if "config_sha256" not in chunk_cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN config_sha256 TEXT")
        conn.commit()
    if "submitted_host" not in chunk_cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN submitted_host TEXT")
        conn.commit()

    run_cols = {row["name"] for row in conn.execute("PRAGMA table_info(experiments)")}
    if "chunk_delay_seconds" not in run_cols:
        conn.execute("ALTER TABLE experiments ADD COLUMN chunk_delay_seconds INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _current_user() -> str:
    """Best-effort identity of whoever is actually running this command,
    for the history log. Captured at the ORIGINAL call site
    (oceanicu_experiments.py's CLI entry points, chunk_runner.py's own
    process) and
    threaded through as an explicit `user` argument -- NOT derived lazily
    inside _log_history itself, because a @_rpc_or_local-decorated
    function's body can end up executing on the RELAY's own process (see
    RemoteConn/run_tracking_server.py) under a shared service account
    that has nothing to do with which human actually typed the command on
    their own machine. os.environ checks first (USER/LOGNAME cover the
    common cases cheaply); getpass.getuser() as a last resort (it itself
    checks the same env vars first, then falls back to the pwd database)."""
    for var in ("USER", "LOGNAME"):
        val = os.environ.get(var)
        if val:
            return val
    import getpass
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _log_history(
    conn: sqlite3.Connection, experiment_id: str, event: str, detail: Optional[str] = None,
    user: Optional[str] = None,
) -> None:
    """Append one audit-log row. Not itself @_rpc_or_local -- it's only
    ever called from inside another already-decorated function's own
    body, with a real conn either way (locally, or on the relay's own
    side of an RPC dispatch), so it needs no remote-transparency of its
    own. Caller is responsible for the surrounding conn.commit().

    *user* is whatever the outer, already-decorated function was itself
    given (see _current_user's own docstring for why this is passed in
    rather than computed here) -- None for the rare internal call that
    doesn't have one to offer, which just stores NULL."""
    conn.execute(
        "INSERT INTO history (experiment_id, timestamp, event, detail, user) VALUES (?, ?, ?, ?, ?)",
        (experiment_id, _now(), event, detail, user),
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
    aren't decorated (is_paused, next_experiment_to_start) call .call() directly
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
def list_history(conn: sqlite3.Connection, experiment_id: str) -> list[sqlite3.Row]:
    """Full audit trail for one experiment, oldest first -- registration, every
    chunk start/finish, the experiment itself reaching a terminal status, and
    every pause/resume/rerun/priority/stop-date/chunk-size change. Shown
    by oceanicu_experiments.py show."""
    return conn.execute(
        "SELECT * FROM history WHERE experiment_id = ? ORDER BY id", (experiment_id,)
    ).fetchall()


@contextmanager
def connect(
    db_path: Optional[Union[str, Path, "RemoteSpec"]] = None,
) -> Iterator[Union[sqlite3.Connection, "RemoteConn"]]:
    """Open the shared DB (WAL mode, for safe concurrent writers across
    simultaneously-running SLURM jobs on different experiments), creating the
    schema on first use.

    *db_path* (or OCEANICU_EXPERIMENT_DB if db_path isn't given) is required --
    deliberately no hardcoded default path. This code runs on whatever
    machine/cluster the job lands on, with whatever folder layout that
    machine has -- SQLite will happily CREATE a new, empty DB file at any
    writable path, so a wrong guess wouldn't even fail loudly, it would
    just silently start populating an unrelated registry. Point at a
    scratch DB during testing (OCEANICU_EXPERIMENT_DB=/tmp/test.sqlite) with zero
    risk of touching the real one because a flag was forgotten.

    An `ssh://host/abs/path` value (or a RemoteSpec) yields a RemoteConn
    instead of opening anything locally -- see the module docstring's
    "Working across machines" section. Every @_rpc_or_local-decorated
    function accepts either kind of *conn* transparently.
    """
    env_path = os.environ.get("OCEANICU_EXPERIMENT_DB")
    if db_path is None and not env_path:
        raise RuntimeError("No database path given: pass --db, or set OCEANICU_EXPERIMENT_DB.")
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
        _migrate_schema(conn)
        _ensure_group_writable(path)
        yield conn
    finally:
        _maybe_backup_after_write(conn, path)
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
    see EXPERIMENT_TRACKING.md); this only ever adds permission bits, never
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
# Accidental-deletion protection: an append-only git history of the DB
# itself, in a small local repo next to it. `rm experiment_registry.sqlite` (or a
# botched edit) is otherwise unrecoverable -- the DB doesn't live in a repo
# of its own, and SQLite has no snapshot/undo concept beyond "the current
# database" (checkpointing just folds the WAL into the main file, it isn't
# a backup -- see EXPERIMENT_TRACKING.md). Every N writes (default 5, see
# OCEANICU_DB_BACKUP_EVERY_N_WRITES), take a WAL-safe snapshot via
# sqlite3's own backup API (NOT a raw file copy, which can catch the main
# file mid-checkpoint and miss pending WAL content) and commit it. Hooked
# into connect()'s local-mode teardown -- the one chokepoint every write
# passes through either directly or, via run_tracking_server.py's own
# connect() call, on the relay side of an RPC dispatch.
# ---------------------------------------------------------------------------

_DEFAULT_BACKUP_EVERY_N_WRITES = 5


def _backup_repo_dir(db_path: Path) -> Path:
    return db_path.parent / f"{db_path.name}.backups"


def _backup_write_count_path(db_path: Path) -> Path:
    return db_path.parent / f".{db_path.name}.backup_write_count"


def _maybe_backup_after_write(conn: sqlite3.Connection, db_path: Path) -> None:
    """Called right before closing a connection that actually wrote
    something (conn.total_changes > 0 -- read-only connections, e.g. from
    `list`/`show`, never trigger this). Best-effort throughout: a backup
    failure must never break the real command that triggered it, same
    spirit as _ensure_group_writable above."""
    if conn.total_changes <= 0:
        return
    try:
        n = int(os.environ.get("OCEANICU_DB_BACKUP_EVERY_N_WRITES", _DEFAULT_BACKUP_EVERY_N_WRITES))
    except ValueError:
        n = _DEFAULT_BACKUP_EVERY_N_WRITES
    if n <= 0:
        return  # explicitly disabled

    count_path = _backup_write_count_path(db_path)
    try:
        count = int(count_path.read_text().strip()) if count_path.exists() else 0
    except (ValueError, OSError):
        count = 0
    count += 1

    if count < n:
        try:
            count_path.write_text(str(count))
        except OSError:
            pass
        return

    try:
        _write_backup_snapshot_and_commit(conn, db_path, writes_covered=count)
        count_path.write_text("0")
    except Exception as exc:  # noqa: BLE001 -- best-effort, never fatal
        print(f"WARNING: DB backup snapshot failed (continuing anyway): {exc}", file=sys.stderr)
        # Don't reset the counter on failure -- retry at the next write
        # instead of silently going quiet for a full N-write cycle.
        try:
            count_path.write_text(str(count))
        except OSError:
            pass


def _write_backup_snapshot_and_commit(conn: sqlite3.Connection, db_path: Path, writes_covered: int) -> None:
    repo_dir = _backup_repo_dir(db_path)
    repo_dir.mkdir(parents=True, exist_ok=True)
    if not (repo_dir / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)

    snapshot_path = repo_dir / db_path.name
    # sqlite3's own backup API, not shutil.copy -- WAL-safe (takes a real
    # transactional snapshot, including anything only in the -wal file so
    # far), unlike a raw file copy which can catch the main file mid-write.
    dest = sqlite3.connect(str(snapshot_path))
    try:
        conn.backup(dest)
    finally:
        dest.close()

    git_env_args = [
        "-c", "user.email=run_tracking_backup@localhost",
        "-c", "user.name=run_tracking_backup",
    ]
    subprocess.run(["git", *git_env_args, "add", db_path.name], cwd=repo_dir, check=True)
    # Nothing to commit if this snapshot is byte-identical to the last one
    # (e.g. a write that got rolled back) -- diff-index exits non-zero only
    # when there ARE staged changes, so skip the commit rather than fail.
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir).returncode != 0
    if staged:
        subprocess.run(
            ["git", *git_env_args, "commit", "-q", "-m",
             f"snapshot after {writes_covered} write(s), {datetime.now(timezone.utc).isoformat()}"],
            cwd=repo_dir, check=True,
        )


# ---------------------------------------------------------------------------
# experiments table
# ---------------------------------------------------------------------------

@_rpc_or_local
def add_experiment(
    conn: sqlite3.Connection, *, experiment_id: str, experiment_root: str, script: str, config: str,
    initial_date: str, stop_date: str, data_roots_file: Optional[str] = None,
    chunk_kind: str = "annual", chunk_multiplier: int = 1, np: int = 1,
    launcher: str = "srun", priority: int = 0, notes: Optional[str] = None,
    fabm: Optional[str] = None, chunk_delay_seconds: int = 0, user: Optional[str] = None,
) -> None:
    if launcher not in ("srun", "mpiexec"):
        raise ValueError(f"launcher must be 'srun' or 'mpiexec', got {launcher!r}")
    now = _now()
    conn.execute(
        """INSERT INTO experiments (experiment_id, experiment_root, script, config, data_roots_file,
               initial_date, stop_date, chunk_kind, chunk_multiplier, np, launcher, fabm,
               status, control, priority, chunk_delay_seconds, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'not_started', 'run', ?, ?, ?, ?, ?)""",
        (experiment_id, experiment_root, script, config, data_roots_file, initial_date, stop_date,
         chunk_kind, chunk_multiplier, np, launcher, fabm, priority, chunk_delay_seconds,
         notes, now, now),
    )
    detail = (
        f"{chunk_multiplier} {chunk_kind} chunk(s) per step, {initial_date} -> {stop_date}, "
        f"priority={priority}, script={script}"
    )
    if chunk_delay_seconds:
        detail += f", chunk_delay_seconds={chunk_delay_seconds}"
    _log_history(conn, experiment_id, "added", detail, user=user)
    conn.commit()


@_rpc_or_local
def remove_experiment(conn: sqlite3.Connection, experiment_id: str, *, force: bool = False, user: Optional[str] = None) -> None:
    row = get_experiment(conn, experiment_id)
    if row is None:
        raise KeyError(f"no such experiment_id: {experiment_id!r}")
    if row["status"] == "in_progress" and not force:
        raise ValueError(
            f"{experiment_id!r} is in_progress -- pause it first, or pass force=True "
            f"if you're sure (this only removes registry/chunk-history rows, "
            f"it never touches files on disk)."
        )
    # Logged before the delete, not after -- history itself is never
    # deleted (no FK to experiments, see the schema's own comment), but it would
    # be a strange audit trail if its very last entry for a removed experiment
    # didn't mention the removal at all.
    _log_history(
        conn, experiment_id, "removed",
        "forced (was in_progress)" if force and row["status"] == "in_progress" else None,
        user=user,
    )
    conn.execute("DELETE FROM chunks WHERE experiment_id = ?", (experiment_id,))
    conn.execute("DELETE FROM experiments WHERE experiment_id = ?", (experiment_id,))
    conn.commit()


@_rpc_or_local
def get_experiment(conn: sqlite3.Connection, experiment_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()


@_rpc_or_local
def list_experiments(
    conn: sqlite3.Connection, *, status: Optional[str] = None,
) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT * FROM experiments WHERE status = ? ORDER BY priority DESC, experiment_id", (status,)
        ).fetchall()
    return conn.execute("SELECT * FROM experiments ORDER BY priority DESC, experiment_id").fetchall()


@_rpc_or_local
def set_priority(conn: sqlite3.Connection, experiment_id: str, priority: int, user: Optional[str] = None) -> None:
    old = get_experiment(conn, experiment_id)
    conn.execute(
        "UPDATE experiments SET priority = ?, updated_at = ? WHERE experiment_id = ?",
        (priority, _now(), experiment_id),
    )
    if old is not None:
        _log_history(conn, experiment_id, "priority_changed", f"{old['priority']} -> {priority}", user=user)
    conn.commit()


@_rpc_or_local
def set_data_roots_file(
    conn: sqlite3.Connection, experiment_id: str, data_roots_file: Optional[str], user: Optional[str] = None,
) -> None:
    """Change which data-roots file an experiment's chunks use, same reason
    experiment_root itself can be relative (see resolve_experiment_root): the machine
    that added an experiment doesn't always know the right data-roots file for
    wherever it actually ends up running, or that may simply change over
    the experiment's lifetime (new data mount, different machine). Read fresh at
    each chunk hand-off (chunk_runner.py resolves it against experiment_root the
    same way as script/config, if it's a bare filename), same
    next-hand-off-only semantics as set_priority/set_chunk_delay."""
    old = get_experiment(conn, experiment_id)
    conn.execute(
        "UPDATE experiments SET data_roots_file = ?, updated_at = ? WHERE experiment_id = ?",
        (data_roots_file, _now(), experiment_id),
    )
    if old is not None:
        _log_history(
            conn, experiment_id, "data_roots_file_changed",
            f"{old['data_roots_file']!r} -> {data_roots_file!r}", user=user,
        )
    conn.commit()


@_rpc_or_local
def set_np(conn: sqlite3.Connection, experiment_id: str, np: int, user: Optional[str] = None) -> None:
    """Change an experiment's process count, e.g. after finding the original --np
    was wrong for the actual production machine's node layout. Read fresh
    at each chunk hand-off, same next-hand-off-only semantics as
    set_priority/set_chunk_delay -- never affects a chunk already
    running."""
    old = get_experiment(conn, experiment_id)
    conn.execute(
        "UPDATE experiments SET np = ?, updated_at = ? WHERE experiment_id = ?",
        (np, _now(), experiment_id),
    )
    if old is not None:
        _log_history(conn, experiment_id, "np_changed", f"{old['np']} -> {np}", user=user)
    conn.commit()


@_rpc_or_local
def set_chunk_delay(
    conn: sqlite3.Connection, experiment_id: str, chunk_delay_seconds: int, user: Optional[str] = None,
) -> None:
    """Persistent, per-experiment pacing (see the schema's own comment on this
    column): wait this many seconds before EACH future resubmission of
    this experiment's own chunks, or before picking it up as the next queued
    experiment. Unlike DELAY_ALL (a global, one-shot TIMED pause), this has no
    expiry -- 0 (the default) means no delay; set back to 0 to cancel.
    Takes effect on the very next hand-off, never retroactively, same as
    set_priority/set_stop_date/set_chunk_settings."""
    old = get_experiment(conn, experiment_id)
    conn.execute(
        "UPDATE experiments SET chunk_delay_seconds = ?, updated_at = ? WHERE experiment_id = ?",
        (chunk_delay_seconds, _now(), experiment_id),
    )
    if old is not None:
        _log_history(
            conn, experiment_id, "chunk_delay_changed",
            f"{old['chunk_delay_seconds']} -> {chunk_delay_seconds}", user=user,
        )
    conn.commit()


@_rpc_or_local
def set_stop_date(conn: sqlite3.Connection, experiment_id: str, stop_date: str, user: Optional[str] = None) -> None:
    """Change an experiment's target end date, including while it's mid-experiment (e.g.
    currently at 2035, decide to stop at 2050 instead of 2099). Nothing
    caches stop_date -- chunk_runner.py and recompute_experiment_status both read
    it fresh from this row every time, so this takes effect on the very
    next chunk with no other bookkeeping needed. Shrinking it below a
    date already reached just means the experiment is already 'done' towards its
    (revised) goal -- it does not undo or delete any chunk already run
    past the new date."""
    old = get_experiment(conn, experiment_id)
    conn.execute(
        "UPDATE experiments SET stop_date = ?, updated_at = ? WHERE experiment_id = ?",
        (stop_date, _now(), experiment_id),
    )
    if old is not None:
        _log_history(conn, experiment_id, "stop_date_changed", f"{old['stop_date']} -> {stop_date}", user=user)
    conn.commit()
    recompute_experiment_status(conn, experiment_id)


@_rpc_or_local
def chunk_delay_sentinel_path(conn: sqlite3.Connection) -> Path:
    """Path of the DELAY_ALL sentinel: same idea and same location
    convention as pause_all_sentinel_path (right next to the registry DB
    file, via PRAGMA database_list), but for a TIMED pause rather than an
    indefinite one -- "the HPC needs to be used for something else for a
    while" (user, 2026-08-28), not "stop until a human says resume".

    Content is the delay in seconds; the file's own mtime marks when it
    was set. resume_time = mtime + int(content). Checked fresh right
    before every self-resubmission (run_chunk.slurm / run_chunk_local.py)
    -- if still before resume_time, that hop sleeps out the remainder
    then proceeds automatically, no manual `resume` needed; once elapsed,
    the file is simply inert (no auto-cleanup) until touched again with a
    fresh value. `echo 3600 > <this path>` delays 1 hour from now; `rm`
    (or letting it expire) goes back to normal. Unlike PAUSE_ALL this
    never blocks a chunk from ever running -- it only ever adds a bounded
    wait at the hand-off between one finishing and the next starting."""
    row = conn.execute("PRAGMA database_list").fetchone()
    db_file = row["file"] if row is not None else None
    if not db_file:
        raise RuntimeError("could not determine the open database's file path")
    return Path(db_file).parent / "DELAY_ALL"


def get_chunk_delay_remaining(conn) -> float:
    """Seconds still remaining on the DELAY_ALL sentinel (see its own
    docstring), 0.0 if absent, unreadable, or already expired. Not
    @_rpc_or_local: like is_paused/next_experiment_to_start, this needs a LOCAL
    filesystem check (the sentinel lives next to wherever the DB actually
    is, which for a RemoteConn is the relay, not necessarily reachable by
    plain Path() from wherever this is called) -- calls
    chunk_delay_sentinel_path (relay-transparent on its own) for the path,
    then always stats it locally on whichever machine is asking, exactly
    mirroring _control_or_pause_all's own split."""
    try:
        path = Path(chunk_delay_sentinel_path(conn))
    except RuntimeError:
        return 0.0
    if not path.exists():
        return 0.0
    try:
        delay_seconds = float(path.read_text().strip())
        resume_time = path.stat().st_mtime + delay_seconds
    except (ValueError, OSError):
        return 0.0
    remaining = resume_time - time.time()
    return remaining if remaining > 0 else 0.0


@_rpc_or_local
def pause_all_sentinel_path(conn: sqlite3.Connection) -> Path:
    """Path of the PAUSE_ALL sentinel: always right next to whatever
    registry DB file *conn* actually has open (via PRAGMA database_list,
    not a hardcoded location) -- so it's automatically correct on any
    machine/folder layout the DB happens to live on, no matter which one
    that is. `touch <this path>` pauses every experiment; `rm` resumes them.

    Dispatched wholesale like most functions here (PAUSE_ALL always lives
    with the DB, so relay-side execution is correct no matter which
    machine is asking) -- unlike is_paused/next_experiment_to_start, which also
    need a per-experiment filesystem check that is NOT relay-side, and so can't
    use this same blanket treatment for themselves as a whole."""
    row = conn.execute("PRAGMA database_list").fetchone()
    db_file = row["file"] if row is not None else None
    if not db_file:
        raise RuntimeError("could not determine the open database's file path")
    return Path(db_file).parent / "PAUSE_ALL"


@_rpc_or_local
def _control_or_pause_all(conn: sqlite3.Connection, experiment_id: str) -> bool:
    """The half of is_paused that lives wherever the DB lives (the DB
    control column, and the PAUSE_ALL sentinel next to the DB file) --
    dispatched as ONE round trip when conn is remote. is_paused adds the
    per-experiment PAUSE-file check on top of this, always locally."""
    if Path(pause_all_sentinel_path(conn)).exists():
        return True
    row = get_experiment(conn, experiment_id)
    return row is not None and row["control"] in ("pause_requested", "paused")


def resolve_experiment_root(experiment_root: str) -> str:
    """Resolve a possibly-relative experiment_root against THIS machine's own
    OCEANICU_EXPERIMENT_ROOT_BASE -- a per-machine env var, not stored in the DB
    (mirrors OCEANICU_EXPERIMENT_DB/OCEANICU_RELAY_DIR). Lets an experiment be
    registered from a workstation that doesn't know exactly where its
    output will land on the production machine: register with a relative
    experiment_root (e.g. matching experiment_id's own shape), and each machine that
    actually touches the filesystem (chunk_runner.py, is_paused, this
    script) resolves it against its own local base path at the point of
    use. Same idea as script/config bare filenames already being resolved
    against experiment_root itself -- one more level up.

    An absolute experiment_root is returned unchanged; no base path needed for
    those, and existing experiments registered with an absolute experiment_root keep
    working exactly as before."""
    if Path(experiment_root).is_absolute():
        return experiment_root
    base = os.environ.get("OCEANICU_EXPERIMENT_ROOT_BASE")
    if not base:
        raise RuntimeError(
            f"experiment_root {experiment_root!r} is relative but OCEANICU_EXPERIMENT_ROOT_BASE is not "
            f"set in the environment on this machine -- export "
            f"OCEANICU_EXPERIMENT_ROOT_BASE=/abs/path (each machine that touches this "
            f"experiment's files sets its own), or register the experiment with an absolute "
            f"--experiment-root instead."
        )
    return str(Path(base) / experiment_root)


def is_paused(conn, experiment_id: str, experiment_root: Optional[str] = None) -> bool:
    """True if EITHER the DB control column, the PAUSE_ALL sentinel
    (both checked wherever the DB actually lives -- see
    _control_or_pause_all, one RPC round trip if conn is remote), OR the
    per-experiment `<experiment_root>/PAUSE` file (ALWAYS checked locally on whichever
    machine is calling this -- experiment_root is a path on whichever machine
    actually has that experiment's output, which is normally the production
    machine and is NEVER the relay; resolved against this machine's own
    OCEANICU_EXPERIMENT_ROOT_BASE first if it's relative, see resolve_experiment_root)
    says to pause. Checked before every chunk launch and before every
    self-resubmission -- never mid-chunk, so a currently-running chunk
    always finishes cleanly first."""
    if _control_or_pause_all(conn, experiment_id):
        return True
    if experiment_root and (Path(resolve_experiment_root(experiment_root)) / "PAUSE").exists():
        return True
    return False


@_rpc_or_local
def _not_started_candidates(conn: sqlite3.Connection) -> list:
    """experiment_id + experiment_root for every not_started, control='run' experiment,
    priority order -- the pure-DB half of next_experiment_to_start, dispatched
    as one round trip; the per-candidate pause check (which needs a
    local filesystem look at each candidate's OWN experiment_root) happens
    afterwards, one candidate at a time, always on the calling machine."""
    rows = conn.execute(
        "SELECT experiment_id, experiment_root FROM experiments WHERE status = 'not_started' AND control = 'run' "
        "ORDER BY priority DESC, experiment_id"
    ).fetchall()
    return [dict(r) for r in rows]


def next_experiment_to_start(conn) -> Optional[str]:
    """Highest-priority experiment_id that hasn't started yet and isn't paused --
    what a run_chunk.slurm job chain should pick up next once its current
    experiment reaches stop_date. None if the queue is empty. Always called by
    whichever machine is about to actually pick up the next experiment (the
    production machine), so per-candidate PAUSE-file checks (via
    is_paused, see there) correctly use ITS OWN filesystem view of each
    experiment_root."""
    if Path(pause_all_sentinel_path(conn)).exists():
        return None
    for row in _not_started_candidates(conn):
        if not is_paused(conn, row["experiment_id"], row["experiment_root"]):
            return row["experiment_id"]
    return None


@_rpc_or_local
def set_chunk_settings(
    conn: sqlite3.Connection, experiment_id: str, *, chunk_kind: Optional[str] = None,
    chunk_multiplier: Optional[int] = None, user: Optional[str] = None,
) -> None:
    """Change chunk size for the REMAINING (not-yet-run) part of an experiment.
    Already-completed chunks and their date-based restart filenames are
    untouched -- the next chunk simply picks up the new size."""
    old = get_experiment(conn, experiment_id)
    fields, params = [], []
    if chunk_kind is not None:
        fields.append("chunk_kind = ?"); params.append(chunk_kind)
    if chunk_multiplier is not None:
        fields.append("chunk_multiplier = ?"); params.append(chunk_multiplier)
    if not fields:
        return
    fields.append("updated_at = ?"); params.append(_now())
    params.append(experiment_id)
    conn.execute(f"UPDATE experiments SET {', '.join(fields)} WHERE experiment_id = ?", params)
    if old is not None:
        changes = []
        if chunk_kind is not None and chunk_kind != old["chunk_kind"]:
            changes.append(f"chunk_kind: {old['chunk_kind']} -> {chunk_kind}")
        if chunk_multiplier is not None and chunk_multiplier != old["chunk_multiplier"]:
            changes.append(f"chunk_multiplier: {old['chunk_multiplier']} -> {chunk_multiplier}")
        if changes:
            _log_history(conn, experiment_id, "chunk_settings_changed", "; ".join(changes), user=user)
    conn.commit()


@_rpc_or_local
def set_control(conn: sqlite3.Connection, experiment_id: str, control: str, user: Optional[str] = None) -> None:
    if control not in EXPERIMENT_CONTROLS:
        raise ValueError(f"control must be one of {EXPERIMENT_CONTROLS}, got {control!r}")
    conn.execute(
        "UPDATE experiments SET control = ?, updated_at = ? WHERE experiment_id = ?",
        (control, _now(), experiment_id),
    )
    # "pause_requested"/"run" are what oceanicu_experiments.py pause/resume
    # actually set -- named for what the user DID, not the raw column
    # value, so the history reads like an audit trail rather than a
    # column dump. "paused" is only ever set some other way (e.g. a
    # future direct API caller), kept as its own event for that case.
    event = {"pause_requested": "pause_requested", "run": "resumed", "paused": "paused"}[control]
    _log_history(conn, experiment_id, event, user=user)
    conn.commit()
    # Real, pre-existing inconsistency found 2026-08-28 while adding this
    # history log: neither this function nor chunk_runner.py's own
    # "already paused, not starting a new chunk" bail-out ever called
    # recompute_experiment_status, so the `status` column could lag `control`
    # indefinitely (is_paused() -- the check everything else actually
    # uses -- was always correct; only the displayed `status` enum was
    # stale). Fixed here since it's a one-line, clearly-safe addition
    # exactly where the inconsistency lives, not a separate unrelated
    # change.
    recompute_experiment_status(conn, experiment_id)


# ---------------------------------------------------------------------------
# chunks table
# ---------------------------------------------------------------------------

@_rpc_or_local
def next_chunk_index(conn: sqlite3.Connection, experiment_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(chunk_index), -1) + 1 AS n FROM chunks WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchone()
    return int(row["n"])


@_rpc_or_local
def next_chunk_start(conn: sqlite3.Connection, experiment_id: str, initial_date: str) -> str:
    """Resume point: the stop of the last chunk on record, or the experiment's
    own initial_date if nothing has run yet. This is the ONLY source of
    truth for 'what's next' -- no separately-stored plan to drift from
    reality."""
    row = conn.execute(
        "SELECT MAX(stop) AS s FROM chunks WHERE experiment_id = ? AND status = 'done'",
        (experiment_id,),
    ).fetchone()
    return row["s"] if row and row["s"] else initial_date


@_rpc_or_local
def start_chunk(
    conn: sqlite3.Connection, *, experiment_id: str, chunk_index: int, start: str, stop: str,
    chunk_dir: str, load_restart: Optional[str], save_restart: str,
    slurm_job_id: Optional[str] = None, user: Optional[str] = None,
    script_sha256: Optional[str] = None, config_sha256: Optional[str] = None,
    submitted_host: Optional[str] = None,
) -> None:
    now = _now()

    # Compare against the most recent chunk still ON RECORD for this experiment
    # (BEFORE inserting this chunk's own row) -- catches a real, verifiable
    # edit to the driver script/config between attempts (a chunk blows up,
    # someone fixes a bug in the script, `rerun --from-current`, this
    # chunk_index runs again) without relying on anyone remembering to
    # mention it. Still correct across a rerun even though rerun_from
    # deletes the failed attempt's own row first: comparing against
    # whatever chunk IS still there (the last one that actually succeeded)
    # answers the same real question -- "did the script change since the
    # last chunk that ran" -- either way. None for the very first chunk of
    # an experiment (nothing to compare against yet).
    prev = conn.execute(
        "SELECT script_sha256, config_sha256 FROM chunks WHERE experiment_id = ? "
        "ORDER BY chunk_index DESC LIMIT 1",
        (experiment_id,),
    ).fetchone()
    if prev is not None:
        if script_sha256 and prev["script_sha256"] and script_sha256 != prev["script_sha256"]:
            _log_history(
                conn, experiment_id, "script_changed",
                f"{prev['script_sha256'][:12]} -> {script_sha256[:12]} (before chunk {chunk_index})",
                user=user,
            )
        if config_sha256 and prev["config_sha256"] and config_sha256 != prev["config_sha256"]:
            _log_history(
                conn, experiment_id, "config_changed",
                f"{prev['config_sha256'][:12]} -> {config_sha256[:12]} (before chunk {chunk_index})",
                user=user,
            )

    conn.execute(
        """INSERT INTO chunks (experiment_id, chunk_index, start, stop, chunk_dir,
               load_restart, save_restart, slurm_job_id, submit_time,
               start_time, status, script_sha256, config_sha256, submitted_host)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)""",
        (experiment_id, chunk_index, start, stop, chunk_dir, load_restart, save_restart,
         slurm_job_id, now, now, script_sha256, config_sha256, submitted_host),
    )
    conn.execute(
        "UPDATE experiments SET status = 'in_progress', updated_at = ? WHERE experiment_id = ? "
        "AND status != 'in_progress'",
        (now, experiment_id),
    )
    detail = f"chunk {chunk_index}: {start} -> {stop}"
    if load_restart:
        detail += f" (load_restart={load_restart})"
    _log_history(conn, experiment_id, "chunk_started", detail, user=user)
    conn.commit()


@_rpc_or_local
def finish_chunk(
    conn: sqlite3.Connection, *, experiment_id: str, chunk_index: int, exit_code: int,
    nan_detected: bool = False, user: Optional[str] = None,
) -> None:
    # status reflects only whether the chunk's process itself completed --
    # nan_detected is tracked as an independent quality flag (see
    # recompute_experiment_status), not folded into status. In practice a live
    # NaN detection also scancels the job (so exit_code != 0 anyway), but
    # a chunk that completes cleanly (exit 0) and is flagged nan_detected
    # by some other check afterwards should still count as reached/done,
    # landing the RUN in complete_with_warnings rather than failed.
    status = "done" if exit_code == 0 else "failed"
    conn.execute(
        """UPDATE chunks SET end_time = ?, exit_code = ?, nan_detected = ?, status = ?
           WHERE experiment_id = ? AND chunk_index = ?""",
        (_now(), exit_code, int(nan_detected), status, experiment_id, chunk_index),
    )
    event = "chunk_finished" if exit_code == 0 else "chunk_failed"
    detail = f"chunk {chunk_index} exit_code={exit_code}" + (" nan_detected" if nan_detected else "")
    _log_history(conn, experiment_id, event, detail, user=user)
    conn.commit()
    new_status = recompute_experiment_status(conn, experiment_id)
    # "a full simulation stops" (user, 2026-08-28): a terminal status --
    # reached stop_date (clean or with warnings) or failed outright --
    # gets its own top-level history entry distinct from the per-chunk
    # one above, so scanning history for "did this experiment ever finish" (or
    # "when did it finish") doesn't require re-deriving it from chunk
    # rows every time.
    if new_status in ("complete", "complete_with_warnings", "failed"):
        _log_history(conn, experiment_id, f"run_{new_status}", f"after chunk {chunk_index}", user=user)
        conn.commit()


@_rpc_or_local
def get_running_chunk(conn: sqlite3.Connection, experiment_id: str) -> Optional[sqlite3.Row]:
    """The chunk currently marked 'running' for this experiment, if any -- used
    as a lock: chunk_runner.py refuses to start a new chunk while one is
    already recorded as running (see chunk_runner.py's own liveness/
    staleness check for what happens if that recorded chunk turns out to
    be a crashed/orphaned job rather than a genuinely active one)."""
    return conn.execute(
        "SELECT * FROM chunks WHERE experiment_id = ? AND status = 'running' "
        "ORDER BY chunk_index DESC LIMIT 1",
        (experiment_id,),
    ).fetchone()


@_rpc_or_local
def get_done_chunk_restart(conn: sqlite3.Connection, experiment_id: str, chunk_index: int) -> Optional[str]:
    """save_restart path of a specific completed chunk -- what the NEXT
    chunk's --load-restart should point at. Exists as its own named,
    dispatchable function (rather than chunk_runner.py running this
    query directly against conn) specifically so it keeps working when
    conn is a RemoteConn: raw ad-hoc SQL outside this module has no way
    to cross the relay, only named functions do."""
    row = conn.execute(
        "SELECT save_restart FROM chunks WHERE experiment_id = ? AND chunk_index = ? AND status = 'done'",
        (experiment_id, chunk_index),
    ).fetchone()
    return row["save_restart"] if row else None


@_rpc_or_local
def list_chunks(conn: sqlite3.Connection, experiment_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chunks WHERE experiment_id = ? ORDER BY chunk_index", (experiment_id,)
    ).fetchall()


@_rpc_or_local
def rerun_from(
    conn: sqlite3.Connection, experiment_id: str, *, chunk_index: Optional[int] = None,
    user: Optional[str] = None, note: Optional[str] = None,
) -> int:
    """Rewind an experiment's tracked history so its NEXT execution redoes chunk
    *chunk_index* onward (dropping any record of it and everything after).
    Chunks before it are untouched, so the redo naturally resumes from the
    same load_restart as before -- next_chunk_start/next_chunk_index just
    read whatever's left. Three cases, all this one primitive:

      chunk_index=None  "from the present chunk" -- redo the last chunk on
                         record (whether it failed or you just want it
                         redone), keep everything before it.
      chunk_index=N      "from a set chunk" -- redo chunk N onward.
      chunk_index=0      "from scratch" -- drops every chunk; the next experiment
                         starts at initial_date with no load_restart.

    Does NOT touch files on disk (no chunk_dir is deleted) -- run_chunks.py
    archives a pre-existing chunk_dir aside before reusing it, so a
    previous attempt's logs/output are never silently overwritten.
    """
    if chunk_index is None:
        row = conn.execute(
            "SELECT MAX(chunk_index) AS n FROM chunks WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        if row is None or row["n"] is None:
            return 0  # nothing has run yet, nothing to rewind
        chunk_index = int(row["n"])

    # Capture the hash of the chunk actually being redone BEFORE dropping
    # it: start_chunk's own script_changed/config_changed detection
    # compares the NEXT chunk against whatever's still in the chunks
    # table, but this DELETE is about to remove the very row that would
    # otherwise be compared against -- most visible when chunk_index is
    # the experiment's first chunk (nothing precedes it to fall back to), where
    # that automatic detection would otherwise have nothing to compare
    # against at all. Embedding it here means the information is never
    # lost even in that case -- just read from the rerun event's own text
    # instead of a separate script_changed event.
    dropped = conn.execute(
        "SELECT script_sha256, config_sha256 FROM chunks WHERE experiment_id = ? AND chunk_index = ?",
        (experiment_id, chunk_index),
    ).fetchone()

    cur = conn.execute(
        "DELETE FROM chunks WHERE experiment_id = ? AND chunk_index >= ?", (experiment_id, chunk_index)
    )
    n_dropped = cur.rowcount
    conn.execute(
        "UPDATE experiments SET control = 'run', updated_at = ? WHERE experiment_id = ?",
        (_now(), experiment_id),
    )
    detail = f"dropped {n_dropped} chunk(s) from index {chunk_index} onward"
    if dropped is not None and (dropped["script_sha256"] or dropped["config_sha256"]):
        detail += (f" (chunk {chunk_index} was script={(dropped['script_sha256'] or '?')[:12]} "
                   f"config={(dropped['config_sha256'] or '?')[:12]})")
    if note:
        detail += f" -- {note}"
    _log_history(conn, experiment_id, "rerun", detail, user=user)
    conn.commit()
    recompute_experiment_status(conn, experiment_id)
    return n_dropped


@_rpc_or_local
def recompute_experiment_status(conn: sqlite3.Connection, experiment_id: str) -> str:
    """Derive the experiment-level status from its chunk history -- 'complete' is
    reserved for a genuinely clean finish (see module docstring); an experiment
    that reached its stop_date via a retried or NaN-recovered chunk lands
    in 'complete_with_warnings' instead, so a clean pass is visible at a
    glance without reading chunk history."""
    experiment = get_experiment(conn, experiment_id)
    if experiment is None:
        raise KeyError(f"no such experiment_id: {experiment_id!r}")
    chunks = list_chunks(conn, experiment_id)
    now = _now()

    if not chunks:
        new_status = "not_started"
    elif any(c["status"] == "running" for c in chunks):
        new_status = "in_progress"
    elif any(c["status"] == "failed" for c in chunks) and chunks[-1]["status"] == "failed":
        new_status = "failed"
    else:
        done = [c for c in chunks if c["status"] == "done"]
        reached_end = bool(done) and done[-1]["stop"] >= experiment["stop_date"]
        any_warning = any(c["status"] == "failed" or c["nan_detected"] for c in chunks)
        if reached_end and not any_warning:
            new_status = "complete"
        elif reached_end:
            new_status = "complete_with_warnings"
        elif experiment["control"] in ("paused", "pause_requested"):
            new_status = "paused"
        else:
            new_status = "in_progress"

    conn.execute(
        "UPDATE experiments SET status = ?, updated_at = ? WHERE experiment_id = ?",
        (new_status, now, experiment_id),
    )
    conn.commit()
    return new_status
