#!/usr/bin/env python3
"""
PydanticBench -- stage 0: repository suitability gate.

Run BEFORE building anything else. Every downstream stage assumes a repo whose
suite is green, fast and deterministic; if a gate fails here, the right move is
to change repository, not to debug.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pytest_scope import ignore_args  # noqa: E402

FAIL_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)
MAX_SECONDS = 360


def run(repo: Path):
    t = time.time()
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider",
         "--tb=no", "-rf"] + ignore_args(),
        cwd=repo, capture_output=True, text=True, timeout=1800)
    return set(FAIL_RE.findall(r.stdout)), time.time() - t, r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    args = ap.parse_args()

    print("run 1 ...", flush=True)
    f1, t1, out1 = run(args.repo)
    print("run 2 ...", flush=True)
    f2, t2, _ = run(args.repo)
    print(out1.strip().splitlines()[-1])

    gates = {
        "suite_green": (len(f1) == 0, f"{len(f1)} failures"),
        "suite_fast": (t1 < MAX_SECONDS, f"{t1:.1f}s (limit {MAX_SECONDS}s)"),
        "deterministic": (f1 == f2, f"run1={len(f1)} run2={len(f2)} failures"),
    }
    ok = True
    for name, (passed, detail) in gates.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:16s} {detail}")
        ok &= passed
    print(f"\nmean suite time: {(t1 + t2) / 2:.2f}s")
    if not ok:
        sys.exit("GATE FAILED -- choose a different repository rather than debugging this one")
    print("ALL GATES PASSED")


if __name__ == "__main__":
    main()
