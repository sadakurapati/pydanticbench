#!/usr/bin/env python3
"""
PydanticBench -- stage 6: aggregate scores into report tables.

Beyond the headline mean, three breakdowns matter:
  * by difficulty tier -- the evidence for or against saturation. A benchmark
    that discriminates shows monotonic decay from easy to hard.
  * by task family    -- shows whether one generator dominates the signal.
  * gate trips        -- how often a model produced a patch touching the grading
    criteria. Non-standard, and interesting: it measures whether a model tries
    to reshape the problem when it cannot solve it.
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
from pathlib import Path


def load(results: Path):
    runs = {}
    for d in sorted(p for p in results.iterdir() if p.is_dir()):
        f = d / "scores.json"
        if f.exists():
            runs[d.name] = json.loads(f.read_text())
    return runs


def agg(rows, key, val):
    sel = [r for r in rows if r.get(key) == val]
    return (statistics.mean(r["score"] for r in sel), len(sel)) if sel else (0.0, 0)


def table(runs: dict) -> str:
    out = ["# PydanticBench results", "", "## Headline", "",
           "| Config | Mean score | Fully solved | Zero score | Gate trips |",
           "|---|---:|---:|---:|---:|"]
    for name, rows in runs.items():
        mean = statistics.mean(r["score"] for r in rows) if rows else 0
        full = sum(1 for r in rows if r["score"] >= 0.999)
        zero = sum(1 for r in rows if r["score"] <= 0.001)
        gate = sum(1 for r in rows if r.get("gate") == 0
                   and r.get("reason", "").startswith(("forbidden", "sabotage")))
        out.append(f"| {name} | {mean:.3f} | {full}/{len(rows)} | {zero}/{len(rows)} | {gate} |")

    out += ["", "## By difficulty tier", "",
            "| Config | easy | medium | hard |", "|---|---:|---:|---:|"]
    for name, rows in runs.items():
        out.append(f"| {name} | " + " | ".join(
            f"{agg(rows, 'difficulty', t)[0]:.3f}" for t in ("easy", "medium", "hard")) + " |")

    out += ["", "## By task family", "",
            "| Config | T1 single mutation | T2 multi-hop | T3 reimplementation |",
            "|---|---:|---:|---:|"]
    for name, rows in runs.items():
        out.append(f"| {name} | " + " | ".join(
            f"{agg(rows, 'task_type', t)[0]:.3f}" for t in ("T1", "T2", "T3")) + " |")

    out += ["", "## Failure reasons", "",
            "| Config | empty/no patch | patch failed | forbidden path | harness error |",
            "|---|---:|---:|---:|---:|"]
    for name, rows in runs.items():
        c = collections.Counter()
        for r in rows:
            reason = r.get("reason", "")
            if reason.startswith("empty"):
                c["empty"] += 1
            elif "patch_failed" in reason:
                c["patch"] += 1
            elif reason.startswith(("forbidden", "sabotage")):
                c["forbidden"] += 1
            elif reason.startswith("harness"):
                c["harness"] += 1
        out.append(f"| {name} | {c['empty']} | {c['patch']} | {c['forbidden']} | {c['harness']} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--out", type=Path, default=Path("results"))
    args = ap.parse_args()
    runs = load(args.results)
    if not runs:
        raise SystemExit(f"no scores.json found under {args.results}")
    md = table(runs)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.md").write_text(md + "\n")
    print(md)


if __name__ == "__main__":
    main()
