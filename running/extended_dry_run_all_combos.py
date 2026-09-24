#!/usr/bin/env python
"""Extended-dry-run over all model x scenario x fabm-variant combos.

Generates each combo's script locally (pygetm env), copies it + the right
fabm.yaml to bb-server1, runs chunk_runner.py --extended-dry-run there,
prints a one-line-per-combo summary. Also writes a full report per combo
(<tag>_report.txt) and a combined summary.txt, both left in BB_DIR
alongside the staged inputs so a run leaves a persistent record.
"""
from __future__ import annotations

import collections
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

BB = "bb-server1"
BB_DIR = "/tmp/nse_all_combos_dryrun"
BB_DATA_ROOTS = "/data/OceanICU/oceanicu_3d/experiments/NSe/bb-server1_data_roots.yaml"
BB_RUNNING = "~/source/repos/OceanICU/oceanicu_3d/running"
BB_FABM_SRC = "/data/OceanICU/oceanicu_3d/experiments/NSe"


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


def dedup_fails(fails: list) -> list:
    """[FAIL] lines repeat verbatim once per missing year-file -- collapse
    identical lines into one with a count, e.g. 'x85', instead of printing
    the same line 85 times."""
    counts = collections.Counter(fails)
    return [f"{line}  (x{n})" if n > 1 else line for line, n in counts.items()]


def main() -> int:
    run(["ssh", BB, f"mkdir -p {BB_DIR}"])
    for name in FABM_VARIANTS.values():
        r = run(["ssh", BB, f"cp {BB_FABM_SRC}/{name} {BB_DIR}/{name}"])
        if r.returncode != 0:
            print(f"FATAL: couldn't stage {name} on {BB}: {r.stderr}", file=sys.stderr)
            return 1

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
                r = run([PYGETM_PY, str(DRIVER), str(cfg_path),
                         "--start", START, "--stop", STOP,
                         "--dump-python", str(script_path)])
                if r.returncode != 0:
                    results.append((tag, "GENERATE-FAIL", r.stderr.strip()[-200:]))
                    continue

                for f in tmp.glob(f"generated_{tag}*"):
                    run(["scp", "-q", str(f), f"{BB}:{BB_DIR}/"])

                r = run(["ssh", BB,
                          f"cd {BB_DIR} && {BB_RUNNING}/bin/chunk-runner "
                          f"--extended-dry-run --script generated_{tag}.py "
                          f"--start {START} --stop {STOP} "
                          f"--data-roots-file {BB_DATA_ROOTS}"])
                out = r.stdout + r.stderr
                fails = dedup_fails([l for l in out.splitlines() if l.startswith("[FAIL]")])
                status = "OK" if r.returncode == 0 else f"{len(fails)} FAIL KIND(S)"
                results.append((tag, status, fails))
                print(out.strip().splitlines()[-1] if out.strip() else "(no output)")

                report_path = tmp / f"{tag}_report.txt"
                report_path.write_text(out)
                run(["scp", "-q", str(report_path), f"{BB}:{BB_DIR}/{tag}_report.txt"])

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
    run(["scp", "-q", str(summary_path), f"{BB}:{BB_DIR}/summary.txt"])

    return 0 if all(s == "OK" for _, s, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
