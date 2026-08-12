#!/usr/bin/env python3
"""
PydanticBench -- harness self-test.

Verifies the SCORER, not any model. A benchmark whose scorer has not been tested
against known inputs is just an opinion, so this asserts the four behaviours the
score is supposed to have:

    formula pure arithmetic, no container -> exact expected values
    oracle  reference patch               -> 1.00  (a correct fix scores full marks)
    noop    empty patch                   -> 0.00  (no work earns nothing)
    cheat   patch that edits tests/        -> 0.00  (gate trips, reason recorded)
    half    1 of 2 defects fixed (T2)      -> < 1.0 (never full marks for half a fix)

Note on the `half` assertion, which has been wrong TWICE:

  v1 required `0 < score < 1` per task. Wrong: a half-fix scoring exactly 0.0 is
  correct when both defects sit on the same code path.
  v2 additionally required at least one task in the sample to show partial
  credit. Also wrong, and for a subtler reason: `03c_verify_multihop.py`
  deliberately keeps only tasks where NO single half restores the fail-to-pass
  set. The stricter the multi-hop filter, the more likely every half-fix scores
  exactly 0 -- so v2 turned a sign of task quality into a test failure.

The invariant that actually belongs to the SCORER is only this: a partial fix
must never earn full marks. Partial credit itself is verified by the `formula`
control, which tests the arithmetic directly and does not depend on whether the
task set happens to admit a partial fix.

Two backends:
    --backend docker  runs inside the built image; what run.sh uses
    --backend local   runs against a local checkout; no Docker needed

Usage:
    python3 scripts/07_selftest.py --backend docker --image pydanticbench:base -n 2
    python3 scripts/07_selftest.py --backend local --repo /tmp/repo --pristine /tmp/pristine
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("scorer", HERE / "05_score.py")
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)


def build_preds(tasks, kind):
    preds = {}
    for t in tasks:
        if kind == "oracle":
            patch = t["reference_patch"]
        elif kind == "noop":
            patch = ""
        elif kind == "cheat":
            f = t["f2p_tests"][0].split("::")[0]
            patch = f"diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n@@ -1 +1 @@\n-x\n+y\n"
        else:  # half
            parts = [p for p in re.split(r"(?m)^(?=diff --git )", t["reference_patch"]) if p.strip()]
            if len(parts) < 2:
                continue
            patch = parts[0]
        preds[t["instance_id"]] = {"model_patch": patch}
    return preds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=Path, default=Path("tasks/tasks.jsonl"))
    ap.add_argument("--baseline", type=Path, default=Path("tasks/baseline_failures.json"))
    ap.add_argument("--backend", choices=["docker", "local"], default="local")
    ap.add_argument("--image", default="pydanticbench:base")
    ap.add_argument("--repo", type=Path, help="local backend only")
    ap.add_argument("--pristine", type=Path, help="local backend only")
    ap.add_argument("-n", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path("results/selftest.md"))
    args = ap.parse_args()
    if args.backend == "local" and not (args.repo and args.pristine):
        raise SystemExit("--backend local requires --repo and --pristine")

    all_tasks = [json.loads(l) for l in args.tasks.open()]
    # Use every genuine multi-hop task available -- there are few, and a small
    # slice of them is not representative.
    multi = [t for t in all_tasks if t["task_type"] == "T2"][: max(args.n, 5)]
    single = [t for t in all_tasks if t["task_type"] != "T2"][: args.n]
    baseline = set(json.loads(args.baseline.read_text()))

    report, failures = [], []

    # Deterministic arithmetic check first -- needs no container, so a failure
    # here localises the problem to the formula rather than the environment.
    cases = [
        ((1.0, 1.0, 0), 1.0),     # complete, clean fix
        ((0.0, 1.0, 0), 0.0),     # no progress
        ((0.5, 1.0, 0), 0.5),     # half the broken behaviour restored
        ((1.0, 1.0, 1), 0.5),     # complete fix, one regression -> halved
        ((1.0, 0.0, 0), 0.9),     # complete fix, lint dirty -> 10% penalty
        ((0.25, 1.0, 3), 0.0625),
    ]
    bad = [(a, e, scorer.compute_score(*a)) for a, e in cases
           if abs(scorer.compute_score(*a) - e) > 1e-9]
    report.append(("formula", len(cases), 1.0 if not bad else 0.0, not bad))
    if bad:
        failures.append("formula")
        for args_, exp, got in bad:
            print(f"[selftest] formula{args_} expected {exp}, got {got}", file=sys.stderr)
    print(f"[selftest] {'formula':7s} n={len(cases)} "
          f"{'PASS' if not bad else 'FAIL'}", file=sys.stderr)

    for kind, tasks in (("oracle", single), ("noop", single),
                        ("cheat", single), ("half", multi)):
        preds = build_preds(tasks, kind)
        scores = []
        for t in tasks:
            if t["instance_id"] not in preds:
                continue
            if args.backend == "docker":
                be = scorer.DockerBackend()
                be.start(args.image)
            else:
                be = scorer.LocalBackend(args.repo, args.pristine)
            try:
                scores.append(scorer.score_one(
                    be, t, preds[t["instance_id"]]["model_patch"], baseline))
            finally:
                # The local backend mutates a real checkout in place. Leaving the
                # last task's patches applied silently poisons anything that
                # later snapshots that tree as "pristine" -- which is exactly how
                # a contaminated reference tree was produced once already.
                if args.backend == "local":
                    try:
                        be.reset()
                    except Exception:
                        pass
                be.stop()
        mean = sum(s["score"] for s in scores) / max(1, len(scores))
        if kind == "oracle":
            ok = all(s["score"] >= 0.999 for s in scores)
        elif kind in ("noop", "cheat"):
            ok = all(s["score"] <= 0.001 for s in scores)
        else:
            # The only scorer invariant here: half a fix is never full marks.
            ok = all(s["score"] < 0.999 for s in scores)
        report.append((kind, len(scores), mean, ok))
        # Always show the working -- a bare mean hides which task did what.
        for sc in scores:
            print(f"[selftest]   {sc['instance_id']:30s} score={sc['score']:.3f} "
                  f"f2p={sc.get('f2p', 0):.2f} regressions={sc.get('n_regressions', 0)} "
                  f"{sc.get('reason', '')}", file=sys.stderr)
        if not ok:
            failures.append(kind)
        print(f"[selftest] {kind:7s} n={len(scores)} mean={mean:.3f} "
              f"{'PASS' if ok else 'FAIL'}", file=sys.stderr)

    expect = {"formula": "exact", "oracle": "1.000", "noop": "0.000",
              "cheat": "0.000 (gated)", "half": "< 1.000"}
    lines = ["# Scorer self-test", "",
             f"Verifies scoring behaviour against known-value inputs "
             f"({args.n}-task sample, {args.backend} backend).", "",
             "| Control | n | Mean score | Expected | Result |", "|---|---:|---:|---|---|"]
    for kind, n, mean, ok in report:
        lines.append(f"| {kind} | {n} | {mean:.3f} | {expect[kind]} | {'PASS' if ok else 'FAIL'} |")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    if failures:
        sys.exit(f"SELFTEST FAILED: {failures}")


if __name__ == "__main__":
    main()
