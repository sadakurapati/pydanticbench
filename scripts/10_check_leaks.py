#!/usr/bin/env python3
"""
PydanticBench -- confirm a running task does not hand the agent the answer.

Two leaks shipped before this check existed, both trivially exploitable and both
invisible to every other gate:

  1. The setup patch was committed on top of the clean base, so `git show HEAD`
     printed the injected defect as a one-line delta and `git revert HEAD`
     solved the task without reading any code.
  2. The image kept a clean copy of the source at /opt/pristine/pydantic, so
     `diff -r /opt/pristine/pydantic /testbed/pydantic` printed the defect.

Neither was caught by the baseline check, the task-application check, the scorer
controls or the model probe, because all of those exercise the *scoring* path.
This one exercises the path the agent sees.

Usage:
    python3 scripts/10_check_leaks.py --image pydanticbench:base
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

import yaml
from jinja2 import StrictUndefined, Template


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="pydanticbench:base")
    ap.add_argument("--tasks", type=Path, default=Path("tasks/tasks.jsonl"))
    ap.add_argument("--config", type=Path, default=Path("configs/pydanticbench.yaml"))
    ap.add_argument("-n", type=int, default=3, help="tasks to inspect")
    args = ap.parse_args()

    cfg = yaml.safe_load(args.config.read_text())
    tmpl = cfg["run"]["env_startup_command"]
    tasks = [json.loads(l) for l in args.tasks.open()][: args.n]

    failures = []
    for t in tasks:
        name = f"pydbench-leak-{uuid.uuid4().hex[:8]}"
        cid = subprocess.run(["docker", "run", "-d", "--rm", "--name", name,
                              args.image, "sleep", "300"],
                             capture_output=True, text=True, check=True).stdout.strip()
        def sh(cmd: str) -> str:
            return subprocess.run(["docker", "exec", "-w", "/testbed", cid, "bash", "-lc", cmd],
                                  capture_output=True, text=True).stdout
        try:
            startup = Template(tmpl, undefined=StrictUndefined).render(**t)
            # strip the outer `bash -lc '...'` so it can run through docker exec
            inner = startup[startup.index("'") + 1: startup.rindex("'")]
            sh(inner)

            iid = t["instance_id"]
            n_commits = len(sh("git log --oneline").strip().splitlines())
            if n_commits != 1:
                failures.append(f"{iid}: git history has {n_commits} commits; the defect is diffable")

            show = sh("git show HEAD --format=")
            if [l for l in show.splitlines() if l.startswith("-") and not l.startswith("---")]:
                failures.append(f"{iid}: `git show HEAD` reveals the injected defect")

            if sh("test -d /opt/pristine/pydantic && echo yes").strip() == "yes":
                failures.append(f"{iid}: a clean copy of the source is readable at /opt/pristine/pydantic")

            # a correct fix must still produce something to submit
            sh("printf %s " + t["setup_patch_b64"] + " | base64 -d | git apply -R - 2>/dev/null")
            if not sh("git diff -- pydantic/").strip():
                failures.append(f"{iid}: a correct fix produces an empty `git diff`")
        finally:
            subprocess.run(["docker", "kill", cid], capture_output=True, check=False)

    if failures:
        print(f"[leaks] {len(failures)} problem(s) found:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    print(f"[leaks] {len(tasks)} tasks inspected: history clean, no pristine source, "
          f"fixes are submittable", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
