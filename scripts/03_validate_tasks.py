#!/usr/bin/env python3
"""
PydanticBench -- stage 3: candidate validation and task materialisation.

A candidate becomes a task only if it survives every check below. This is where
benchmark difficulty is actually controlled, so the filters are documented
inline rather than buried.

Pipeline per candidate:
  1. apply setup_patch in place
  2. run the test suite (whole suite; ~4.5s on this repo)
  3. F2P := failures(mutated) - failures(baseline)
        Baseline subtraction is essential. Some tests fail for environment
        reasons unrelated to the mutation (subprocess-based version checks,
        source-introspection tests). Attributing those to the agent would make
        scores unearnable and non-reproducible.
  4. accept iff MIN_FAIL <= |F2P| <= MAX_FAIL
        Lower bound rejects equivalent mutants (semantically no-op edits).
        Upper bound rejects mutations that shatter the suite -- trivially
        localised from any traceback, and would saturate the benchmark.
  5. reference patch must restore the tree byte-for-byte (solvability proof)
  6. capture failure detail and synthesise a symptom-style prompt
  7. revert to clean

Runs as resumable, time-bounded slices: state is checkpointed after every
candidate, so an interrupted run loses at most one.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from prompt_sanitize import redact_locations  # noqa: E402
from pytest_scope import ignore_args  # noqa: E402

MIN_FAIL = 1
MAX_FAIL = 4
SUITE_TIMEOUT = 180
PRISTINE: Path

PYTEST_BASE = ([sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"]
               + ignore_args())
FAIL_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)


def run_suite(repo: Path, detail: bool = False, only: list[str] | None = None,
              maxfail: int | None = None):
    """
    Run the suite and return the set of failing node ids.

    ``maxfail`` is a speed optimisation: any candidate exceeding the acceptance
    cap is rejected regardless of the exact count, so there is no reason to
    finish the run. Some mutations break 900+ tests; aborting at the cap turns
    those from ~4.5s into ~0.5s.
    """
    if only:
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *only]
    else:
        cmd = list(PYTEST_BASE)
    if maxfail:
        cmd += [f"--maxfail={maxfail}"]
    cmd += ["--tb=long", "-rf"] if detail else ["--tb=no", "-rf"]
    try:
        r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                           timeout=SUITE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    return set(FAIL_RE.findall(r.stdout)), r.stdout


def git_apply(repo: Path, patch: str) -> bool:
    r = subprocess.run(["git", "apply", "--whitespace=nowarn"], cwd=repo,
                       input=patch, capture_output=True, text=True)
    return r.returncode == 0


def revert_all(repo: Path):
    subprocess.run(["rsync", "-a", "--delete", str(PRISTINE / "pydantic") + "/",
                    str(repo / "pydantic") + "/"], check=True)


def identical_to_pristine(repo: Path, sites: list) -> bool:
    """
    True iff every touched file matches the pristine tree byte-for-byte.

    The reference patch is the exact inverse of the setup patch, so applying it
    must reproduce the original file. Byte-identity with a known-green baseline
    is a stronger guarantee than re-running the suite -- and free.
    """
    for site in sites:
        if (repo / site["file"]).read_bytes() != (PRISTINE / site["file"]).read_bytes():
            return False
    return True


def extract_repro(repo: Path, nodeid: str) -> str | None:
    """
    Pull the failing test's body and present it as a bug reproduction snippet.

    The agent sees WHAT breaks, never WHERE the fix goes: the test's path and
    name are stripped. This is deliberately harder than SWE-bench, where issue
    text often contains a traceback naming the exact frame to edit.
    """
    m = re.match(r"([^:]+)::(?:.+::)?([^:\[]+)", nodeid)
    if not m:
        return None
    path, func = repo / m.group(1), m.group(2)
    if not path.exists():
        return None
    text = path.read_text()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            src = ast.get_source_segment(text, node)
            if not src:
                return None
            src = re.sub(r"^@.*\n", "", src, flags=re.M)  # decorators leak identity
            return src.replace(f"def {func}(", "def reproduce(")[:2500]
    return None


# Lines that leak harness identity or add nothing diagnostic.
_NOISE = re.compile(r"^(tests/|/|_{5,}|=+|\s*$|Collected \d+ items?"
                    r"|[.FEsx]+\s+\[\s*\d+%\]|\d+ (failed|passed|error)|-+ generated )")


def sanitise_error(detail_out: str) -> str:
    """
    Reduce pytest output to behavioural evidence only.

    Everything that could shortcut the search is removed: file paths, the test's
    own name, harness progress chatter. What survives is the expected-vs-actual
    delta -- the same information a user filing a bug report would have.
    """
    lines = []
    for ln in detail_out.splitlines():
        if _NOISE.match(ln) or "site-packages" in ln or ln.startswith(("FAILED", "ERROR")):
            continue
        lines.append(ln)
        if len(lines) >= 25:
            break
    return redact_locations("\n".join(lines))[:1800]


PROMPT_MUTATION = """\
A regression has been introduced somewhere in the `pydantic` library source tree.
The library imports cleanly and the vast majority of its behaviour is unaffected,
but the following code no longer behaves correctly:

```python
{repro}
```

Observed failure:

```
{error}
```

Locate the root cause in the library source under `pydantic/` and fix it, so that
the behaviour above is correct again and no other behaviour regresses.

Constraints:
- Do NOT modify anything under `tests/`, and do NOT modify `conftest.py`.
- Do NOT modify packaging or configuration files (`pyproject.toml`, `setup.cfg`, ...).
- The fix belongs in the library source, not in the reproduction snippet.
- There may be more than one faulty location.
"""

PROMPT_REMOVAL = """\
The body of one function in the `pydantic` library source has been removed and
replaced with `raise NotImplementedError`. As a result the following code fails:

```python
{repro}
```

Observed failure:

```
{error}
```

Locate the unimplemented function under `pydantic/` and implement it correctly,
consistent with how the rest of the codebase uses it.

Constraints:
- Do NOT modify anything under `tests/`, and do NOT modify `conftest.py`.
- Do NOT modify packaging or configuration files.
- Your implementation must satisfy all existing behaviour, not just the snippet above.
"""


def difficulty(task_type: str, f2p: set, sites: list) -> str:
    """
    Assign a tier by construction, so results can be reported per-tier. A
    benchmark that decays monotonically across tiers is demonstrably
    discriminative; one that does not is either saturated or noise.
    """
    if task_type == "T2":
        return "hard"
    score = 0
    if len(f2p) == 1:
        score += 1                       # narrow signal -> hard to localise
    if any("_internal" in s["file"] for s in sites):
        score += 1                       # deep in the machinery, not the surface
    stems = {Path(s["file"]).stem.lstrip("_") for s in sites}
    if not any(any(st and st in n for st in stems) for n in f2p):
        score += 1                       # failing test does not name the guilty module
    if task_type == "T3" and not sites[0].get("kept_docstring", True):
        score += 1                       # contract must be inferred from call sites
    return "hard" if score >= 3 else ("medium" if score == 2 else "easy")


def main():
    global PRISTINE
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--pristine", required=True, type=Path)
    ap.add_argument("--candidates", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--rejects", type=Path, default=None)
    ap.add_argument("--target", type=int, default=100)
    ap.add_argument("--base-commit", default="v2.13.4")
    ap.add_argument("--image", default="pydanticbench:base")
    ap.add_argument("--time-budget", type=float, default=0,
                    help="stop after N seconds and record resume position (0 = unlimited)")
    ap.add_argument("--state", type=Path, default=None)
    args = ap.parse_args()
    PRISTINE = args.pristine

    revert_all(args.repo)
    print("[val] computing baseline ...", file=sys.stderr, flush=True)
    baseline, _ = run_suite(args.repo)
    if baseline is None:
        sys.exit("baseline run timed out")
    print(f"[val] baseline: {len(baseline)} pre-existing failures (excluded everywhere)",
          file=sys.stderr, flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    (args.out.parent / "baseline_failures.json").write_text(json.dumps(sorted(baseline), indent=2))

    cands = [json.loads(l) for l in args.candidates.open()]
    state_path = args.state or (args.out.parent / "_validation_state.json")
    start, accepted = 0, []
    if state_path.exists():
        start = json.loads(state_path.read_text())["next_index"]
        if args.out.exists():
            accepted = [json.loads(l) for l in args.out.open()]
        print(f"[val] resuming at candidate {start} with {len(accepted)} tasks",
              file=sys.stderr, flush=True)

    out_fh = args.out.open("a")
    rej_fh = args.rejects.open("a") if args.rejects else None
    t0 = time.time()
    n = start - 1

    def checkpoint(next_index: int):
        out_fh.flush()
        if rej_fh:
            rej_fh.flush()
        state_path.write_text(json.dumps(
            {"next_index": next_index, "total": len(cands), "accepted": len(accepted)}))

    for n in range(start, len(cands)):
        c = cands[n]
        if len(accepted) >= args.target:
            break
        if args.time_budget and (time.time() - t0) > args.time_budget:
            n -= 1
            break

        def reject(reason: str):
            if rej_fh:
                rej_fh.write(json.dumps({"candidate_id": c.get("candidate_id"),
                                         "task_type": c.get("task_type"),
                                         "reject": reason, "sites": c.get("sites")}) + "\n")
            checkpoint(n + 1)

        revert_all(args.repo)
        if not git_apply(args.repo, c["setup_patch"]):
            reject("patch_did_not_apply"); continue

        fails, _ = run_suite(args.repo, maxfail=MAX_FAIL + len(baseline) + 1)
        if fails is None:
            reject("timeout"); continue
        f2p = fails - baseline
        if not (MIN_FAIL <= len(f2p) <= MAX_FAIL):
            reject(f"failure_count={len(f2p)}"); continue

        if not git_apply(args.repo, c["reference_patch"]):
            reject("reference_did_not_apply"); continue
        if not identical_to_pristine(args.repo, c["sites"]):
            reject("reference_did_not_restore"); continue

        revert_all(args.repo)
        git_apply(args.repo, c["setup_patch"])
        nodeid = sorted(f2p)[0]
        _, detail = run_suite(args.repo, detail=True, only=[nodeid])
        repro = extract_repro(args.repo, nodeid)
        if not repro:
            reject("no_repro_extractable"); continue

        tmpl = PROMPT_REMOVAL if c["task_type"] == "T3" else PROMPT_MUTATION
        tier = difficulty(c["task_type"], f2p, c["sites"])
        accepted.append({
            "instance_id": f"pydanticbench__{c['task_type'].lower()}_{len(accepted):03d}",
            "repo": "pydantic/pydantic", "base_commit": args.base_commit,
            "image_name": args.image,
            "problem_statement": tmpl.format(repro=repro.strip(),
                                             error=sanitise_error(detail).strip()),
            "task_type": c["task_type"], "difficulty": tier,
            "setup_patch": c["setup_patch"], "reference_patch": c["reference_patch"],
            "f2p_tests": sorted(f2p), "p2p_mode": "full_suite_minus_baseline",
            "sites": c["sites"], "weights": {"f2p": 0.6, "p2p": 0.3, "static": 0.1},
        })
        out_fh.write(json.dumps(accepted[-1]) + "\n")
        print(f"[val] cand {n+1}/{len(cands)} -> {len(accepted)} tasks "
              f"({c['task_type']}/{tier}, {len(f2p)} f2p) [{time.time()-t0:.0f}s]",
              file=sys.stderr, flush=True)
        checkpoint(n + 1)

    revert_all(args.repo)
    checkpoint(n + 1)
    out_fh.close()
    if rej_fh:
        rej_fh.close()
    print(f"[val] DONE: {len(accepted)} accepted, {time.time()-t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
