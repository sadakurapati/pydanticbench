# PydanticBench

An **unsaturated evaluation benchmark for coding agents**, built from
[`pydantic/pydantic`](https://github.com/pydantic/pydantic) (~7,000 merged PRs).

100 tasks · one Docker image · automatic scoring in [0, 1] · driven by
[mini-swe-agent](https://mini-swe-agent.com)

> `REPORT.md` has the design rationale and findings. This file is the operating
> manual.

---

## Quick start

```bash
./run.sh
```

That is the whole thing. The script is interactive: it checks prerequisites,
installs dependencies, builds the Docker image, verifies the scorer, asks which
models to evaluate and how many tasks, shows a cost estimate before spending
anything, runs the benchmark, scores every run, and prints the results.

It is safe to re-run — each stage detects existing work and skips or resumes it.

**Start with the `smoke` scope** (5 tasks, ~10 min, a few dollars) to prove the
setup end to end before committing to a full run.

### What it will ask you

`run.sh` verifies four things before spending anything: every configured model
answers a one-token probe, the image baseline is green, all 100 task patches
apply inside the image, and the scorer passes its five controls.

| Prompt | Options | Notes |
|---|---|---|
| Provider | Anthropic / Gemini / Mixed | Anthropic is the default ladder |
| API key | hidden input | Never written to disk or shown on screen |
| Scope | smoke (5) / subset (25) / full (100) | Tasks per model |
| Workers | default 8 | Parallel containers |
| Proceed? | y/N | Shown after the cost estimate |
| Self-test? | y/N | ~2 min; strongly recommended |

### Non-interactive (CI, or a second run)

```bash
PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... \
SCOPE=full WORKERS=8 ASSUME_YES=1 ./run.sh
```

| Variable | Purpose |
|---|---|
| `PROVIDER` | `anthropic` / `gemini` / `mixed` |
| `SCOPE` | `smoke` / `subset` / `full` |
| `WORKERS` | Parallel containers (default 8) |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | Credentials |
| `ASSUME_YES` | Skip run confirmations. **Does not authorise software installs.** |
| `ALLOW_INSTALL` | Permit installing missing prerequisites, including `sudo` commands |
| `MODELS_OVERRIDE` | Replace the model list without editing the script, e.g. `MODELS_OVERRIDE="gemini/gemini-3.6-flash gemini/gemini-3-pro gemini/gemini-3.5-flash-lite"` |
| `FRESH` | Discard previous results and re-run from scratch |
| `PYBIN` | Use a specific Python interpreter, e.g. `/opt/homebrew/bin/python3.12` |
| `USE_VENV=0` | Install into the current environment instead of `.venv` |
| `SKIP_SELFTEST` | Skip scorer verification |
| `MSWEA_GLOBAL_COST_LIMIT` | Hard spend circuit-breaker |
| `NO_COLOR` | Plain output |

`ASSUME_YES` and `ALLOW_INSTALL` are deliberately separate. "Don't ask me about
running the benchmark" should not silently also mean "install system packages
with sudo".

### If a model is unavailable

The preflight tells the difference between *dead identifier* (404) and *out of
quota* (429), then shows what your key can actually use:

```
  Usable coding models for this key, best first:

    lite:
       1) gemini/gemini-3.5-flash-lite
    flash:
       2) gemini/gemini-3.6-flash
    pro:
       3) gemini/gemini-2.5-pro

  Recommended ladder: gemini/gemini-3.5-flash-lite gemini/gemini-3.6-flash gemini/gemini-2.5-pro

  Press Enter to accept, or enter three numbers (e.g. 1 4 7):
```

Image, audio, TTS, video, embedding and robotics models are filtered out; stable
releases rank above previews and newer versions above older. Cost estimates and
result directory names follow whatever you pick.

### Missing prerequisites

If Docker or a suitable Python is missing, `run.sh` detects your package manager
(Homebrew / apt / dnf), **shows the exact command it proposes to run**, and asks
permission before running anything. Decline and it prints manual instructions
instead. It can also offer to start Docker Desktop and wait for the daemon.

---

## 1. What you need

| Requirement | Why | Check |
|---|---|---|
| Docker, running | The evaluation environment | `docker info` |
| **Python ≥ 3.10** | mini-swe-agent requires it | `python3 --version` |
| ~6 GB free disk | Image + layers | `df -h .` |
| An LLM API key | To run agents | Anthropic and/or Gemini |
| Network to GitHub + PyPI | The image build clones pydantic | — |

Apple Silicon is fine: the image is built locally rather than pulled from the
SWE-bench x86 registry.

**macOS users:** the system `python3` at `/usr/bin/python3` is 3.9 and will not
work. `run.sh` searches for `python3.14 … python3.10` before falling back to
`python3`, so an installed Homebrew Python is found automatically even if it is
not first on `PATH`. Dependencies are installed into a project-local `.venv`,
never into your system Python.

## 2. Cost and runtime

Per-task caps are `step_limit: 150` and `cost_limit: $1.00`
(`configs/pydanticbench.yaml`), deliberately below mini-swe-agent's defaults of
250 / $3.00. Hitting a cap scores 0 and is reported as budget exhaustion.

| Scope | Tasks/model | Wall clock @ 8 workers | Est. cost (Anthropic ladder) |
|---|---:|---|---:|
| smoke | 5 | ~10 min | ~$8 |
| subset | 25 | ~40 min | ~$40 |
| full | 100 | ~2.5 h | ~$110 |

Projections from the configured caps, not quotes. `run.sh` prints an itemised
estimate and waits for confirmation before spending anything, and sets
`MSWEA_GLOBAL_COST_LIMIT` as a hard circuit-breaker.

## 3. Reading the results

`results/summary.md` has four tables. **The by-tier table is the one that
matters.**

- Monotonic decay easy → medium → hard, strongest model well under 50% on hard:
  the benchmark discriminates.
- All models clustered high: it is saturated. Tighten `MAX_FAIL` in
  `scripts/03_validate_tasks.py` from `4` toward `2` and regenerate (§6).

Also watch **gate trips** — patches that touched grading criteria. Non-zero means
a model tried to redefine the problem instead of solving it.

Score formula (`scripts/05_score.py`):

```
score = gate × F2P × (0.9 + 0.1 × S) × 1/(1 + R)
```

`F2P` = fraction of the task's broken tests now passing (partial credit lives
here) · `R` = tests the patch broke · `S` = ruff clean · `gate` = 0 if the patch
is empty, fails to apply, or touches tests/config.

Instances absent from `preds.json` are **skipped, not scored zero** — so a
5-task smoke run reports on 5 tasks, not on 100.

## 4. Verifying before you trust the numbers

`run.sh` offers this automatically; to run it directly:

```bash
python3 scripts/07_selftest.py --backend docker --image pydanticbench:base -n 2
```

Five known-value controls:

| Control | Checks |
|---|---|
| `formula` | The scoring arithmetic against six exact cases. No container needed, so a failure here means the formula, not the environment. |
| `oracle` | A correct patch scores 1.000. |
| `noop` | An empty patch scores 0.000. |
| `cheat` | A patch editing `tests/` scores 0.000 with the gate tripped. |
| `half` | Half a fix never earns full marks. |

Each control prints per-task detail — score, fail-to-pass fraction, regression
count, gate reason — so a failure tells you *which* task did *what*. Status in
`results/selftest.md`. **All five pass.**

A half-fix scoring exactly 0.000 is correct, not a bug: multi-hop tasks are
filtered so that no single half of the fix restores the failing tests.

## 5. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Docker is installed but the daemon is not running` | Say yes when offered, or start Docker Desktop manually. |
| `Could not find a version that satisfies the requirement mini-swe-agent (from versions: none)` | Your Python is too old. `from versions: none` means no release matches this interpreter — mini-swe-agent needs **≥ 3.10**, and macOS ships 3.9 at `/usr/bin/python3`. Install a newer one (`brew install python@3.12`) or point the script at an existing one: `PYBIN=/opt/homebrew/bin/python3.12 ./run.sh`. |
| `no such option: --break-system-packages` | Same root cause: that flag needs pip ≥ 23.0, so an old pip means an old Python. Fix as above. |
| `error: externally-managed-environment` | `run.sh` installs into `.venv` and should not hit this. If you used `USE_VENV=0`, drop it. |
| `could not create a virtualenv` | The venv module is missing. On Debian/Ubuntu: `sudo apt install python3-venv` — the script offers this. |
| Image build reports test failures | The build now **fails** rather than shipping a broken image. Read the printed pytest output. `pytest` is pinned to 8.3.5 because newer versions turn pydantic-internal warnings into collection errors — do not unpin. |
| `ModuleNotFoundError: No module named 'hypothesis'` | `tests/pydantic_core` exists only in the git checkout (not the sdist the tasks were built from) and is hypothesis-based property testing — randomised, so unusable for a reproducible baseline. It is excluded via `scripts/pytest_scope.py` and `PYTEST_ADDOPTS` in the image. Rebuild. |
| `unrecognized arguments: --benchmark-columns` | `pytest-benchmark` is missing. pydantic's `pyproject.toml` puts benchmark flags in `addopts`, so pytest refuses to start without the plugin even though `tests/benchmarks` is excluded. Fixed in the current Dockerfile; rebuild with `docker rmi pydanticbench:base && ./run.sh`. |
| Self-test `oracle` scores 0.000 | The image's test suite cannot run — a correct patch is being graded against a broken environment. Check the baseline: `docker run --rm pydanticbench:base bash -lc "cd /testbed && python -m pytest tests/ -q --ignore=tests/test_docs.py --ignore=tests/benchmarks \| tail -3"`. Rebuild if it is not green. |
| `could not run the test suite inside the image at all` | The image is unusable. `docker rmi pydanticbench:base && ./run.sh`. |
| `the image baseline is NOT green` | The image is unusable for scoring. Rebuild: `docker rmi pydanticbench:base && ./run.sh`. |
| Every task scores 0 with `setup_patch_failed` | Image built from a different tag than tasks were generated against. Rebuild with `--build-arg BASE_TAG=v2.13.4`. |
| `harness_error:` in scores | Docker could not start a container. Check `docker info` and free disk. |
| Runs stall at "initializing task" | Docker is pulling/building. Later runs are fast. |
| Scores look implausibly high | Check `preds.json` for patches touching `tests/`, and confirm the self-test passes. |
| Want to re-score without re-running agents | Re-run `scripts/05_score.py` against the existing `preds.json`. Scoring is independent of inference. |
| `run.sh: line NNN: /Users/you/Library/Application: No such file or directory` | A path-with-spaces bug. Fixed — the agent command is held in a bash array rather than a string, so word splitting cannot tear it at `Application Support`. If you see this again, check for any unquoted `$VAR` you added. |
| Every task scores 0.000 with reason `empty_patch`, and trajectories show the agent solving the problem | The injected defect must be **committed** during task setup, otherwise HEAD is the clean tree and a correct fix produces an empty `git diff`. Fixed in `configs/pydanticbench.yaml` (the startup command ends with `git commit`). If you changed that command, put the commit back. |
| Many tasks end in `LimitsExceeded` | Raise `step_limit` in `configs/pydanticbench.yaml`. It is 150; `cost_limit` remains the real spend ceiling. |
| All models score 0.000 and the log says `Skipping N existing instances` | A previous failed run left `preds.json` behind, and mini-swe-agent counts any recorded instance as done — including ones that crashed. `run.sh` now detects this (all patches empty = failed run, not a bad model) and offers to clear it. Force it with `FRESH=1 ./run.sh`, or `rm -rf results/<model>`. |
| `FileNotFoundError: ... preds.json` after a failed run | The agent produced no predictions, so there was nothing to score. `run.sh` now skips scoring for that model and reports it instead of crashing. Read the agent error above it. |
| `NotFoundError: model ... is no longer available` | The provider retired that identifier. `run.sh` preflights every model before building anything; if one is dead it lists what your key *can* reach, ranked into lite/flash/pro, and offers a ladder to accept or override. Nothing is spent. |
| `RateLimitError` / 429 during the model check | The model exists but the key is out of quota. It is offered rather than discarded, since quota often recovers — but pick a different tier if it persists. |
| Need to change which models run | Edit the `case "$PROVIDER"` block in `run.sh`. Any litellm model string works. |

## 6. Running stages manually

`run.sh` orchestrates these; run them directly only if you need to.

```bash
# build the environment
docker build -f docker/Dockerfile --build-arg BASE_TAG=v2.13.4 -t pydanticbench:base .

# run one model over the first 5 tasks
mini-extra swebench --subset tasks/hf --split train \
  --config configs/pydanticbench.yaml --environment-class docker \
  --model anthropic/claude-haiku-4-5-20251001 \
  --slice 0:5 --workers 2 --output results/smoke

# score and report
python3 scripts/05_score.py --tasks tasks/tasks.jsonl \
  --preds results/smoke/preds.json --baseline tasks/baseline_failures.json \
  --backend docker --out results/smoke/scores.json
python3 scripts/06_report.py --results results --out results
```

### Regenerating the task set

Only needed to produce a *different* benchmark; `tasks/tasks.jsonl` ships
generated and validated.

```bash
python3 scripts/02_generate_tasks.py  --repo /path/to/pydantic --out tasks/candidates.jsonl
python3 scripts/03_validate_tasks.py  --repo /path/to/pydantic --pristine /path/to/pristine \
    --candidates tasks/candidates.jsonl --out tasks/tasks_pool.jsonl \
    --rejects tasks/candidates_rejected.jsonl --target 250
python3 scripts/03c_verify_multihop.py --repo /path/to/pydantic --pristine /path/to/pristine
python3 scripts/03b_select_tasks.py
```

`/path/to/pristine` is an untouched copy of the source tree, used to revert
between candidates. Generation is seeded (`--seed 20260811`), so the same inputs
reproduce the same task set. Stage 3 is resumable via `--time-budget`.

## 7. File map

```
pydanticbench/
├── run.sh                          ← single interactive entry point
├── .gitignore                      excludes .venv/, results/, __pycache__
├── README.md                       this file
├── REPORT.md                       the written report
├── PLAN.md                         design document + execution log
├── AI_DECLARATION.md               declaration of AI assistance
├── docker/Dockerfile               the evaluation environment
├── bench/                          in-container helpers (reset, restore tests)
├── configs/pydanticbench.yaml      mini-swe-agent config: prompts, limits, setup hook
├── scripts/
│   ├── 00_validate_repo.py         repo suitability gate
│   ├── mutation_ops.py             libcst mutation operators
│   ├── prompt_sanitize.py          redaction keeping prompts symptom-only
│   ├── 02_generate_tasks.py        candidate generation
│   ├── 03_validate_tasks.py        validation, acceptance filter, prompt synthesis
│   ├── 03b_select_tasks.py         stratified selection of the final 100
│   ├── 03c_verify_multihop.py      demotes fake multi-hop tasks
│   ├── 04_run_benchmark.sh         non-interactive runner (run.sh wraps this logic)
│   ├── 05_score.py                 automatic scoring (docker + local backends)
│   ├── 06_report.py                aggregation into result tables
│   ├── 07_selftest.py              scorer verification (5 controls)
│   ├── 08_verify_tasks_apply.py    confirms every task applies inside the image
│   ├── 09_check_models.py          model availability check + interactive picker
│   └── pytest_scope.py             single source of truth for the test scope
├── tasks/
│   ├── tasks.jsonl                 the 100 tasks
│   ├── hf/train.jsonl              same set, in mini-swe-agent's --subset layout
│   ├── tasks_pool.jsonl            all validated tasks (unselected = reserve)
│   ├── candidates.jsonl            the 400 generated candidates
│   ├── candidates_rejected.jsonl   every rejection, with reason
│   └── baseline_failures.json      pre-existing failures, excluded from scoring
├── results/selftest.md             scorer verification status
└── logs/                           chat transcript (see AI_DECLARATION.md)
```

## 8. Task format

Each line of `tasks/tasks.jsonl` is one instance — SWE-bench-compatible fields
plus benchmark-specific ones:

| Field | Meaning |
|---|---|
| `instance_id` | Unique id; encodes family and tier |
| `problem_statement` | **The prompt.** A symptom report — never names the file to fix |
| `setup_patch` | **The starting state.** Injects the defect at agent start |
| `reference_patch` | Known-good fix. Validation and self-test only; never shipped into the image |
| `f2p_tests` | Tests that must go from failing to passing |
| `task_type` | `T1` single mutation · `T2` multi-hop · `T3` reimplementation |
| `difficulty` | `easy` / `medium` / `hard`, assigned by construction |
| `image_name` | Docker image for this instance |
