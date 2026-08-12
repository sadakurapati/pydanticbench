# PydanticBench: an unsaturated coding-agent benchmark from `pydantic/pydantic`

**Sada Kurapati**

All figures are measured unless explicitly labelled a projection.

---

## 1. Repository

**`pydantic/pydantic`**, pinned at release tag **v2.13.4**. ~7,000 merged PRs.

The dominant selection criterion was not size — it was **contamination**. The
twelve repositories in SWE-bench (django, sympy, astropy, matplotlib, pytest,
requests, scikit-learn, sphinx, xarray, flask, pylint, seaborn) appear verbatim
in the training data and public evaluation traces of every frontier model.
Building on one of them produces inflated scores that measure memorisation.
That outranked every other consideration; pydantic is clean on it.

The practical criteria were verified before anything else was built
(`scripts/00_validate_repo.py`):

| Gate | Result |
|---|---|
| Suite green at base commit | 5,584 passed, 296 skipped, 23 xfailed, **0 failures** |
| Suite fast | **4.3 s** serially, whole suite |
| Deterministic | Two consecutive runs byte-identical |
| Hermetic | No network, database or GPU |
| No compiled build | `pydantic-core` ships as a prebuilt wheel — no Rust toolchain |

The 4.3-second suite is the most consequential property. Validation runs the
suite once per candidate and scoring once per submission; at 30 seconds a run
this project would not have fit the time budget at all.

The codebase is substantial and genuinely hard: 65 source modules, 30,542 lines,
layered Python API → core schema → Rust core, so defects propagate non-locally.

## 2. Evaluation environment

A **single Docker image** (`docker/Dockerfile`) serves all 100 tasks. Every task
pins the same base commit; per-task starting state arrives as a small patch
applied at agent start through mini-swe-agent's `run.env_startup_command` hook.
The alternative — one image per task — costs hours of build time and tens of
gigabytes. The trade is real and stated in §6.

Four measures make the environment trustworthy:

**Git history is destroyed and re-initialised.** Otherwise an agent can `git log`
or `git fetch` and read the upstream fix. This is the most commonly missed
anti-cheat in homegrown benchmarks.

**Grading criteria are restored after the model patch is applied.** `tests/` is
re-synced from `/opt/pristine/` before any test executes, so edits to tests are
inert even if the integrity gate missed them.

**Scoring containers run `--network none`.**

**Determinism is pinned**: `PYTHONHASHSEED=0`, `TZ=UTC`, no pytest cache, and
`pytest==8.3.5` — newer pytest turns pydantic-internal warnings into collection
errors, which would make the baseline non-green and corrupt every score. The
image build ends by running the suite and printing the result, so a broken
baseline fails loudly at build time.

## 3. Task design

### 3.1 The saturation problem

Scraping issues and using each PR's tests as the grader reproduces SWE-bench, on
which frontier models exceed 70%. A faithful clone arrives saturated.
PydanticBench attacks this with an uncontaminated repository, **synthetic
defects that exist in no training corpus**, prompts that describe symptoms
without naming a location, and defects whose fix site differs from their failure
site.

### 3.2 Families and composition

| ID | Family | N | Starting state |
|---|---|---:|---|
| T1 | Single-mutation repair | 86 | One semantic mutation in library source |
| T2 | Multi-hop repair | 5 | Two mutations in different modules, both required |
| T3 | Reimplementation | 9 | Function body replaced with `raise NotImplementedError` |

Difficulty tiers: **16 hard, 36 medium, 48 easy.**

Nine libcst mutation operators are used. libcst rather than `ast` because it
preserves formatting byte-for-byte: a mutation that also reflowed whitespace
would be trivially visible in `git diff`.

| Operator | N | Operator | N |
|---|---:|---|---:|
| comparison swap | 34 | branch swap | 8 |
| boolean-literal flip | 17 | keyword-arg flip | 7 |
| integer off-by-one | 17 | exception swap | 6 |
| boolean-op swap | 13 | `not` removal | 4 |
| body removal | 9 | arithmetic swap | 3 |

Tasks touch **30 distinct modules**; **44 of 100** sit in `pydantic/_internal/`.

### 3.3 The acceptance filter — where difficulty is controlled

400 candidates were generated from 3,524 available mutation sites and 497
removable function bodies. A candidate becomes a task only if it breaks
**between 1 and 4 tests**.

| Outcome | N | Why |
|---|---:|---|
| Accepted | **118** | 1–4 tests broken |
| Too broad | 157 | >4 broken — trivially localised from any traceback |
| Equivalent mutant | 124 | 0 broken — semantically no-op, unscoreable |
| Patch did not apply | 1 | overlapping edits |

Acceptance 29.5%. The upper bound is the anti-saturation lever: some mutations
broke over 900 tests and would be solvable by reading one traceback. Surviving
tasks break **2.01 tests on average**, and **44 of 100 break exactly one** — a
deliberately narrow signal.

Solvability is proven, not assumed: each accepted candidate's reference patch is
applied and the touched files checked byte-for-byte against the pristine tree.
Byte-identity with a known-green baseline is stronger than re-running the suite,
and free.

### 3.4 Multi-hop verification — a finding

T2 tasks inject two mutations in different modules on the premise that an agent
must find both. **That premise turned out to be false most of the time.**

The scorer self-test flagged it: one T2 task scored a full 1.000 on the
"half fix" control, which is only possible if half the fix is the whole fix.
`scripts/03c_verify_multihop.py` now applies each half of every T2 reference
patch alone; if either half restores the whole fail-to-pass set, the task is
demoted to T1 and re-tiered.

**13 of 18 T2 tasks were demoted. Only 5 are genuinely multi-hop.**

The cause is a design flaw in generation: mutation pairs are sampled from
different modules but not from a *shared code path*, so the second mutation
frequently sits somewhere no failing test exercises. Sampling pairs along a
common call path is the fix, and is the highest-value change to the generator
(§7). The immediate consequence is a benchmark that skews easier than intended —
the hard tier fell from 28 tasks to 16 — and that is reported rather than
papered over.

### 3.5 Prompts

Prompts are **symptom reports**: a runnable reproduction extracted from the
failing test with its identity stripped, plus the observed expected-vs-actual
delta. They never name the file, function, or test.

This is harder than SWE-bench, where issue text frequently contains a traceback
naming the exact frame to edit. An automated leak check found **11 of 100
prompts still named the guilty source file** through library frames in the
traceback; `scripts/prompt_sanitize.py` redacts those. Post-fix leak count: **0**.
Prompts run 995–5,010 characters, median 2,316.

## 4. Scoring

```
score = gate × F2P × (0.9 + 0.1 × S) × 1/(1 + R)
```

- **F2P** — fraction of the task's fail-to-pass tests now passing. Partial credit lives here.
- **R** — previously-passing tests the patch broke.
- **S** — ruff clean on changed files (10% modifier).
- **gate** — 0 if the patch is empty, fails to apply, touches `tests/`, `conftest.py`, packaging or CI config, or introduces `pytest.skip`/`xfail`/`sys.exit`/`collect_ignore`.

**The formula was changed after measurement.** The first version was additive —
`0.6·F2P + 0.3·P2P + 0.1·S` — and it was wrong. P2P is ~1.0 for almost any
patch, because breaking a handful of tests out of 5,584 barely moves a fraction.
A patch that fixed **nothing** but broke nothing therefore scored **0.40**. That
floor, awarded for no work, compresses the range models actually compete in. The
multiplicative form gives 0 for no progress and 1 for a complete clean fix. The
regression term is deliberately sharp: one regression halves the score, because
shipping a fix that breaks something else is a failure, not a rounding error.

A **baseline subtraction** applies throughout: tests failing before any model
patch are excluded from both F2P and regression counts. This was forced by
discovering that copying the repository breaks the editable install and produces
11 phantom failures. Without it, part of every score would have been unearnable.

### 4.1 Scorer verification

`scripts/07_selftest.py` is the benchmark's own test suite. Measured:

| Control | Input | Expected | Measured | Result |
|---|---|---|---|---|
| oracle | reference patch | 1.000 | **1.000** | PASS |
| noop | empty patch | 0.000 | **0.000** | PASS |
| cheat | patch editing `tests/` | 0.000, gated | **0.000**, `forbidden_path:` | PASS |
| half | 1 of 2 defects fixed | <1, partial credit | **0.444** | PASS |

A half-fix scoring exactly 0.0 is **correct**, not a bug: on some multi-hop
tasks both defects lie on the same code path, so repairing one restores no
failing test. The self-test's original assertion (`0 < score < 1` per task) was
wrong and was corrected — and chasing that failure is what uncovered §3.4.

## 5. Benchmark execution

Three configurations via mini-swe-agent. No fork of the harness is required: the
task file loads through `--subset tasks/hf`, each instance carries its own
`image_name`, and per-task setup rides on `run.env_startup_command` — with the
patch base64-encoded, because a raw diff cannot survive YAML folding plus shell
parsing (see `logs/SESSION_LOG.md` §11f).

`./run.sh` drives the whole thing interactively: prerequisites, dependency
install, model selection, image build, verification and the run. Model
identifiers are checked against the live provider before anything is built —
they rot, and a dead identifier otherwise fails only after an approved budget.
When one is unusable the runner lists what the key can reach, ranks it into
lite/flash/pro tiers and offers a ladder.

Five gates run before any spend: the image baseline must be green, all 100 task
patches must apply *inside the image*, the scorer must pass its five controls,
every model must answer a one-token probe, and a live task must not leak its
own answer.

That last gate exists because fixing the submission bug below introduced two
ways for a task to give itself away, and nothing else would have noticed.
Committing the defect on top of the clean base put the answer in the history:
`git show HEAD` printed the injected mutation as a one-line delta, and
`git revert HEAD` solved any task without reading code. Separately, the image
kept a clean copy of the source at `/opt/pristine/pydantic`, so `diff -r` against
`/testbed/pydantic` printed the defect outright.

Both are closed. The task's git history is re-initialised so the buggy tree is
the *root* commit — nothing to diff against — and the image no longer ships a
clean source snapshot; the reset path restores from the git tag instead.
Verified across all 100 tasks: one commit each, no delta in history, and a
correct fix still produces a submittable patch.

The pattern is now three-for-three: the baseline check, the task-application
check, the scorer controls and the model probe all exercise the *scoring* path,
and every bug that mattered lived on the *agent* path. A verification step is
only as good as the code path it exercises.

Limits are `step_limit: 80`, `cost_limit: $1.00`, below the stock 250 / $3.00.
Hitting a limit scores 0 and is reported separately as budget exhaustion, so the
cap is visible rather than silently confounding results.

**Status: the first real runs exposed a benchmark bug, now fixed; a full run is
still outstanding.**

Three Anthropic tiers (Haiku 4.5, Sonnet 5, Opus 5) were run over a 5-task smoke
slice. Every task scored 0.000 — and the reason was not model capability.

Reading the trajectories showed Sonnet correctly diagnosing an injected defect:

> **Root cause:** in `pydantic/_internal/_validators.py`, `forbid_inf_nan_check`
> had inverted logic ... **Fix:** corrected the condition to raise only when the
> value is *not* finite.

That is exactly the injected mutation, correctly identified and correctly
repaired. It scored zero, because the agent submits its work as `git diff` and
the injected defect had been applied to the working tree **without being
committed**. HEAD was therefore the *clean* tree, so a correct fix restored the
file to its original content and the diff came back empty.

The failure mode is perverse: the more accurately a model repairs the defect,
the more certainly it scores nothing. A model that made unrelated edits would
have produced a non-empty diff and scored higher than one that was exactly
right. Committing the defect during task setup makes HEAD the buggy state, so
the diff contains precisely the agent's repair. Verified: the same submission
path now yields a 13-line patch where it produced 0 lines before.

The runs surfaced a second problem. Two of five tasks ended in `LimitsExceeded`
at `step_limit: 80` — a 40% budget-exhaustion rate, high enough to confound
capability with budget. The limit is now 150; `cost_limit` still caps real spend.

Both fixes are in. The full three-model run has not yet been repeated, so this
report contains **no capability numbers**, and none should be inferred from the
zeros above — they measure a harness defect, not the models.

Projected cost from the configured caps: **~$110** for a full 100-task run
across three Anthropic tiers, ~2.5 h at 8 workers; a 5-task smoke run is a few
dollars. Projections from the per-task caps, not measurements.

**The reading that matters is the by-tier row.** A benchmark that discriminates
shows monotonic decay easy → hard, with the strongest model well under 50% on
hard. If all three cluster high, the benchmark is saturated and the acceptance
band should tighten from 1–4 broken tests toward 1–2.

## 6. Shortcomings

**No results.** The headline deliverable — three models compared — is projected,
not measured. Everything upstream of it is verified.

**Synthetic defects are not real defects.** Mutation-injected bugs are
single-edit, syntactically local, and always have a minimal inverse fix. Real
bugs involve design errors, missing cases and misunderstood requirements. This
measures *debugging under uncertainty*, not *engineering judgment*. The planned
PR-derived family was cut for time — it was the only family needing multiple
commits and therefore multiple dependency environments.

**The benchmark skews easier than designed.** After multi-hop verification the
hard tier is 16 tasks, not 28, and genuine multi-hop tasks number 5. §3.4
explains why and §7 how to fix it.

**Difficulty tiers are constructed, not validated.** They come from structural
proxies. Whether they predict actual model difficulty is exactly what the first
run reveals — and if they do not, the tiering is wrong.

**Single repository, single base commit.** Results are pydantic-specific and
codebase-state diversity is minimal.

**Tests are an imperfect oracle.** A patch can pass F2P and P2P while being poor
code; a legitimate alternative fix may fail overly specific tests.

**One run per model, no variance estimates.** Agent runs are stochastic;
differences between adjacent tiers may not be significant.

**Cost caps confound capability.** A task scored 0 because the agent exhausted
its budget is not the same as one the model cannot solve. Exhaustion rate is
reported separately, but the two remain entangled.

**Prompt sanitisation is heuristic and lossy.** Redacting tracebacks makes tasks
harder but less realistic. The trade was made deliberately in favour of
difficulty.

**Contamination is mitigated, not eliminated.** Pydantic's source is certainly in
training data even though these specific defects are not.

## 7. How I would improve and scale this

**Immediate.** Run the three configurations — that is the missing deliverable.
Then fix the multi-hop generator: sample mutation pairs along a **shared call
path** rather than merely from different modules, and verify with
`03c_verify_multihop.py` in the loop rather than after the fact. On the measured
28% survival rate, generating ~70 pairs to reach 20 genuine multi-hop tasks is
cheap. Then multi-seed runs (3× per model) for confidence intervals — the
largest credibility gain per unit effort — and restore the PR-derived family
with a per-commit environment cache.

**Medium term.** An **automated difficulty calibration loop**: run a cheap model
over a large candidate pool, auto-retire what it solves, auto-promote the
survivors. This makes the benchmark self-maintaining against saturation, the
central failure mode of every static benchmark. Add semantic-equivalence
checking for mutants — the 1–4 band is only a proxy, and 124 equivalent mutants
were rejected empirically rather than detected analytically. Extend to 4–5
further non-SWE-bench repositories. Add a held-out private split to detect
contamination as models retrain on public data.

**The real answer to scaling.** Regenerate continuously from the upstream PR
stream. Every newly merged PR is a fresh, uncontaminated task, and a benchmark
that regenerates from live history is structurally unsaturable. The pipeline
here already runs unattended, is seeded for reproducibility, and is resumable;
pointing it at `HEAD` on a schedule rather than a pinned tag is a small change
to stages 2 and 3 and is the natural next step.

## 8. AI use

AI was used extensively — design, implementation, research and drafting. See
`AI_DECLARATION.md` and `logs/`.
