# PydanticBench — Plan & Design Document

**Goal:** build an unsaturated coding-agent evaluation benchmark from a large real-world codebase.
**Author:** Sada Kurapati
**Time budget:** 8 hours, single operator.

> **Read this first.** Sections 1–13 are the plan **as frozen before any code was
> written**, kept unedited so the reasoning can be judged against what actually
> happened. Several decisions in them were later overturned by measurement — the
> scoring formula (§7) and the task taxonomy (§5) most significantly.
>
> **§14 and §15 record every deviation, with the evidence that forced it.**
> **`REPORT.md` is the authoritative description of what was built.**
> Where this document and the report disagree, the report is correct.

---

## 1. Executive summary

We convert **`pydantic/pydantic`** into a 100-task coding-agent benchmark called **PydanticBench**, packaged as a single Docker image plus a SWE-bench-format task file, evaluated with **mini-swe-agent** against three Anthropic model tiers (Haiku 4.5 / Sonnet 5 / Opus 5).

The design is driven by one constraint that dominates all others: **the benchmark must be unsaturated.** A benchmark on which all three models score 90% has no discriminative power and is worthless regardless of engineering quality. Every major design decision below — repo choice, task generation strategy, difficulty controls, scoring formula — is downstream of that.

The second constraint is the 8-hour wall clock. This forces a specific architectural choice (single base commit → single image → programmatic task synthesis) that we would *not* make with a week available. §11 documents what we would do differently with more time, since scaling is the question that matters most for a benchmark's shelf life.

---

## 2. Requirements traceability

| # | Assignment requirement | How satisfied | Deliverable |
|---|---|---|---|
| R1 | Public GitHub repo, ≥1,000 merged PRs | `pydantic/pydantic` (~7k merged PRs; verified in step 0) | §3 |
| R2 | Convert repo into evaluation environment(s) | Docker image `pydanticbench:base`, pinned base commit, hermetic | §4 |
| R3 | Env supports automatic evaluation | `scripts/05_score.py` runs hidden tests in-container, emits JSON | §4, §7 |
| R4 | 100 evaluation tasks | *planned* 6 families → **shipped 3** (T1 86 · T2 5 · T3 9), `tasks/tasks.jsonl` — see §14.2, §15.1 | §5, §6 |
| R5 | Each task has starting state + prompt | `setup_patch` (state) + `problem_statement` (prompt) per instance | §6.3 |
| R6 | Automatic scoring → [0, 1] | *planned* additive composite → **shipped multiplicative** with integrity gate — see §14.2 | §7 |
| R7 | ≥3 models via mini-swe-agent | Anthropic or Gemini ladder, selected at runtime with a live availability check (`run.sh`, `scripts/09_check_models.py`) | §8 |
| R8 | Report: env, tasks, scoring, results, shortcomings, scaling | `REPORT.md` | §10, §11 |
| R9 | AI-use declaration + chat logs | `AI_DECLARATION.md` + `logs/` | §12 |

---

## 3. Repository selection

### 3.1 Criteria

Selection was scored against six criteria, in priority order:

1. **Not in SWE-bench / SWE-gym / SWE-smith.** This is the single most important criterion. The 12 SWE-bench repos (django, sympy, astropy, matplotlib, pytest, requests, scikit-learn, sphinx, xarray, flask, pylint, seaborn) appear verbatim in the training data and public evaluation traces of every frontier model. Building on them would produce inflated scores that measure memorization rather than capability.
2. **≥1,000 merged PRs** (hard requirement).
3. **Fast, hermetic test suite.** No network, no database, no GPU. Must run in minutes, not tens of minutes — we will execute it hundreds of times.
4. **No heavy compiled build.** Rust/C++ core built from source would consume the entire time budget in Docker builds.
5. **Genuine difficulty.** The code must be hard enough that frontier models fail a meaningful fraction of tasks.
6. **Tests colocated with source changes**, so PR-derived tasks yield clean fail-to-pass sets.

### 3.2 Decision: `pydantic/pydantic`

| Criterion | Assessment |
|---|---|
| SWE-bench overlap | **None.** Clean. |
| Merged PRs | ~7,000 (verify in step 0) |
| Test suite | Pure-Python, hermetic, no external services |
| Build cost | `pydantic-core` ships as a **prebuilt wheel** — no Rust toolchain, no compile step |
| Difficulty | High. Validator/serializer resolution, generics, `__get_pydantic_core_schema__`, forward refs, and the v1→v2 compatibility shim are subtle, non-local, and genuinely hard to reason about |
| Architecture | Multi-layer (Python API → core schema → Rust core), so bugs propagate non-locally — excellent for hard tasks |

**Bonus:** because we build our own image rather than pulling `swebench/sweb.eval.x86_64.*`, we are not pinned to x86 and the image builds natively on Apple Silicon.

### 3.3 Step-0 verification gates (must pass before proceeding)

These are assumptions, not facts, until measured. `scripts/00_validate_repo.py` checks:

- [ ] Merged PR count ≥ 1,000 (GitHub API)
- [ ] Full `pytest` suite green at chosen base commit
- [ ] Full suite wall-clock < 6 min with `-n auto` (xdist)
- [ ] Suite passes with `--network none`
- [ ] No flaky tests across 2 consecutive runs (record any; quarantine them)

**Fallback if a gate fails:** switch to `encode/httpx` (same profile, smaller). Decision must be made by **T+0:30** — do not debug past that point.

### 3.4 Base commit

A single pinned commit — a release tag, e.g. `v2.x.y` — is used for **all** synthetic tasks. This is the key 8-hour optimization: one commit means one dependency install, one Docker layer, and zero per-task environment setup. Pinned as the `BASE_TAG` build argument in `docker/Dockerfile` (`v2.13.4`). *(The plan proposed a separate `configs/base_commit.txt`; that file was never needed once the tag became a build arg.)*

---

## 4. Evaluation environment

### 4.1 Architecture

```
┌─────────────────────────────────────────────────────────┐
│ pydanticbench:base  (single image, built once)          │
│                                                          │
│  /testbed/            pydantic @ BASE_COMMIT (git repo)  │
│  /opt/venv/           deps installed, pydantic -e        │
│  /opt/pristine/       tarball of the ORIGINAL tests/     │
│  /opt/bench/          scoring helpers                    │
└─────────────────────────────────────────────────────────┘
             │
             │  per task, at agent start:
             │    git checkout -f BASE && git clean -fdx
             │    git apply /opt/bench/setup/<id>.patch
             ▼
      agent works, emits a git diff
             │
             │  per task, at scoring time (fresh container):
             │    apply setup_patch, then apply model_patch
             │    restore tests/ from /opt/pristine  ← anti-tamper
             │    run F2P set, P2P set, static checks
             ▼
        score ∈ [0, 1]
```

### 4.2 Design decisions and rationale

**One image, not one hundred.** All synthetic tasks share `BASE_COMMIT`. Task-specific starting state is delivered by a small `setup_patch` applied via `run.env_startup_command`, which mini-swe-agent renders with Jinja against the instance dict. Saves an estimated 3+ hours of build time and tens of GB of disk.

**Hidden tests.** The tests that determine the score are **removed or never present** in the agent's working copy for task families that require it, and are restored from `/opt/pristine/tests.tar.gz` immediately before scoring. The agent cannot read, edit, or delete the grading criteria.

**Git history scrubbing.** The container's clone has its remote stripped and history truncated to the base commit. Without this, an agent can `git log`, `git diff HEAD~1`, or fetch the upstream fix and trivially recover the answer. **This is the most commonly missed anti-cheat in homegrown benchmarks.**

**Network isolation at scoring time.** Scoring containers run `--network none`. The agent's working container permits network (models routinely try `pip install`), but scoring must be reproducible and un-exfiltrable.

**Determinism.** `PYTHONHASHSEED=0`, `-p no:randomly`, `-p no:cacheprovider`, fixed `TZ=UTC`, `SOURCE_DATE_EPOCH` pinned.

### 4.3 Dockerfile sketch

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

ARG BASE_COMMIT
WORKDIR /testbed

RUN git clone https://github.com/pydantic/pydantic.git . && \
    git checkout ${BASE_COMMIT}

# Deps first (cached layer), then editable install
RUN pip install --no-cache-dir uv && \
    uv pip install --system -r requirements/all.txt || \
    pip install --no-cache-dir -e ".[all]" && \
    pip install --no-cache-dir pytest pytest-xdist dirty-equals \
                               jsonschema ruff mypy

# Pristine snapshot of tests for anti-tamper restore
RUN mkdir -p /opt/pristine && tar czf /opt/pristine/tests.tar.gz tests/

# Scrub history so the fix is not recoverable from git
RUN git remote remove origin && \
    git checkout --detach ${BASE_COMMIT} && \
    echo "${BASE_COMMIT}" > /opt/base_commit.txt

ENV PYTHONHASHSEED=0 TZ=UTC PIP_PROGRESS_BAR=off TQDM_DISABLE=1
COPY bench/ /opt/bench/
```

> Exact dependency install line is confirmed empirically in step 0 — pydantic's dev setup has changed across versions (`make install`, `uv sync`, `requirements/`). Do not guess; run it.

---

## 5. Task design

### 5.1 The unsaturation problem

The naive approach — scrape N issues, use the PR's tests as the grader — reproduces SWE-bench. Frontier models now score >70% on SWE-bench Verified, so a faithful clone would be saturated on arrival.

We attack saturation on four axes:

| Lever | Mechanism |
|---|---|
| **Contamination** | Non-SWE-bench repo; synthetic bugs that exist in no training corpus |
| **Hint suppression** | Prompts describe *symptoms*, never file paths, function names, or test names |
| **Non-locality** | Multi-file mutations where the fix site ≠ the failure site |
| **Task diversity** | Six families, only one of which is standard bug-fixing |

The strongest of these is **synthetic mutation**: a bug we generate today cannot be in any model's training data, and its difficulty is a tunable parameter rather than an accident of what happened to be filed on GitHub.

### 5.2 Task families (100 tasks)

| ID | Family | N | Starting state | Grading signal |
|---|---|---:|---|---|
| **T1** | Mutation repair | 34 | 1 semantic mutation injected into source | F2P tests + P2P regression |
| **T2** | Multi-hop mutation repair | 12 | 2 interacting mutations in different modules | F2P + P2P |
| **T3** | Function reimplementation | 18 | Function body replaced with `raise NotImplementedError` | F2P + P2P |
| **T4** | Real PR bug fix | 15 | Repo at PR parent commit + issue text | PR's F2P tests + P2P |
| **T5** | Test authoring | 11 | Known-buggy build; agent must write a *test* | Mutation-scored (see §7.3) |
| **T6** | Refactor / typing / config under invariant | 10 | Working code + a structural constraint | Full suite green + AST/static assertion |
| | **Total** | **100** | | |

**T4 is the cutline.** If the schedule slips, T4 (real PRs — the only family requiring multiple commits and therefore multiple dependency environments) is dropped and T1/T3 are expanded to compensate. Documented as a limitation rather than hidden.

### 5.3 Mutation operators (T1/T2)

Implemented over `libcst` (preserves formatting, so diffs are minimal and the mutation is not visually obvious):

| Operator | Example |
|---|---|
| Comparison swap | `if n < limit` → `if n <= limit` |
| Boolean flip | `a and b` → `a or b` |
| Guard removal | delete an early-return/`raise` guard |
| Constant perturbation | `0` → `1`, `None` → `False` |
| Default-arg change | `strict: bool = False` → `= True` |
| Branch swap | exchange `if`/`else` bodies |
| Off-by-one | `x[i:]` → `x[i+1:]` |
| Arg-order swap | `f(a, b)` → `f(b, a)` (same-type args only) |
| Exception-type swap | `ValueError` → `TypeError` |

### 5.4 Mutation acceptance filter — the core difficulty control

A generated mutation is **accepted as a task only if**:

- The code still imports and parses (no syntax/import errors).
- It causes **≥1 and ≤4** test failures. *This is the key knob.* A mutation that breaks 200 tests is trivially localized by anyone who reads the traceback. A mutation that breaks 0 tests is an equivalent mutant and unscoreable. The 1–4 band selects for subtle, specific, genuinely hard bugs.
- The mutation site is **not** in a file whose name appears in the failing test's own module name (weak proxy for non-locality; forces cross-module reasoning).
- The failure is deterministic across 2 runs.

Mutations are sampled from files weighted by cyclomatic complexity, so we hit dense logic rather than boilerplate.

### 5.5 Prompt construction

Prompts are **symptom reports**, not bug reports with answers attached. Generated by sanitizing the pytest failure output:

- Strip all file paths, line numbers, test names, and function names.
- Keep the observable behavioral delta (expected vs. actual value).
- Render as a plausible user issue.

Example (T1):

> When a model field is annotated with a constrained integer type and instantiated with a value exactly equal to the upper bound, validation raises a `ValidationError` where the value should be accepted. Values below the bound behave correctly. Locate the cause and fix it. Do not modify any test files.

The agent gets no pointer to the file, the function, or the test. This is deliberately harder than SWE-bench, where issue text frequently contains a traceback naming the exact frame.

### 5.6 Difficulty tiers

Every task carries a `difficulty` label, assigned by construction, enabling per-tier reporting:

| Tier | Definition | Target N |
|---|---|---:|
| `easy` | Single mutation, symptom names the public API surface | 30 |
| `medium` | Single mutation, non-local, API surface not named | 45 |
| `hard` | Multi-hop, or body-removal with docstring stripped, or test-authoring | 25 |

Reporting score-by-tier is the primary evidence that the benchmark is unsaturated: a healthy benchmark shows monotonic degradation across tiers and a hard tier where the best model is well under 50%.

---

## 6. Task generation pipeline

### 6.1 Scripts

| Script | Purpose | Runtime |
|---|---|---|
| `scripts/00_validate_repo.py` | Verify §3.3 gates, pick base commit | ~15 min |
| `scripts/01_build_image.sh` | Build `pydanticbench:base` | ~10 min (background) |
| `scripts/02_generate_tasks.py` | Mutation engine + body-removal + PR miner → candidate tasks | ~20 min |
| `scripts/03_validate_tasks.py` | Run each candidate in-container; keep those passing §5.4 filter; emit `tasks.jsonl` | ~60 min (parallel, background) |
| `scripts/04_run_benchmark.sh` | Drive mini-swe-agent × 3 models | ~2.5 h (background) |
| `scripts/05_score.py` | Apply patches, run hidden tests, emit scores | ~30 min |
| `scripts/06_report.py` | Tables + plots → `results/` | ~10 min |

### 6.2 Validation is the expensive step

`03_validate_tasks.py` must run the affected test subset once per candidate mutation. Generate **~250 candidates** to yield 100 accepted tasks (expect ~40% acceptance after the §5.4 filter). With 8 parallel containers and a targeted test subset (not the full suite) per candidate, this fits in ~60 minutes of unattended wall clock.

**Critical optimization:** do not run the full suite per candidate. Build a coarse module→test map once, then run only the plausibly-affected subset for the accept/reject decision. Run the full suite only on the 100 accepted tasks, once, to compute the P2P baseline.

### 6.3 Instance schema

SWE-bench-compatible (so `mini-extra swebench --subset ./tasks` works unmodified), plus custom fields:

```json
{
  "instance_id": "pydanticbench__t1_0042",
  "repo": "pydantic/pydantic",
  "base_commit": "<BASE_COMMIT>",
  "problem_statement": "When a model field is annotated with ...",
  "image_name": "pydanticbench:base",

  "task_type": "T1",
  "difficulty": "medium",
  "setup_patch": "diff --git a/pydantic/... (injects the bug)",
  "reference_patch": "diff --git ... (the inverse; for validation only)",
  "f2p_tests": ["tests/test_types.py::test_constrained_int_upper_bound"],
  "p2p_tests": ["tests/test_types.py", "tests/test_main.py"],
  "static_checks": [],
  "weights": {"f2p": 0.6, "p2p": 0.3, "static": 0.1}
}
```

`reference_patch` is used only to prove the task is solvable during validation. It is **never** shipped inside the image.

---

## 7. Scoring

### 7.1 Formula

For a submitted patch `P` on task `t`:

```
score(t, P) = gate(t, P) × [ w_f2p · F2P + w_p2p · P2P + w_static · S ]

    ^^ SUPERSEDED. This additive form awarded 0.40 to a patch that did nothing,
       measured on the no-op control. The shipped formula is multiplicative:
           score = gate × F2P × (0.9 + 0.1·S) × 1/(1 + R)
       See §14.2 and REPORT.md §4.
```

where

- **F2P** = (# fail-to-pass tests now passing) / (# fail-to-pass tests). Partial credit lives here.
- **P2P** = (# regression tests still passing) / (# regression tests). Guards against fixing the bug by breaking everything else.
- **S** ∈ {0, 1} = task-specific static assertions (ruff clean on changed files; AST invariant for T6; empty set → S = 1).
- **gate** ∈ {0, 1} — see below.
- Default weights (0.6, 0.3, 0.1), overridable per task family.

Result is in [0, 1] by construction.

### 7.2 Integrity gate

`gate = 0` if **any** of:

- The patch touches any path under `tests/` or any `conftest.py`.
- The patch modifies `pyproject.toml`, `setup.cfg`, `pytest.ini`, or CI config.
- The patch adds `pytest.mark.skip`, `xfail`, `sys.exit`, or monkeypatches the test runner.
- The patch is empty or fails to apply.
- Any P2P test **errors on collection** (a common way to fake a green run).

Test files are restored from `/opt/pristine/tests.tar.gz` before every scoring run regardless, so tampering cannot succeed even if the gate is somehow evaded. Belt and braces: the gate exists to make cheating *visible in the results*, not merely ineffective.

### 7.3 T5 (test authoring) — special case

The agent must write a test that detects a known bug. Scored by mutation, not by string matching:

```
score = 0.5 · [new test FAILS on the buggy build]
      + 0.5 · [new test PASSES on the fixed build]
      − 0.25 · [penalty if the test also fails on ≥3 unrelated healthy builds]
```

clamped to [0, 1]. The penalty term rejects the degenerate strategy of writing `assert False`, which would otherwise score 0.5.

### 7.4 Aggregate metrics reported

- Mean score over 100 tasks (primary)
- Resolve rate (fraction with score = 1.0)
- Mean score by task family and by difficulty tier
- Cost per task, steps per task, and **budget-exhaustion rate** (tasks where the agent hit `step_limit`/`cost_limit` — a legitimate signal of difficulty)
- Integrity-gate trip rate per model (does any model try to cheat?)

That last metric is worth its own paragraph in the report; it is not standard and it is interesting.

---

## 8. Benchmark execution

### 8.1 mini-swe-agent integration

No fork required. mini-swe-agent's batch runner accepts any `datasets`-loadable path via `--subset`, reads `image_name` off each instance to select the container, and renders `run.env_startup_command` with Jinja against the instance dict. Our per-task setup rides in on that hook.

`configs/pydanticbench.yaml` (derived from the shipped `swebench.yaml`):

```yaml
run:
  env_startup_command: >
    cd /testbed &&
    git checkout -f {{ base_commit }} &&
    git clean -fdx &&
    printf '%s' '{{ setup_patch }}' | git apply -

agent:
  step_limit: 80        # down from default 250 — cost control
  cost_limit: 1.00      # down from default 3.00

environment:
  cwd: /testbed
  timeout: 120
  environment_class: docker
  env:
    PAGER: cat
    PYTHONHASHSEED: "0"
    PIP_PROGRESS_BAR: "off"
    TQDM_DISABLE: "1"
```

Instance template is inherited from the stock SWE-bench config, with the "DO NOT MODIFY tests" boundary language retained and strengthened.

### 8.2 Model configurations (≥3)

| Config | Model | Rationale |
|---|---|---|
| C1 | `anthropic/claude-haiku-4-5-20251001` | Capability floor |
| C2 | `anthropic/claude-sonnet-5` | Mid tier |
| C3 | `anthropic/claude-opus-5` | Capability ceiling |
| C4 *(if time)* | Sonnet 5 @ `step_limit: 30` | Scaffold ablation — isolates budget from capability |

C4 is a stretch goal; C1–C3 satisfy the requirement.

### 8.3 Cost & time projection

| Config | $/task (est.) | 100 tasks | Wall clock @ 8 workers |
|---|---:|---:|---:|
| Haiku 4.5 | $0.08 | ~$8 | ~35 min |
| Sonnet 5 | $0.55 | ~$55 | ~50 min |
| Opus 5 | $1.00 (capped) | ~$90 | ~70 min |
| **Total** | | **~$155** | **~2.6 h** |

Caps make the Opus figure a near-hard ceiling. Set `MSWEA_GLOBAL_COST_LIMIT` as a circuit breaker.

**Runs execute in the background while the report is written.** They must be launched by **T+5:00**.

---

## 9. Eight-hour execution schedule

Boldface items are on the critical path. Background items run unattended.

| Time | Task | Blocking? | Notes |
|---|---|---|---|
| **T+0:00 – 0:30** | **Step 0: repo validation** (§3.3) | ✅ | Hard gate. If failed → switch to httpx, no debugging past 0:30 |
| T+0:15 – 0:45 | Docker image build | background | Launch as soon as base commit is chosen |
| **T+0:30 – 1:45** | **Write mutation engine + generators** (`02`) | ✅ | Largest coding block |
| **T+1:45 – 2:15** | **Write task validator** (`03`) | ✅ | |
| T+2:15 – 3:15 | Candidate validation run (~250 candidates) | background | Unattended; ~60 min |
| **T+2:15 – 3:15** | **Write scorer** (`05`) — in parallel with above | ✅ | |
| T+3:15 – 3:45 | Assemble `tasks.jsonl`; verify N = 100 and tier balance | ✅ | Top up from spare candidates if short |
| **T+3:45 – 4:15** | **Smoke test: 3 tasks × Sonnet, end to end** | ✅ | **Do not skip.** Catches env/config/scoring breakage before spending $150 |
| T+4:15 – 4:45 | Fix whatever the smoke test broke | ✅ | Buffer, and it will be needed |
| **T+4:45 – 5:00** | **Launch all 3 model runs** | ✅ | **Hard deadline** |
| T+5:00 – 7:00 | Runs execute | background | ~2.6 h projected; overlaps with writing |
| T+5:00 – 6:45 | **Write `REPORT.md`** (everything except results) | ✅ | Env, task design, scoring, shortcomings, scaling |
| T+7:00 – 7:30 | Score all runs; generate tables/plots (`05`, `06`) | ✅ | |
| T+7:30 – 7:45 | Paste results into report; write results analysis | ✅ | |
| T+7:45 – 8:00 | Package: repo tree, README, AI declaration, chat logs | ✅ | |

### 9.1 Cutlines (apply in order if behind schedule)

1. **Drop T4** (real PR tasks) — saves ~45 min, removes the only multi-commit environment work. Redistribute to T1/T3.
2. **Drop T6** (refactor/typing) — saves ~20 min of bespoke static checks.
3. **Cut to 60 tasks** and state so explicitly. *A working 60-task benchmark beats a broken 100-task one.* Note the deviation prominently in the report.
4. **Drop C4 ablation.**
5. **Drop Opus**, run Haiku + Sonnet + Sonnet-low-budget for three configs. Cheapest way to still satisfy R7.

Never cut: the smoke test, the integrity gate, or the report.

---

## 10. Deliverables layout

```
pydanticbench/
├── README.md                  # 5-min quickstart: build, generate, run, score
├── REPORT.md                  # ← the written report
├── PLAN.md                    # this document
├── AI_DECLARATION.md          # §12
├── docker/
│   └── Dockerfile
├── configs/
│   ├── pydanticbench.yaml     # mini-swe-agent config
│   └── base_commit.txt
├── scripts/
│   ├── 00_validate_repo.py
│   ├── 01_build_image.sh
│   ├── 02_generate_tasks.py   # mutation engine, body removal, PR miner
│   ├── 03_validate_tasks.py
│   ├── 04_run_benchmark.sh
│   ├── 05_score.py
│   └── 06_report.py
├── bench/                     # copied into the image
│   ├── restore_tests.sh
│   └── run_tests.py
├── tasks/
│   ├── tasks.jsonl            # the 100 instances
│   └── candidates_rejected.jsonl   # kept for transparency
├── results/
│   ├── haiku/ sonnet/ opus/   # trajectories + preds.json
│   ├── scores.csv
│   └── figures/
└── logs/
    └── ai-session-transcript.md
```

### 10.1 Report outline (`REPORT.md`)

1. **Overview** — repo choice and the contamination argument (½ page)
2. **Environment** — image architecture, hermeticity, anti-cheat measures
3. **Task design** — six families, mutation operators, the 1–4 failure acceptance band, difficulty tiers
4. **Scoring** — formula, integrity gate, T5 special case
5. **Results** — table of 3 configs × {mean score, resolve rate, by-tier, by-family, cost, budget exhaustion, gate trips}; the unsaturation argument
6. **Shortcomings** — honest list (§11.1)
7. **Scaling & improvements** (§11.2)
8. **AI use declaration**

---

## 11. Known limitations and scaling

### 11.1 Shortcomings to state honestly in the report

- **Synthetic bugs are not real bugs.** Mutation-injected defects have a different distribution from human-authored ones: they are single-edit, syntactically local, and always have a minimal inverse fix. Real bugs involve design errors, missing cases, and misunderstood requirements. Our benchmark measures *debugging under uncertainty*, not *engineering judgment*. T4 partially compensates; the sample is small.
- **Single repository.** Results are pydantic-specific and may not transfer. Cross-repo generalization is untested.
- **Single base commit** for 90% of tasks means limited codebase-state diversity.
- **Test suites are an imperfect oracle.** A patch can pass F2P and P2P while being poor code; conversely a legitimate alternative fix may fail overly-specific tests.
- **One run per model.** No variance estimates, no error bars. Agent runs are stochastic; observed differences between adjacent tiers may not be significant.
- **Prompt sanitization is heuristic.** Some symptom descriptions may leak more (or less) than intended, adding difficulty noise.
- **Cost caps confound capability.** A task scored 0 because the agent hit its budget is not the same as a task the model cannot solve. We report exhaustion rate separately to make this visible, but the two remain entangled.
- **Contamination is mitigated, not eliminated.** Pydantic's source is certainly in training data even if these specific bugs are not.

### 11.2 How we would scale and improve

**Immediate (days):**
- Multi-seed runs (3× per model) for confidence intervals — the single biggest credibility improvement per unit effort.
- Expand T4 to ~100 real PRs with a per-commit environment cache, restoring realism.
- Cross-repo expansion to 4–5 non-SWE-bench repos; report per-repo and pooled.
- Human validation pass on a 20-task sample to check prompt solvability and that the intended fix is inferable from the symptom alone.

**Medium term:**
- **Automated difficulty calibration loop:** run a cheap model over a large candidate pool, auto-retire anything it solves, auto-promote survivors. Makes the benchmark self-maintaining against saturation — the central problem with static benchmarks.
- **Held-out private split** to detect contamination as models retrain on public data.
- **Semantic-equivalence checking** for mutants (currently we use the 1–4 failure heuristic as a proxy, which admits some equivalent mutants).
- Property-based / differential testing as an oracle alongside unit tests, reducing over-fitting to specific assertions.

**Longer term:**
- **Continuous regeneration from the upstream repo's PR stream.** Every new merged PR is a fresh, uncontaminated task. A benchmark that regenerates from live history is structurally unsaturable — this is the real answer to the assignment's framing, and the strongest point to end the report on.
- Trajectory-level analysis (where do agents actually fail: localization, hypothesis formation, or edit execution?) rather than pass/fail only.

---

## 12. AI use declaration

**AI was used extensively in this assignment, and this is declared per the instructions.**

To be documented in `AI_DECLARATION.md`:

| Area | How AI was used |
|---|---|
| Design | Requirement analysis, repo selection criteria, benchmark architecture, task taxonomy, scoring formula design — developed in dialogue |
| Implementation | Generation of the mutation engine, validators, scorer, Dockerfile, and run scripts, with human review and iteration |
| Research | Reading mini-swe-agent documentation to determine the integration surface (`--subset`, `image_name`, `env_startup_command`) |
| Report | Drafting and structuring |
| **Not delegated** | Final architectural decisions, verification that scripts actually run, interpretation of results, and all empirical claims |

Full session transcript in `logs/ai-session-transcript.md`.

---

## 13. Immediate next action

Run step 0. It is 30 minutes, it is a hard gate, and every downstream hour depends on the base commit and the measured suite runtime it produces.

```bash
python scripts/00_validate_repo.py --repo pydantic/pydantic --out configs/
```

Do not start writing the mutation engine before this returns green.

---

## 14. Execution log — plan versus actual

Recorded after the build, because the deviations are more informative than the
plan. Every figure here is measured.

### 14.1 What held

- Repository choice. Every step-0 gate passed, several by a wide margin.
- Single-image architecture. No per-task environment work was needed.
- The 1–4 broken-test acceptance band. It rejected 281 of 400 candidates for
  exactly the reasons predicted (157 too broad, 124 equivalent mutants).
- mini-swe-agent integration. No fork required, as the docs implied.

### 14.2 What changed, and why

| Plan | Actual | Cause |
|---|---|---|
| Suite < 6 min with xdist | **4.3 s serially** | pytest-xdist produced collection mismatches; serial is both faster and deterministic here |
| Validate ~250 candidates in ~60 min | 400 candidates in ~22 min | The `--maxfail` early-abort: mutations breaking 900+ tests now abort in ~0.5 s instead of 4.5 s |
| Reference patch verified by re-running the suite | Verified by **byte-identity** with the pristine tree | Stronger guarantee, and free — the reference patch is the exact inverse of the setup patch |
| Additive score `0.6·F2P + 0.3·P2P + 0.1·S` | **Multiplicative** | Measured: the additive form gave 0.40 to a patch that did nothing |
| 6 task families, 100 tasks | 3 families, 100 tasks | T4/T5/T6 cut per the §9.1 cutline; T4 was the only family needing multiple dependency environments |
| Calibration pilot with Sonnet | Not run | Requires API access, unavailable in the build environment |

### 14.3 Problems found by running the code

1. **Copying the repo breaks the editable install** — 11 phantom test failures
   appeared with no mutation applied. Forced the baseline-subtraction design.
   Had this gone unnoticed, part of every score would have been unearnable.
2. **Prompt leak** — 11 of 100 prompts named the guilty source file through
   library frames in the pytest traceback. For T3 tasks this named the exact
   function to implement. Fixed by `prompt_sanitize.py`; post-fix leak count 0.
3. **Stale-patch bug in the scorer** — a leftover line applied `/tmp/setup.patch`
   before the correct one was written, which could have replayed the *previous*
   task's defect. Caught by reading the code during the self-test build.
4. **The self-test's own assertion was wrong** — it required every half-fix to
   score strictly above 0, but a half-fix scoring exactly 0 is correct when both
   defects lie on the same code path.

### 14.4 Sandbox constraints that shaped the build

- No Docker daemon and no GitHub access in the build environment. The repository
  was obtained from its PyPI sdist, which ships all 184 test files. The Docker
  image is defined and reviewed but has not been built here.
- Background processes do not survive between shell invocations, and each
  invocation is capped at ~3 minutes. Validation was therefore made resumable
  with incremental checkpointing and run as a series of time-bounded slices — a
  property worth keeping regardless, since it makes the pipeline restartable.

### 14.5 Remaining work

Only §9's benchmark execution stage. It needs a Docker daemon and an
`ANTHROPIC_API_KEY`, and is a single command: `bash scripts/04_run_benchmark.sh`.

---

## 15. Post-build addendum

### 15.1 Multi-hop verification (added after the schedule in §9)

A stage not in the original plan: `scripts/03c_verify_multihop.py`. The scorer
self-test surfaced a T2 task scoring 1.000 on the half-fix control, which is
only possible if half the fix is the whole fix. Verifying every T2 task the same
way demoted **13 of 18** to T1.

Root cause: generation samples the two mutations from different *modules* but
not from a shared *code path*, so the second mutation often sits where no failing
test reaches. The fix is to sample pairs along a common call path; on the
measured 28% survival rate, ~70 pairs would yield 20 genuine multi-hop tasks.

Effect on composition: hard tier 28 → 16, genuine multi-hop 18 → 5. Reported in
`REPORT.md` §3.4 and §6 rather than adjusted away.

### 15.2 Loss and reconstruction of the working tree

Partway through, the working folder used for artifacts was dropped by the
sandbox mount and its contents were lost. Reconstruction was possible because:

- generation is **seeded**, so stage 2 reproduced all 400 candidates byte-identically;
- validation is **deterministic**, so stage 3 reproduced the same accepted set
  (118 vs 119 originally — the single difference traced to redaction now running
  before prompt templating rather than after, which changed one candidate's
  repro-extraction outcome);
- validation is **resumable**, so it could be re-run as time-bounded slices.

Those three properties were built for other reasons — reproducibility and the
sandbox's 3-minute call cap — and turned out to be what made recovery possible
at all. Worth keeping in any pipeline that runs longer than a single session.

### 15.3 Final measured composition

| Metric | Value |
|---|---|
| Candidates generated | 400 |
| Validated (accepted) | 118 |
| Selected | 100 |
| Families | T1 86 · T2 5 · T3 9 |
| Tiers | easy 48 · medium 36 · hard 16 |
| Mean F2P tests per task | 1.99 |
| Tasks breaking exactly one test | 46 |
| Distinct modules touched | 30 |
| Tasks in `pydantic/_internal/` | 43 |
| Prompt length (chars) | 787 / 2,316 / 5,010 (min/median/max) |
| Scorer self-test | 4 of 4 controls PASS |
