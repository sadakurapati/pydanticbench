#!/usr/bin/env python3
"""
PydanticBench -- verify every task's starting state applies inside the image.

Why this exists: the task set was generated against pydantic's **sdist**, while
the Docker image checks out the **git tag**. Those trees are built from the same
release and should be identical, but "should be" is not a guarantee -- and if
any source file differs, `git apply` fails, every task reports
`setup_patch_failed`, and all three models score 0.0 for reasons that have
nothing to do with their ability.

That failure would look exactly like three very bad models. It is worth ten
seconds to rule out before spending an API budget.

Usage:
    python3 scripts/08_verify_tasks_apply.py --image pydanticbench:base
    python3 scripts/08_verify_tasks_apply.py --backend local --repo /tmp/repo
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=Path, default=Path("tasks/tasks.jsonl"))
    ap.add_argument("--backend", choices=["docker", "local"], default="docker")
    ap.add_argument("--image", default="pydanticbench:base")
    ap.add_argument("--repo", type=Path, help="local backend only")
    ap.add_argument("--limit", type=int, default=0, help="check only the first N")
    args = ap.parse_args()

    tasks = [json.loads(l) for l in args.tasks.open()]
    if args.limit:
        tasks = tasks[: args.limit]

    cid = None
    if args.backend == "docker":
        name = f"pydbench-verify-{uuid.uuid4().hex[:8]}"
        cid = subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", name, args.image, "sleep", "600"],
            capture_output=True, text=True, check=True).stdout.strip()

    def sh(cmd: str, stdin: str | None = None):
        if cid:
            full = ["docker", "exec", "-i", "-w", "/testbed", cid, "bash", "-c", cmd]
        else:
            full = ["bash", "-c", cmd]
        return subprocess.run(full, input=stdin, capture_output=True, text=True,
                              cwd=None if cid else args.repo)

    bad = []
    try:
        for t in tasks:
            # --check applies nothing; it only reports whether the patch would apply.
            r = sh("git apply --check --whitespace=nowarn -", t["setup_patch"])
            if r.returncode != 0:
                bad.append((t["instance_id"],
                            (r.stderr or r.stdout).strip().splitlines()[:2]))
    finally:
        if cid:
            subprocess.run(["docker", "kill", cid], capture_output=True, check=False)

    n = len(tasks)
    if bad:
        print(f"[verify] {len(bad)}/{n} task patches DO NOT apply", file=sys.stderr)
        for iid, err in bad[:10]:
            print(f"  {iid}: {' | '.join(err)}", file=sys.stderr)
        print("\n  The image's source tree does not match the tree the tasks were\n"
              "  generated against. Rebuild with the correct BASE_TAG, or regenerate\n"
              "  the task set against this tree (README section 6).", file=sys.stderr)
        sys.exit(1)
    print(f"[verify] all {n} task patches apply cleanly", file=sys.stderr)


if __name__ == "__main__":
    main()
