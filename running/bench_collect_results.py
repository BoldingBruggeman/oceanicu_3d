#!/usr/bin/env python3
"""Aggregate bench_concurrent_reads_task.py's per-task JSON result files
(written under <out_dir>/N<n>/result_<label>_<rank>.json) into the same
mean/max/min table used in docs/chunk-pace-analysis.md.

Usage: python3 bench_collect_results.py <out_dir>
"""
import argparse
import glob
import json
import os

import numpy as np


def main():
    p = argparse.ArgumentParser()
    p.add_argument("out_dir")
    args = p.parse_args()

    for n_dir in sorted(glob.glob(os.path.join(args.out_dir, "N*"))):
        n = os.path.basename(n_dir).lstrip("N")
        for label in ("OLD", "NEW"):
            paths = sorted(glob.glob(os.path.join(n_dir, f"result_{label}_*.json")))
            if not paths:
                continue
            times = []
            for path in paths:
                with open(path) as f:
                    times.append(json.load(f)["ms_per_read"])
            times = np.array(times)
            print(f"{label:4s} N={n:>4s}  tasks_reporting={len(times):4d}  "
                  f"mean={times.mean():8.2f}ms  max={times.max():8.2f}ms  "
                  f"min={times.min():8.2f}ms")


if __name__ == "__main__":
    main()
