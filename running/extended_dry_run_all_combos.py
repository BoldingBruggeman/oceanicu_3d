#!/usr/bin/env python
"""Extended-dry-run over all model x scenario x fabm-variant combos.

Generates each combo's script locally (pygetm env), stages it on the
target host, runs chunk_runner.py --extended-dry-run there, prints a
one-line-per-combo summary. Also writes a full report per combo
(<tag>_report.txt) and a combined summary.txt, both left in BB_DIR
alongside the staged inputs so a run leaves a persistent record.

fabm_ersem.yaml/fabm_mizer.yaml still get staged into --dir, though two
SEPARATE fabm.yaml lookups no longer need that (fixed 2026-09-24):
check_inputs.py's mizer/fish-pressure check and oceanicu_driver.py's own
nclass lookup used to resolve fabm.ERSEM.file as a bare, cwd-relative
filename; both now join it with the real, already-resolved fabm folder
instead. The staging copy itself stays required for a THIRD, more
fundamental reason: `simulation.fabm` (pygetm's own core schema field,
what actually opens fabm.yaml at real simulation runtime) is bare and
genuinely, deliberately cwd-relative -- the "each run/chunk keeps its
own physical copy of fabm.yaml, not a shared reference" convention
established earlier this project. Confirmed directly: removing the
staging step broke the "simulation setup / fabm_ersem.yaml" check with
exactly that missing-file error. Source for that copy is --fabm-yaml-dir
(renamed from --fabm-src-dir -- confusing name, per user) EXCEPT with
--fetch-from, where the fabm yaml files are pulled from --fetch-from's
own --fetch-dir instead (already staged there by that host's own earlier
orca-generated run) -- --fabm-yaml-dir is ignored in that mode.

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
# Full scenario period by default -- override with --start/--stop for a
# narrower window (e.g. reaching back into the pre-2015 historical period,
# or restricted to whatever BiasCorrected years are actually staged on a
# storage-limited host like scylla today). Drives BOTH generation and the
# check itself, same as a real run's own start/stop would.
DEFAULT_START, DEFAULT_STOP = "2015-01-01T00:00:00", "2099-12-31T00:00:00"

# CNRM-ESM2-1 dropped (2026-09-24, per user): its net_sw/net_lw
# BiasCorrected files will never be complete, so it always fails this
# check for a reason unrelated to anything this script tests.
MODELS = ["GFDL-ESM4", "MPI-ESM1-2-HR"]
SCENARIOS = ["ssp126", "ssp370"]
FABM_VARIANTS = {"ersem": "fabm_ersem.yaml", "mizer": "fabm_mizer.yaml"}
# gotm.yaml joins fabm_ersem.yaml/fabm_mizer.yaml here (2026-09-25, per
# user: "treat gotm.yaml as fabm.yaml") -- simulation.gotm is now also a
# bare, cwd-relative filename, so it needs the same per-run staging copy,
# from the same --fabm-yaml-dir (both sit side by side in experiments/NSe/).
STATIC_YAML_FILES = [*FABM_VARIANTS.values(), "gotm.yaml"]

# bb-server1's known-good paths -- the only host confirmed today.
DEFAULT_HOST = "bb-server1"
DEFAULT_DIR = "/tmp/nse_all_combos_dryrun"
DEFAULT_DATA_ROOTS = "/data/OceanICU/oceanicu_3d/experiments/NSe/bb-server1_data_roots.yaml"
# Not hardcoded: the checkout path is confirmed identical on every real
# machine this has run on (orca, bb-server1) -- deriving it from where
# THIS script's own file lives keeps that in sync automatically, and is
# exactly right for --local (the target IS this machine).
DEFAULT_RUNNING = str(REPO / "running")
DEFAULT_FABM_YAML_DIR = "/data/OceanICU/oceanicu_3d/experiments/NSe"


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
    p.add_argument("--fabm-yaml-dir", default=DEFAULT_FABM_YAML_DIR,
                    help="dir holding the real fabm_ersem.yaml/fabm_mizer.yaml on the target "
                         "host -- copied into --dir; simulation.fabm resolves it cwd-relative "
                         "by deliberate design, see module docstring. Ignored with --fetch-from "
                         "(fetched from there instead, since --fetch-from's own --fetch-dir "
                         "already has them staged from its own earlier run)")
    p.add_argument("--fetch-from", default="",
                    help="instead of generating locally, pull already-generated "
                         "generated_<tag>* files (and fabm_ersem.yaml/fabm_mizer.yaml) from "
                         "--fetch-dir on this host (e.g. bb-server1, once orca has "
                         "generated+staged them there) -- for running this script's check "
                         "step on a host with no pygetm-config of its own")
    p.add_argument("--fetch-dir", default=DEFAULT_DIR,
                    help="dir on --fetch-from holding the already-generated files")
    p.add_argument("--start", default=DEFAULT_START,
                    help=f"period start, ISO 8601 (default: {DEFAULT_START}) -- drives both "
                         "generation and the check; set earlier to also exercise the "
                         "pre-2015 historical splice, or narrower to match whatever "
                         "BiasCorrected years are actually staged on the target host")
    p.add_argument("--stop", default=DEFAULT_STOP,
                    help=f"period stop, ISO 8601 (default: {DEFAULT_STOP})")
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


def scan_data_roots(data_roots_file: str, run_on_host) -> list:
    """Every value in a real data_roots.yaml is an existing folder on the
    target host, EXCEPT OUTPUT_FOLDER (deliberately '.') and any other
    non-absolute value -- those are skipped, not checked. Returns
    [(VAR_NAME, path), ...] for each absolute path that isn't a real
    directory there. Catches a stale/wrong data-roots-file up front,
    before spending time generating anything against it."""
    r = run_on_host(f"cat {data_roots_file}")
    if r.returncode != 0:
        return [("(file itself)", f"{data_roots_file}: {r.stderr.strip() or 'not readable'}")]
    try:
        roots = yaml.safe_load(r.stdout) or {}
    except yaml.YAMLError as exc:
        return [("(file itself)", f"{data_roots_file}: couldn't parse as YAML ({exc})")]
    missing = []
    for name, path in roots.items():
        if not isinstance(path, str) or not path.startswith("/"):
            continue
        if run_on_host(f"test -d {path}").returncode != 0:
            missing.append((name, path))
    return missing


def main() -> int:
    args = parse_args()
    local = args.local or is_same_host(args.host)
    host, dir_, data_roots, running_dir, fabm_yaml_dir = (
        args.host, args.dir, args.data_roots_file, args.running_dir, args.fabm_yaml_dir,
    )
    fetch_from = args.fetch_from
    start, stop = args.start, args.stop

    def run_on_host(shell_cmd: str):
        if local:
            return run(["bash", "-c", shell_cmd])
        return run(["ssh", host, shell_cmd])

    def stage_file(src: Path, dst: str):
        if local:
            return run(["cp", str(src), dst])
        return run(["scp", "-q", str(src), f"{host}:{dst}"])

    missing_roots = scan_data_roots(data_roots, run_on_host)
    if missing_roots:
        print(f"FATAL: {data_roots} on {host} references {len(missing_roots)} "
              f"non-existent folder(s):", file=sys.stderr)
        for name, path in missing_roots:
            print(f"    {name}: {path}", file=sys.stderr)
        return 1

    run_on_host(f"mkdir -p {dir_}")
    tmp = Path(tempfile.mkdtemp(prefix="nse_dryrun_"))

    for name in STATIC_YAML_FILES:
        if fetch_from:
            # Already staged there by fetch_from's own earlier (orca ->
            # that host) run -- pull it down then stage onward via the
            # same helper used for generated_* files below, rather than
            # copying from a local fabm-yaml-dir this machine may not have.
            local_copy = tmp / name
            r = run(["scp", "-q", f"{fetch_from}:{args.fetch_dir}/{name}", str(local_copy)])
            if r.returncode == 0:
                r = stage_file(local_copy, f"{dir_}/{name}")
        else:
            r = run_on_host(f"cp {fabm_yaml_dir}/{name} {dir_}/{name}")
        if r.returncode != 0:
            print(f"FATAL: couldn't stage {name} on {host}: {r.stderr}", file=sys.stderr)
            return 1

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
                             "--start", start, "--stop", stop,
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
                    f"--start {start} --stop {stop} "
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
