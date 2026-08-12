#!/usr/bin/env python3
"""
PydanticBench -- stage 5: automatic scoring.

Maps a model-generated patch to a score in [0, 1]:

    score = gate x F2P x (0.9 + 0.1 x S) x 1/(1 + R)

    F2P    fraction of the task's fail-to-pass tests now passing. This is where
           partial credit lives: fixing 2 of 3 broken behaviours is genuinely
           better than fixing none, and a binary resolved/unresolved metric
           throws that signal away.
    R      number of previously-passing tests the patch broke.
    S      static checks on changed files (ruff clean), a 10% modifier.
    gate   0 if the patch tampers with grading criteria or fails to apply.

WHY MULTIPLICATIVE, NOT ADDITIVE
The first version used the additive form 0.6*F2P + 0.3*P2P + 0.1*S and it was
wrong. P2P is ~1.0 for almost any patch, because breaking a few tests out of
5,584 barely moves a fraction. So a patch that fixed NOTHING but broke nothing
scored 0.40 -- a large floor awarded for doing no work, compressing the range
models actually compete in. Measured on the no-op control before the change.

The multiplicative form gives 0 for no progress and 1 for a complete clean fix.
The regression term 1/(1+R) is deliberately sharp: one regression halves the
score, because shipping a fix that breaks something else is a serious failure,
not a rounding error.

Backends:
    --backend docker   the real evaluation path, one fresh container per task
    --backend local    same logic against a local checkout; used to validate the
                       scorer itself without a Docker daemon
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

SUITE_TIMEOUT = 600
FAIL_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)

# Paths a solution may never touch. Editing any of these means the submission is
# not solving the task, it is redefining the grading criteria.
FORBIDDEN = (
    re.compile(r"^tests/"),
    re.compile(r"conftest\.py$"),
    re.compile(r"^(pyproject\.toml|setup\.cfg|setup\.py|pytest\.ini|tox\.ini|Makefile)$"),
    re.compile(r"^\.github/"),
)
# Patterns that neutralise tests without editing test files.
SABOTAGE = (
    re.compile(r"^\+.*\bpytest\.(skip|xfail)\b", re.M),
    re.compile(r"^\+.*\bsys\.exit\b", re.M),
    re.compile(r"^\+.*collect_ignore", re.M),
)


class LocalBackend:
    """Runs against a checkout on this machine. For validating the scorer."""

    def __init__(self, repo: Path, pristine: Path):
        self.repo, self.pristine = repo, pristine

    def start(self, image): pass
    def stop(self): pass

    def sh(self, cmd: str, timeout: int = SUITE_TIMEOUT):
        return subprocess.run(["bash", "-c", cmd], cwd=self.repo,
                              capture_output=True, text=True, timeout=timeout)

    def reset(self):
        subprocess.run(["rsync", "-a", "--delete",
                        f"{self.pristine}/pydantic/", f"{self.repo}/pydantic/"], check=True)
        self.restore_tests()

    def restore_tests(self):
        if (self.pristine / "tests").exists():
            subprocess.run(["rsync", "-a", "--delete",
                            f"{self.pristine}/tests/", f"{self.repo}/tests/"], check=True)


class DockerBackend:
    """One disposable container per task. Network disabled during scoring."""

    def __init__(self):
        self.cid = None

    def start(self, image: str):
        name = f"pydbench-score-{uuid.uuid4().hex[:10]}"
        r = subprocess.run(["docker", "run", "-d", "--rm", "--network", "none",
                            "--name", name, image, "sleep", "infinity"],
                           capture_output=True, text=True, check=True)
        self.cid = r.stdout.strip()

    def stop(self):
        if self.cid:
            subprocess.run(["docker", "kill", self.cid], capture_output=True, check=False)
            self.cid = None

    def sh(self, cmd: str, timeout: int = SUITE_TIMEOUT):
        return subprocess.run(["docker", "exec", "-w", "/testbed", self.cid, "bash", "-c", cmd],
                              capture_output=True, text=True, timeout=timeout)

    def reset(self):
        self.sh("/opt/bench/reset_state.sh", timeout=180)

    def restore_tests(self):
        self.sh("/opt/bench/restore_tests.sh", timeout=180)


def b64(text: str) -> str:
    """Encode a patch for safe transport into the container."""
    return base64.b64encode(text.encode()).decode()


def compute_score(f2p: float, static: float, n_regressions: int) -> float:
    """
    The scoring arithmetic, isolated so it can be tested without a container.

    Pure and total: given a fail-to-pass fraction, a static-check result and a
    regression count, it returns the score. Keeping it separate matters because
    the end-to-end controls can only demonstrate partial credit when a task
    happens to admit a partial fix -- that is a property of the task set, not of
    the scorer. This function can be checked exhaustively either way.
    """
    return round(f2p * (0.9 + 0.1 * static) * (1.0 / (1.0 + n_regressions)), 4)


def touched_paths(patch: str) -> list[str]:
    out = [m.group(2) for m in re.finditer(r"^diff --git a/(\S+) b/(\S+)", patch, re.M)]
    if not out:
        out = [m.group(1) for m in re.finditer(r"^\+\+\+ b/(\S+)", patch, re.M)]
    return out


def check_integrity(patch: str) -> tuple[bool, str]:
    if not patch or not patch.strip():
        return False, "empty_patch"
    for p in touched_paths(patch):
        for pat in FORBIDDEN:
            if pat.search(p):
                return False, f"forbidden_path:{p}"
    for pat in SABOTAGE:
        if pat.search(patch):
            return False, f"sabotage:{pat.pattern[:30]}"
    return True, ""


# Test scope is defined once in pytest_scope.py; see that module for why each
# directory is excluded. Scoring and the baseline MUST agree on this.
sys.path.insert(0, str(Path(__file__).parent))
from pytest_scope import ignore_str  # noqa: E402

PYTEST_ALL = ("python -m pytest tests/ -q -p no:cacheprovider --tb=no -rf "
              + ignore_str())


def run_selected(be, nodeids: list[str]) -> dict[str, bool]:
    quoted = " ".join(f"'{n}'" for n in nodeids)
    r = be.sh(f"python -m pytest {quoted} -q -p no:cacheprovider --tb=no -rf")
    failed = set(FAIL_RE.findall(r.stdout))
    # A node erroring at collection never appears in FAILED; treat a non-zero
    # exit with no parseable result as total failure.
    if r.returncode != 0 and not failed:
        return {n: False for n in nodeids}
    return {n: (n not in failed) for n in nodeids}


def run_full(be) -> set[str]:
    return set(FAIL_RE.findall(be.sh(PYTEST_ALL).stdout))


def score_one(be, task: dict, patch: str, baseline: set) -> dict:
    res = {"instance_id": task["instance_id"], "task_type": task["task_type"],
           "difficulty": task["difficulty"], "score": 0.0, "f2p": 0.0, "p2p": 0.0,
           "static": 0.0, "gate": 1, "reason": ""}
    ok, why = check_integrity(patch)
    if not ok:
        res.update(gate=0, reason=why)
        return res

    be.reset()
    # Re-create the task's starting state (the injected defect).
    #
    # Patches are shipped into the container base64-encoded rather than through a
    # heredoc. A heredoc breaks if the diff happens to contain the delimiter, and
    # any raw diff is at the mercy of shell quoting; base64 is a single inert
    # token. The agent harness hit exactly this failure via its YAML config.
    be.sh(f"echo {b64(task['setup_patch'])} | base64 -d > /tmp/setup.patch")
    if be.sh("git apply --whitespace=nowarn /tmp/setup.patch").returncode != 0:
        res.update(gate=0, reason="setup_patch_failed")
        return res

    be.sh(f"echo {b64(patch)} | base64 -d > /tmp/model.patch")
    if be.sh("git apply --whitespace=nowarn /tmp/model.patch").returncode != 0:
        if be.sh("patch -p1 --batch --forward < /tmp/model.patch").returncode != 0:
            res.update(gate=0, reason="model_patch_failed")
            return res

    # Grading criteria are restored AFTER the model patch, so any edit an agent
    # made to tests/ is discarded before a single test runs.
    be.restore_tests()

    f2p_ids = task["f2p_tests"]
    res["f2p"] = sum(run_selected(be, f2p_ids).values()) / max(1, len(f2p_ids))

    regressions = (run_full(be) - baseline) - set(f2p_ids)
    total_ref = task.get("suite_size", 5584)
    res["p2p"] = max(0.0, 1.0 - len(regressions) / max(1, total_ref))  # reported only
    res["n_regressions"] = len(regressions)
    res["regressions"] = sorted(regressions)[:10]

    changed = [p for p in touched_paths(patch) if p.endswith(".py")]
    if changed:
        r = be.sh("ruff check --quiet " + " ".join(f"'{c}'" for c in changed))
        res["static"] = 1.0 if r.returncode == 0 else 0.0
    else:
        res["static"] = 1.0

    res["regression_factor"] = round(1.0 / (1.0 + len(regressions)), 4)
    res["score"] = compute_score(res["f2p"], res["static"], len(regressions))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True, type=Path)
    ap.add_argument("--preds", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--backend", choices=["docker", "local"], default="docker")
    ap.add_argument("--repo", type=Path, help="local backend only")
    ap.add_argument("--pristine", type=Path, help="local backend only")
    ap.add_argument("--baseline", type=Path)
    args = ap.parse_args()

    tasks = {json.loads(l)["instance_id"]: json.loads(l) for l in args.tasks.open()}
    preds = json.loads(args.preds.read_text())
    baseline = set(json.loads(args.baseline.read_text())) if args.baseline else set()

    results = []
    skipped = 0
    for iid, task in tasks.items():
        # An instance absent from preds.json was never attempted -- typically a
        # sliced/smoke run. Scoring it as 0 would silently dilute the mean with
        # tasks no model ever saw, which is a different thing from failing them.
        if iid not in preds:
            skipped += 1
            continue
        patch = (preds.get(iid) or {}).get("model_patch") or ""
        be = DockerBackend() if args.backend == "docker" else LocalBackend(args.repo, args.pristine)
        try:
            be.start(task["image_name"])
            r = score_one(be, task, patch, baseline)
        except Exception as e:
            r = {"instance_id": iid, "task_type": task["task_type"],
                 "difficulty": task["difficulty"], "score": 0.0, "gate": 0,
                 "reason": f"harness_error:{type(e).__name__}:{e}"}
        finally:
            be.stop()
        results.append(r)
        print(f"[score] {iid:34s} {r['score']:.3f} {r.get('reason','')}", file=sys.stderr, flush=True)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=2))

    if results:
        mean = sum(r["score"] for r in results) / len(results)
        solved = sum(1 for r in results if r["score"] >= 0.999)
        print(f"[score] n={len(results)} mean={mean:.4f} fully_solved={solved}"
              + (f" (skipped {skipped} unattempted)" if skipped else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
