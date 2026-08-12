#!/usr/bin/env python3
"""
PydanticBench -- stage 3c: verify that multi-hop tasks are genuinely multi-hop.

T2 tasks inject two mutations in different modules, on the premise that an agent
must find BOTH. That premise is not automatically true: the second mutation may
sit on a code path no failing test exercises, in which case repairing the first
alone restores the whole fail-to-pass set and the task is a single-defect task
wearing a multi-hop label.

This was not a hypothesis. The scorer self-test flagged it: one T2 task scored a
full 1.000 on the "half fix" control, which is only possible if half the fix is
the whole fix.

For each T2 task this script applies each half of the reference patch on its own.
If EITHER half alone restores every F2P test, the task is demoted to T1 and its
difficulty tier is recomputed under the T1 rule. Demoted tasks are still valid --
they are simply not multi-hop, and mislabelling them would corrupt the by-family
breakdown in the results.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("val", HERE / "03_validate_tasks.py")
val = importlib.util.module_from_spec(spec)
spec.loader.exec_module(val)


def halves(patch: str) -> list[str]:
    return [p for p in re.split(r"(?m)^(?=diff --git )", patch) if p.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", type=Path, default=Path("tasks/tasks_pool.jsonl"))
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--pristine", required=True, type=Path)
    ap.add_argument("--baseline", type=Path, default=Path("tasks/baseline_failures.json"))
    args = ap.parse_args()
    val.PRISTINE = args.pristine
    baseline = set(json.loads(args.baseline.read_text()))

    rows = [json.loads(l) for l in args.pool.open()]
    demoted = 0
    for t in rows:
        if t["task_type"] != "T2":
            continue
        parts = halves(t["reference_patch"])
        if len(parts) != 2:
            continue
        genuinely_multihop = True
        for part in parts:
            val.revert_all(args.repo)
            if not val.git_apply(args.repo, t["setup_patch"]):
                continue
            if not val.git_apply(args.repo, part):
                continue
            fails, _ = val.run_suite(args.repo, only=t["f2p_tests"])
            remaining = (fails or set()) - baseline
            if not remaining:          # this half alone fixed everything
                genuinely_multihop = False
                break
        if not genuinely_multihop:
            t["task_type"] = "T1"
            t["difficulty"] = val.difficulty("T1", set(t["f2p_tests"]), t["sites"])
            t["demoted_from"] = "T2"
            demoted += 1
            print(f"[multihop] demoted {t['instance_id']} -> T1/{t['difficulty']}",
                  file=sys.stderr, flush=True)

    val.revert_all(args.repo)
    with args.pool.open("w") as f:
        for t in rows:
            f.write(json.dumps(t) + "\n")
    n_t2 = sum(1 for t in rows if t["task_type"] == "T2")
    print(f"[multihop] {demoted} demoted; {n_t2} genuine multi-hop tasks remain",
          file=sys.stderr)


if __name__ == "__main__":
    main()
