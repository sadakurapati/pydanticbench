#!/usr/bin/env python3
"""
PydanticBench -- stage 3b: stratified selection of the final 100 tasks.

Validation over-produces. Selecting the final set is not a formality: a uniform
random draw would inherit the pool's easy-heavy skew, and an easy-heavy
benchmark saturates. Selection therefore takes every hard and medium task
available and back-fills with easy ones.

Non-selected tasks stay in tasks_pool.jsonl as the reserve used to replace tasks
that later prove degenerate.
"""

from __future__ import annotations

import argparse
import base64
import collections
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from prompt_sanitize import redact_problem_statement  # noqa: E402

SUITE_SIZE = 5584  # passing tests at baseline; denominator for the P2P term


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", type=Path, default=Path("tasks/tasks_pool.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("tasks/tasks.jsonl"))
    ap.add_argument("--hf-dir", type=Path, default=Path("tasks/hf"))
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20260811)
    args = ap.parse_args()

    pool = [json.loads(l) for l in args.pool.open()]
    rng = random.Random(args.seed)

    by_tier = collections.defaultdict(list)
    for t in pool:
        by_tier[t["difficulty"]].append(t)
    for v in by_tier.values():
        rng.shuffle(v)

    # Priority order is the point: it maximises the share of discriminative tasks.
    selected = []
    for tier in ("hard", "medium", "easy"):
        selected += by_tier[tier][: max(0, args.n - len(selected))]

    for i, t in enumerate(sorted(selected, key=lambda x: (x["task_type"], x["difficulty"]))):
        t["instance_id"] = f"pydanticbench__{t['task_type'].lower()}_{t['difficulty'][:1]}_{i:03d}"
        t["suite_size"] = SUITE_SIZE
        # Base64 of the setup patch. The agent harness injects this into a shell
        # command via a YAML config, and a raw multi-line diff cannot survive
        # that trip: newlines, quotes, backticks and $ all get interpreted.
        # Base64 is a single line drawn from [A-Za-z0-9+/=] -- inert to both YAML
        # folding and shell parsing.
        t["setup_patch_b64"] = base64.b64encode(t["setup_patch"].encode()).decode()
        # Belt-and-braces: re-run redaction on the final prompts.
        t["problem_statement"] = redact_problem_statement(t["problem_statement"])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for t in selected:
            f.write(json.dumps(t) + "\n")

    # mini-swe-agent loads tasks with datasets.load_dataset(path, split=...),
    # so ship a directory it can infer: tasks/hf/train.jsonl
    args.hf_dir.mkdir(parents=True, exist_ok=True)
    with (args.hf_dir / "train.jsonl").open("w") as f:
        for t in selected:
            f.write(json.dumps(t) + "\n")

    print(f"selected {len(selected)} tasks -> {args.out}")
    print("  by tier:", dict(collections.Counter(t["difficulty"] for t in selected)))
    print("  by type:", dict(collections.Counter(t["task_type"] for t in selected)))
    ops = collections.Counter(s["operator"] for t in selected for s in t["sites"])
    print("  operators:", dict(ops))


if __name__ == "__main__":
    main()
