#!/usr/bin/env python
"""Extended-dry-run over all model x scenario x fabm-variant combos.

Generates each combo's script locally (pygetm env), stages it + the right
fabm.yaml on the target host, runs chunk_runner.py --extended-dry-run
there, prints a one-line-per-combo summary. Also writes a full report per
combo (<tag>_report.txt) and a combined summary.txt, both left in BB_DIR
alongside the staged inputs so a run leaves a persistent record.

Target host defaults to bb-server1 (today's known-good paths below), but
every host-side path is a CLI override -- so once data is mirrored onto
the HPC (scylla), the same script can target it with
--host scylla --data-roots-file ... --running-dir ... --fabm-src-dir ...
without editing this file. Pass --host "$(hostname -s)" (or let --local
autodetect it) to run entirely on the current machine: staging then
happens via cp instead of scp, and the dry-run runs via a plain subprocess
instead of ssh.

Generation (--dump-python) needs a pygetm-config environment, which the
check/target host may not have (running/check_inputs.py deliberately
doesn't import pygetm/pygetm-config at all -- see its own docstring).
--gen-host relays that ONE step over ssh/scp to a machine that does have
it (defaults to "", meaning generate locally, today's behavior).

Checked directly 2026-09-24: this does NOT yet make bb-server1 usable as
--gen-host for a scylla-orchestrated run -- ssh reachability is right
(scylla -> bb-server1 works; the reverse doesn't), but pygetm_config is
missing from EVERY one of bb-server1's 10 conda envs (copernicusmarine,
eat, fabmos, ocean-plot, ocean-stack, oceanval, parsac, pygetm,
pygetm-adaptive, stats -- checked each directly), including the "pygetm"
one this script's own PYGETM_PY default points at. --gen-host's ssh/scp
relay mechanics are real and exercised (tested against bb-server1 as
both --gen-host and --host at once), but bb-server1 needs a pygetm-config
install (see pygetm-config's own setup docs) before it can actually BE a
--gen-host for anything. Until then, orca (where pygetm-config IS
installed) stays the only real generation host.
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

MODELS = ["CNRM-ESM2-1", "GFDL-ESM4", "MPI-ESM1-2-HR"]
SCENARIOS = ["ssp126", "ssp370"]
FABM_VARIANTS = {"ersem": "fabm_ersem.yaml", "mizer": "fabm_mizer.yaml"}

# bb-server1's known-good paths -- the only host confirmed today.
DEFAULT_HOST = "bb-server1"
DEFAULT_DIR = "/tmp/nse_all_combos_dryrun"
DEFAULT_DATA_ROOTS = "/data/OceanICU/oceanicu_3d/experiments/NSe/bb-server1_data_roots.yaml"
DEFAULT_RUNNING = "~/source/repos/OceanICU/oceanicu_3d/running"
DEFAULT_FABM_SRC = "/data/OceanICU/oceanicu_3d/experiments/NSe"
DEFAULT_GEN_DIR = "/tmp/nse_all_combos_dryrun_gen"
DEFAULT_GEN_REPO = "~/source/repos/OceanICU/oceanicu_3d"
# NOT bb-server1-ready as-is: this path exists there (it's a real conda
# env) but pygetm_config isn't installed in it, or in any of bb-server1's
# other 9 envs (checked directly 2026-09-24) -- see this module's own
# docstring. Left as the orca-side default since --gen-host defaults to
# "" (no relay) and a caller pointing --gen-host elsewhere needs to pass
# --gen-pygetm-py for that host explicitly anyway.
DEFAULT_GEN_PYGETM_PY = PYGETM_PY


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
                    help="running/ dir (holding bin/chunk-runner) on the target host")
    p.add_argument("--fabm-src-dir", default=DEFAULT_FABM_SRC,
                    help="dir holding fabm_ersem.yaml/fabm_mizer.yaml on the target host")
    p.add_argument("--gen-host", default="",
                    help="run --dump-python generation over ssh on this host instead of "
                         "locally (default: generate locally, on whichever machine runs "
                         "this script) -- for when --host/this machine has no pygetm-config "
                         "environment of its own")
    p.add_argument("--gen-dir", default=DEFAULT_GEN_DIR, help="scratch dir on --gen-host")
    p.add_argument("--gen-repo", default=DEFAULT_GEN_REPO,
                    help="oceanicu_3d code checkout on --gen-host (for driver/oceanicu_driver.py)")
    p.add_argument("--gen-pygetm-py", default=DEFAULT_GEN_PYGETM_PY,
                    help="pygetm-config python interpreter path on --gen-host")
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
    host, dir_, data_roots, running_dir, fabm_src = (
        args.host, args.dir, args.data_roots_file, args.running_dir, args.fabm_src_dir,
    )
    gen_host = "" if is_same_host(args.gen_host) else args.gen_host

    def run_on_host(shell_cmd: str):
        if local:
            return run(["bash", "-c", shell_cmd])
        return run(["ssh", host, shell_cmd])

    def stage_file(src: Path, dst: str):
        if local:
            return run(["cp", str(src), dst])
        return run(["scp", "-q", str(src), f"{host}:{dst}"])

    run_on_host(f"mkdir -p {dir_}")
    for name in FABM_VARIANTS.values():
        r = run_on_host(f"cp {fabm_src}/{name} {dir_}/{name}")
        if r.returncode != 0:
            print(f"FATAL: couldn't stage {name} on {host}: {r.stderr}", file=sys.stderr)
            return 1

    if gen_host:
        run(["ssh", gen_host, f"mkdir -p {args.gen_dir}"])

    tmp = Path(tempfile.mkdtemp(prefix="nse_dryrun_"))
    results = []
    for model in MODELS:
        for scenario in SCENARIOS:
            for variant, fabm_file in FABM_VARIANTS.items():
                tag = f"{model}_{scenario}_{variant}"
                print(f"--- {tag} ---")

                cfg = yaml.safe_load(BASE_CONFIG.read_text())
                set_recursive(cfg, "model", model)
                set_recursive(cfg, "scenario", scenario)
                cfg["fabm"]["ERSEM"]["file"] = fabm_file
                cfg_path = tmp / f"{tag}.yaml"
                cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))

                script_path = tmp / f"generated_{tag}.py"
                if not gen_host:
                    r = run([PYGETM_PY, str(DRIVER), str(cfg_path),
                             "--start", START, "--stop", STOP,
                             "--dump-python", str(script_path)])
                else:
                    r = run(["scp", "-q", str(cfg_path), f"{gen_host}:{args.gen_dir}/"])
                    if r.returncode == 0:
                        r = run(["ssh", gen_host,
                                 f"{args.gen_pygetm_py} {args.gen_repo}/driver/oceanicu_driver.py "
                                 f"{args.gen_dir}/{tag}.yaml --start {START} --stop {STOP} "
                                 f"--dump-python {args.gen_dir}/generated_{tag}.py"])
                    if r.returncode == 0:
                        r = run(["scp", "-q", f"{gen_host}:{args.gen_dir}/generated_{tag}*", f"{tmp}/"])
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
