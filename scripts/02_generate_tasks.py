#!/usr/bin/env python3
"""
PydanticBench -- stage 2: candidate task generation.

Emits CANDIDATE tasks. A candidate is not yet a task: it becomes one only after
stage 3 validates that it actually breaks tests, and breaks the right number of
them. Over-generate here; stage 3 is the filter.

Families produced:
  T1/T2  mutation repair    -- inject 1 (T1) or 2 (T2) semantic mutations
  T3     reimplementation   -- replace a function body with NotImplementedError

Usage:
    python3 02_generate_tasks.py --repo /testbed --out tasks/candidates.jsonl
"""

from __future__ import annotations

import argparse
import difflib
import json
import random
import sys
from pathlib import Path

import libcst as cst

sys.path.insert(0, str(Path(__file__).parent))
from mutation_ops import ALL_OPERATORS, OPERATORS_BY_NAME, apply_site, count_sites  # noqa: E402

# Excluded from mutation, with reasons:
#   mypy.py        -- a mypy plugin; tested out-of-process, failures are opaque
#   version.py     -- version strings; mutations are trivially visible
#   _migration.py  -- v1 shim, mostly a large lookup dict
#   deprecated/,v1 -- deprecated surface; fixes there teach nothing
EXCLUDE = {"mypy.py", "version.py", "_migration.py"}
EXCLUDE_DIRS = {"deprecated", "v1"}


def source_files(repo: Path) -> list[Path]:
    out = []
    for p in sorted((repo / "pydantic").rglob("*.py")):
        if p.name in EXCLUDE or p.name == "__init__.py":
            continue
        if any(d in p.parts for d in EXCLUDE_DIRS):
            continue
        if len(p.read_text().splitlines()) < 80:
            continue  # too small to hide a bug in
        out.append(p)
    return out


def make_patch(rel: str, old: str, new: str) -> str:
    """Unified diff with git-style headers, applicable via `git apply`."""
    body = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3))
    return f"diff --git a/{rel} b/{rel}\n{body}" if body else ""


def gen_mutation_candidates(repo: Path, n: int, rng: random.Random, n_mutations: int = 1):
    files = source_files(repo)
    site_space: list[tuple[Path, str, int]] = []
    parsed: dict[Path, cst.Module] = {}
    for p in files:
        try:
            mod = cst.parse_module(p.read_text())
        except Exception:
            continue
        parsed[p] = mod
        for op in ALL_OPERATORS:
            for i in range(count_sites(op, mod)):
                site_space.append((p, op.name, i))

    print(f"[gen] {len(files)} modules, {len(site_space)} mutation sites available",
          file=sys.stderr)
    rng.shuffle(site_space)

    out, used, idx = [], set(), 0
    while len(out) < n and idx < len(site_space):
        picks, seen_files = [], set()
        # For T2 the two mutations must live in DIFFERENT modules. That is what
        # makes the task multi-hop: the failing traceback points at one site,
        # and fixing only that one restores nothing.
        while len(picks) < n_mutations and idx < len(site_space):
            cand = site_space[idx]
            idx += 1
            if n_mutations > 1 and cand[0] in seen_files:
                continue
            if (str(cand[0]), cand[1], cand[2]) in used:
                continue
            picks.append(cand)
            seen_files.add(cand[0])
        if len(picks) < n_mutations:
            break

        patch_parts, ref_parts, sites, ok = [], [], [], True
        for path, op_name, site_i in picks:
            rel = str(path.relative_to(repo))
            old = path.read_text()
            new, info = apply_site(OPERATORS_BY_NAME[op_name], parsed[path], site_i)
            if new is None or new == old:
                ok = False
                break
            fwd = make_patch(rel, old, new)   # clean -> buggy  (setup patch)
            rev = make_patch(rel, new, old)   # buggy -> clean  (reference fix)
            if not fwd:
                ok = False
                break
            patch_parts.append(fwd)
            ref_parts.append(rev)
            info["file"] = rel
            sites.append(info)
            used.add((str(path), op_name, site_i))
        if not ok:
            continue
        out.append({"task_type": "T2" if n_mutations > 1 else "T1",
                    "setup_patch": "".join(patch_parts),
                    "reference_patch": "".join(ref_parts), "sites": sites})
    return out


class BodyRemover(cst.CSTTransformer):
    """Replace the k-th sufficiently-large function body with NotImplementedError."""

    def __init__(self, target: int | None, min_lines: int, keep_docstring: bool):
        super().__init__()
        self.count = 0
        self.target = target
        self.min_lines = min_lines
        self.keep_docstring = keep_docstring
        self.applied: dict | None = None

    def leave_FunctionDef(self, original, updated):
        body_lines = len(cst.Module(body=[updated.body]).code.splitlines())
        if body_lines < self.min_lines or updated.name.value.startswith("__"):
            return updated
        idx = self.count
        self.count += 1
        if self.target is None or idx != self.target:
            return updated
        stmts = []
        if self.keep_docstring:
            first = updated.body.body[0] if updated.body.body else None
            if (isinstance(first, cst.SimpleStatementLine)
                    and isinstance(first.body[0], cst.Expr)
                    and isinstance(first.body[0].value, cst.SimpleString)):
                stmts.append(first)
        stmts.append(cst.parse_statement("raise NotImplementedError"))
        self.applied = {"function": updated.name.value, "body_lines": body_lines,
                        "kept_docstring": self.keep_docstring}
        return updated.with_changes(body=updated.body.with_changes(body=stmts))


def gen_removal_candidates(repo: Path, n: int, rng: random.Random, min_lines: int = 8):
    files = source_files(repo)
    space, parsed = [], {}
    for p in files:
        try:
            mod = cst.parse_module(p.read_text())
        except Exception:
            continue
        parsed[p] = mod
        probe = BodyRemover(None, min_lines, True)
        mod.visit(probe)
        space.extend((p, i) for i in range(probe.count))
    print(f"[gen] {len(space)} removable function bodies available", file=sys.stderr)
    rng.shuffle(space)

    out = []
    for path, i in space:
        if len(out) >= n:
            break
        rel = str(path.relative_to(repo))
        old = path.read_text()
        # Half keep the docstring (medium); half strip it (hard -- the contract
        # must be inferred from call sites and tests alone).
        keep_doc = rng.random() < 0.5
        t = BodyRemover(i, min_lines, keep_doc)
        new = parsed[path].visit(t).code
        if t.applied is None or new == old:
            continue
        fwd, rev = make_patch(rel, old, new), make_patch(rel, new, old)
        if not fwd:
            continue
        out.append({"task_type": "T3", "setup_patch": fwd, "reference_patch": rev,
                    "sites": [dict(t.applied, file=rel, operator="body_removal")]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--n-t1", type=int, default=260)
    ap.add_argument("--n-t2", type=int, default=70)
    ap.add_argument("--n-t3", type=int, default=70)
    ap.add_argument("--seed", type=int, default=20260811)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    cands = []
    cands += gen_mutation_candidates(args.repo, args.n_t1, rng, 1)
    cands += gen_mutation_candidates(args.repo, args.n_t2, rng, 2)
    cands += gen_removal_candidates(args.repo, args.n_t3, rng)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for i, c in enumerate(cands):
            c["candidate_id"] = f"cand_{i:04d}"
            f.write(json.dumps(c) + "\n")
    by_type: dict[str, int] = {}
    for c in cands:
        by_type[c["task_type"]] = by_type.get(c["task_type"], 0) + 1
    print(f"[gen] wrote {len(cands)} candidates to {args.out}: {by_type}", file=sys.stderr)


if __name__ == "__main__":
    main()
