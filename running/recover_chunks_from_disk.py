#!/usr/bin/env python3
"""recover_chunks_from_disk.py -- rebuild chunk history rows for an
experiment whose registry chunk records were dropped by accident (e.g.
`oceanicu-experiments reset` or `rerun --from-scratch` run against the
wrong experiment, or one chunk too many), while the real chunk_dir
output on disk survived untouched -- rerun_from/reset (the primitive
behind both) never deletes files, only registry rows (see its own
docstring in experiment_tracking.py).

Reconstructs chunks 0 .. --up-to-chunk - 1 as 'done', one at a time in
order (each chunk's load_restart is the previous one's own save_restart,
same as a real run), by:
  1. Parsing start/stop straight from each chunk_dir's own name
     (NNN_STARTDATE_ENDDATE -- ground truth, not recomputed from
     initial_date/chunk_kind/chunk_multiplier, so a chunk-size change
     made after these chunks ran doesn't matter).
  2. Computing the restart file chunk_runner.py itself would have
     written (restart_<config-stem>_<stop>.nc, see its own save_restart
     construction) and REFUSING to record a chunk as done unless that
     exact file actually exists -- never fabricates a done row for a
     chunk that didn't really produce usable restart output.

Chunk --up-to-chunk itself (and anything after) is left completely
untouched -- its own chunk_dir, if a previous failed attempt is still
sitting there from before the accidental reset, gets archived aside as
usual the next time chunk_runner.py actually runs it (same as any other
retry). Idempotent: chunk indices already present in the registry are
skipped, not re-inserted or errored on -- safe to re-run after fixing
whatever stopped an earlier attempt partway through.

Run on whichever machine can see the real chunk_dir files (normally the
HPC), with OCEANICU_EXPERIMENT_ROOT_BASE set the same way chunk_runner.py
itself needs it, pointed at the real registry the normal way (--db, or
OCEANICU_EXPERIMENT_DB already set):

    python recover_chunks_from_disk.py \\
        --experiment-id NSe/CMIP6/GFDL-ESM4/ssp126/run01 --up-to-chunk 8

Then `oceanicu-experiments submit-chunk --experiment-id ...` (or the
routine self-resubmission chain) picks up chunk 8 next, same as if it
had never been reset.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import experiment_tracking as rt

_CHUNK_DIR_RE = re.compile(r"^(?P<num>\d{3})_(?P<start>\d{8})_(?P<end>\d{8})$")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--up-to-chunk", type=int, required=True, metavar="N",
                    help="replay chunks 0..N-1 as done; chunk N itself is left untouched")
    p.add_argument("--db", default=None, help="override the SQLite registry path")
    p.add_argument("--dry-run", action="store_true",
                    help="print what would be recorded, without writing anything")
    args = p.parse_args()

    with rt.connect(args.db) as conn:
        experiment = rt.get_experiment(conn, args.experiment_id)
        if experiment is None:
            print(f"ERROR: no such experiment_id: {args.experiment_id!r}", file=sys.stderr)
            return 1

        experiment_root = Path(rt.resolve_experiment_root(experiment["experiment_root"]))
        setup_name = Path(experiment["config"]).stem
        existing = {row["chunk_index"] for row in rt.list_chunks(conn, args.experiment_id)}

        chunks_on_disk: dict[int, tuple[str, str, Path]] = {}
        for child in sorted(experiment_root.iterdir()):
            if not child.is_dir():
                continue
            m = _CHUNK_DIR_RE.match(child.name)
            if not m:
                continue
            chunks_on_disk[int(m["num"])] = (m["start"], m["end"], child)

        missing = [i for i in range(args.up_to_chunk) if i not in chunks_on_disk]
        if missing:
            print(f"ERROR: no chunk_dir on disk for chunk index(es) {missing} under {experiment_root} "
                  f"-- refusing to fabricate a 'done' row for a chunk that was never actually run.",
                  file=sys.stderr)
            return 1

        user = rt._current_user()
        load_restart = None
        for i in range(args.up_to_chunk):
            if i in existing:
                print(f"chunk {i}: already in the registry -- skipping")
                row = rt.get_chunk(conn, args.experiment_id, i)
                if row is None:
                    print(f"ERROR: chunk {i} was just listed as existing but get_chunk found nothing "
                          f"-- registry changed underneath this script, retry.", file=sys.stderr)
                    return 1
                load_restart = row["save_restart"]
                continue

            start_ymd, end_ymd, chunk_dir = chunks_on_disk[i]
            start = f"{start_ymd[:4]}-{start_ymd[4:6]}-{start_ymd[6:]}"
            stop = f"{end_ymd[:4]}-{end_ymd[4:6]}-{end_ymd[6:]}"
            save_restart = str(chunk_dir / f"restart_{setup_name}_{end_ymd}.nc")

            if not Path(save_restart).is_file():
                print(f"ERROR: chunk {i} ({chunk_dir.name}) has no restart file at {save_restart} "
                      f"-- refusing to record it as done. Fix or remove this chunk_dir, or lower "
                      f"--up-to-chunk, then retry.", file=sys.stderr)
                return 1

            print(f"chunk {i}: {start} -> {stop}  load_restart={load_restart}  save_restart={save_restart}")
            if not args.dry_run:
                rt.start_chunk(
                    conn, experiment_id=args.experiment_id, chunk_index=i,
                    start=start, stop=stop, chunk_dir=str(chunk_dir),
                    load_restart=load_restart, save_restart=save_restart, user=user,
                )
                rt.finish_chunk(conn, experiment_id=args.experiment_id, chunk_index=i,
                                 exit_code=0, user=user)
            load_restart = save_restart

    if args.dry_run:
        print(f"\n[dry-run] would record chunks 0..{args.up_to_chunk - 1} as done; nothing written.")
    else:
        print(f"\nDone -- chunks 0..{args.up_to_chunk - 1} recorded as done. "
              f"Next submission starts chunk {args.up_to_chunk}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
