#!/usr/bin/env python
"""Extended-dry-run over all model x scenario x fabm-variant combos.

Generates each combo's script locally (pygetm env), stages it on the
target host, runs chunk_runner.py --extended-dry-run there, prints a
one-line-per-combo summary. Also writes a full report per combo
(<tag>_report.txt) and a combined summary.txt, both left in BB_DIR
alongside the staged inputs so a run leaves a persistent record.

fabm_ersem.yaml/fabm_mizer.yaml are read straight from the target host's
own ${FABM_ERSEM_FOLDER} (via --data-roots-file) -- no staging needed
(fixed 2026-09-24: check_inputs.py's mizer/fish-pressure check used to
resolve fabm.ERSEM.file as a bare, cwd-relative filename, requiring a
copy into the staging dir; it now joins it with the real, already-
resolved fabm folder instead, same fix applied to oceanicu_driver.py's
own nclass lookup).

Target host defaults to bb-server1 (today's known-good paths below), but
every host-side path is a CLI override -- so once data is mirrored onto
the HPC (scylla), the same script can target it with
--host scylla --data-roots-file ... without editing this file. Pass
--host "$(hostname -s)" (or let --local autodetect it) to run entirely
on the current machine: staging then happens via cp instead of scp, and
the dry-run runs via a plain subprocess instead of ssh. --running-dir
defaults to this script's own directory (Path(__file__).parent) --
override only if the target's running/ checkout sits somewhere else.

Generation (--dump-python) needs a pygetm-config environment, which
bb-server1/scylla don't have and, per the user (2026-09-24), never will --
pygetm-config only ever runs on orca. The real pickup chain instead: orca
generates + stages onto bb-server1 (today's default --host behavior,
unchanged, no flags needed), and scylla -- confirmed able to ssh to
bb-server1, not the reverse -- separately PULLS those already-generated
files from there with --fetch-from bb-server1, then runs the check
locally (--host "$(hostname -s)" --local) against its own local data,
once that's in place. --fetch-from is a plain pull (scp from
--fetch-dir on that host); it does NOT run pygetm-config anywhere.
"""
from __future__ import annotations

import argparse
import collections
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
BASE_CONFIG = REPO / "NSe/config/nse_cmip6_fabm_dryrun.yaml"
PYGETM_PY = "/home/kb/miniconda3/envs/pygetm/bin/python"
DRIVER = REPO / "driver/oceanicu_driver.py"
START, STOP = "2015-01-01T00:00:00", "2099-12-31T00:00:00"

# CNRM-ESM2-1 dropped (2026-09-24, per user): its net_sw/net_lw
# BiasCorrected files will never be complete, so it always fails this
# check for a reason unrelated to anything this script tests.
MODELS = ["GFDL-ESM4", "MPI-ESM1-2-HR"]
SCENARIOS = ["ssp126", "ssp370"]
FABM_VARIANTS = {"ersem": "fabm_ersem.yaml", "mizer": "fabm_mizer.yaml"}

# bb-server1's known-good paths -- the only host confirmed today.
DEFAULT_HOST = "bb-server1"
DEFAULT_DIR = "/tmp/nse_all_combos_dryrun"
DEFAULT_DATA_ROOTS = "/data/OceanICU/oceanicu_3d/experiments/NSe/bb-server1_data_roots.yaml"
# Not hardcoded: the checkout path is confirmed identical on every real
# machine this has run on (orca, bb-server1) -- deriving it from where
# THIS script's own file lives keeps that in sync automatically, and is
# exactly right for --local (the target IS this machine).
DEFAULT_RUNNING = str(REPO / "running")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=DEFAULT_HOST,
                    help=f"target host to stage/run on (default: {DEFAULT_HOST})")
    p.add_argument("--local", action="store_true",
                    help="run directly on this machine (no ssh/scp) instead of over --host; "
                         "implied automatically if --host matches this machine's own hostname")
    p.add_argument("--dir", default=DEFAULT_DIR, help="staging dir on the target host")
    p.add_argument("--data-roots-file", default=DEFAULT_DATA_ROOTS,
                    help="data-roots yaml on the target host")
    p.add_argument("--running-dir", default=DEFAULT_RUNNING,
                    help="running/ dir (holding bin/chunk-runner) on the target host "
                         f"(default: {DEFAULT_RUNNING}, this script's own directory)")
    p.add_argument("--fetch-from", default="",
                    help="instead of generating locally, pull already-generated "
                         "generated_<tag>* files from --fetch-dir on this host (e.g. "
                         "bb-server1, once orca has generated+staged them there) -- "
                         "for running this script's check step on a host with no "
                         "pygetm-config of its own")
    p.add_argument("--fetch-dir", default=DEFAULT_DIR,
                    help="dir on --fetch-from holding the already-generated files")
    return p.parse_args()


def set_recursive(obj, key, value):
    if isinstance(obj, dict):
        for k in obj:
            if k == key:
                obj[k] = value
            else:
                set_recursive(obj[k], key, value)
    elif isinstance(obj, list):
        for item in obj:
            set_recursive(item, key, value)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def is_same_host(host: str) -> bool:
    me = socket.gethostname()
    return host in (me, me.split(".")[0])


def dedup_fails(fails: list) -> list:
    """[FAIL] lines repeat verbatim once per missing year-file -- collapse
    identical lines into one with a count, e.g. 'x85', instead of printing
    the same line 85 times."""
    counts = collections.Counter(fails)
    return [f"{line}  (x{n})" if n > 1 else line for line, n in counts.items()]


def main() -> int:
    args = parse_args()
    local = args.local or is_same_host(args.host)
    host, dir_, data_roots, running_dir = (
        args.host, args.dir, args.data_roots_file, args.running_dir,
    )
    fetch_from = args.fetch_from

    def run_on_host(shell_cmd: str):
        if local:
            return run(["bash", "-c", shell_cmd])
        return run(["ssh", host, shell_cmd])

    def stage_file(src: Path, dst: str):
        if local:
            return run(["cp", str(src), dst])
        return run(["scp", "-q", str(src), f"{host}:{dst}"])

    run_on_host(f"mkdir -p {dir_}")

    tmp = Path(tempfile.mkdtemp(prefix="nse_dryrun_"))
    results = []
    for model in MODELS:
        for scenario in SCENARIOS:
            for variant, fabm_file in FABM_VARIANTS.items():
                tag = f"{model}_{scenario}_{variant}"
                print(f"--- {tag} ---")

                if fetch_from:
                    r = run(["scp", "-q", f"{fetch_from}:{args.fetch_dir}/generated_{tag}*", f"{tmp}/"])
                    if r.returncode != 0:
                        results.append((tag, "FETCH-FAIL", [r.stderr.strip()[-200:]]))
                        continue
                else:
                    cfg = yaml.safe_load(BASE_CONFIG.read_text())
                    set_recursive(cfg, "model", model)
                    set_recursive(cfg, "scenario", scenario)
                    cfg["fabm"]["ERSEM"]["file"] = fabm_file
                    cfg_path = tmp / f"{tag}.yaml"
                    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))

                    script_path = tmp / f"generated_{tag}.py"
                    r = run([PYGETM_PY, str(DRIVER), str(cfg_path),
                             "--start", START, "--stop", STOP,
                             "--dump-python", str(script_path)])
                    if r.returncode != 0:
                        # A single-item list, not a bare string -- the summary
                        # printer below does `for f in fails:`, which would
                        # otherwise iterate the string character-by-character.
                        results.append((tag, "GENERATE-FAIL", [r.stderr.strip()[-200:]]))
                        continue

                for f in tmp.glob(f"generated_{tag}*"):
                    stage_file(f, f"{dir_}/")

                r = run_on_host(
                    f"cd {dir_} && {running_dir}/bin/chunk-runner "
                    f"--extended-dry-run --script generated_{tag}.py "
                    f"--start {START} --stop {STOP} "
                    f"--data-roots-file {data_roots}")
                out = r.stdout + r.stderr
                fails = dedup_fails([l for l in out.splitlines() if l.startswith("[FAIL]")])
                status = "OK" if r.returncode == 0 else f"{len(fails)} FAIL KIND(S)"
                results.append((tag, status, fails))
                print(out.strip().splitlines()[-1] if out.strip() else "(no output)")

                report_path = tmp / f"{tag}_report.txt"
                report_path.write_text(out)
                stage_file(report_path, f"{dir_}/{tag}_report.txt")

    print("\n=== summary ===")
    summary_lines = []
    for tag, status, fails in results:
        summary_lines.append(f"{tag:40s} {status}")
        for f in fails:
            summary_lines.append(f"    {f}")
    summary_text = "\n".join(summary_lines) + "\n"
    print(summary_text)

    summary_path = tmp / "summary.txt"
    summary_path.write_text(summary_text)
    stage_file(summary_path, f"{dir_}/summary.txt")

    return 0 if all(s == "OK" for _, s, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
