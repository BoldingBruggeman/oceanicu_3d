#!/usr/bin/env python
"""run_tracking_server.py -- RPC entrypoint for remote registry access.

Deployed once to the relay machine (alongside run_tracking.py -- both
files, nothing else needed) at whatever directory OCEANICU_RELAY_DIR
points callers at. Invoked over SSH, once per call, by RemoteConn.call()
in run_tracking.py -- never run directly by a human.

Reads ONE JSON request from stdin:
    {"db": "/abs/path/to/registry.sqlite", "func": "name", "kwargs": {...}}
opens that DB locally (real sqlite3, run_tracking.connect()'s normal
path -- this file only ever runs ON the relay, so "locally" here really
does mean the relay's own filesystem), calls run_tracking.<func>(conn,
**kwargs), and writes ONE JSON response line to stdout:
    {"ok": true, "result": ...}
    {"ok": false, "error": "..."}

*func* must name a function in run_tracking's public API -- arbitrary
code is never accepted, only a lookup by name via getattr.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import run_tracking as rt

# Only functions actually decorated with @_rpc_or_local are ever
# dispatchable -- driven by that marker attribute, not a naming
# convention (a leading underscore means "internal to this module", NOT
# "not RPC-safe"; _control_or_pause_all and _not_started_candidates are
# both decorated and both legitimately dispatched this way, from inside
# is_paused/next_run_to_start). getattr() alone would happily resolve
# dunders, imported modules (os, sqlite3, ...), or arbitrary callables
# too, and this is fed by whatever a caller sends over SSH.
_ALLOWED = {
    name for name in dir(rt)
    if getattr(getattr(rt, name), "_is_rpc_dispatchable", False)
}


def _jsonable(value):
    if isinstance(value, sqlite3.Row):
        return dict(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        db = request["db"]
        func_name = request["func"]
        kwargs = request.get("kwargs", {})
    except Exception as exc:  # noqa: BLE001 -- must always emit a JSON response, not a traceback
        print(json.dumps({"ok": False, "error": f"malformed request: {exc}"}))
        return 1

    if func_name not in _ALLOWED:
        print(json.dumps({"ok": False, "error": f"{func_name!r} is not a dispatchable function"}))
        return 1

    try:
        fn = getattr(rt, func_name)
        with rt.connect(db) as conn:
            result = fn(conn, **kwargs)
        print(json.dumps({"ok": True, "result": _jsonable(result)}))
        return 0
    except Exception as exc:  # noqa: BLE001 -- ditto
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
