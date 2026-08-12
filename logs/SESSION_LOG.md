# Session log — building PydanticBench

**Reconstruction, not a verbatim transcript.** Written by the assistant from its
own working context at the end of the session. The actual conversation must be
exported separately — see `logs/README.md`. Every number here was measured
during the session; projections are labelled as such.

---

## 1. Requirements and framing

The brief was read as seven deliverables, of which three carried real
difficulty: 100 tasks (too many to hand-write, so generation had to be
programmatic), scoring into [0, 1] (the range implies *partial credit*, not
pass/fail), and benchmarking ≥3 models (mechanically easy, expensive in API
spend).

The framing decision that drove everything else: **"unsaturated" is the property that
matters.** A benchmark on which every model scores 90% has no discriminative
power regardless of engineering quality. Repo choice, task generation, difficulty
controls, and the scoring formula were all derived from that.

Three clarifying questions were asked before any work: repository, model set,
budget. Answers: `pydantic/pydantic`, an Anthropic capability ladder, and
"advise me" on budget.

## 2. The 8-hour constraint changed the architecture

When the deadline was set at 8 hours, four decisions flipped:

1. **Task generation moved from PR-mining to mutation injection.** Mining 100
   real PRs means validating fail-to-pass pairs across 100 commits, each needing
   its own dependency install — hours of compute. Mutation-based tasks pin to a
   single base commit: one image, no per-task reinstall, and validation is
   automatic because the failing tests fall out of the mutation.
2. **The calibration pilot was cut** as a separate stage; difficulty was built in
   by construction instead.
3. **Model runs had to start by hour 5** to overlap with report writing.
4. **Tighter caps** — `step_limit: 80`, `cost_limit: $1.00`, versus stock 250/$3.

## 3. Environment discovery

The build sandbox had **no Docker daemon and no GitHub access**. PyPI was
reachable, and pydantic's sdist ships all 184 test files, so the repository was
obtained via `pip download pydantic --no-binary :all:` and the entire generation
and validation pipeline was developed against that.

Step-0 gates, measured:

| Gate | Result |
|---|---|
| Suite green | 5,584 passed, 296 skipped, 23 xfailed, **0 failures** |
| Suite speed | **4.3 s** serially, whole suite |
| Determinism | two consecutive runs byte-identical |
| Codebase size | 65 source modules, 30,542 lines |

Dependency pinning took several iterations: `jsonschema` had to be upgraded past
the system version, `pytest-run-parallel` was needed for a marker used in
`conftest.py`, and **pytest had to be pinned to 8.3.5** — newer versions turn
pydantic-internal warnings into collection errors, which would make the baseline
non-green and corrupt every score.

`pytest-xdist` was tried and abandoned: it produced collection mismatches across
workers. At 4.3 s serially there was nothing to gain anyway.

## 4. Generation

`mutation_ops.py` implements nine libcst operators. libcst rather than `ast`
because it preserves formatting byte-for-byte — a mutation that also reflowed
whitespace would be obvious in `git diff`.

Measured search space: **3,524 mutation sites across 42 eligible modules**, plus
**497 removable function bodies**. 400 candidates were generated with seed
`20260811`.

## 5. Validation — and the first real problem

The first validation attempt copied the repo per worker to parallelise. It
produced **11 test failures with no mutation applied at all.**

Cause: copying the tree breaks the editable install. Subprocess-based tests and
`importlib.metadata` still resolved to the original checkout. The failures were
environmental, not semantic.

This forced the **baseline-subtraction** design: compute the failure set before
any patch, and subtract it from every subsequent measurement. Without it, part
of every score would have been unearnable — and the cause would have been nearly
invisible in results.

Parallelisation was dropped in favour of serial in-place validation.

Three optimisations followed, each driven by a constraint rather than taste:

- **`--maxfail` early abort.** Any candidate exceeding the acceptance cap is
  rejected regardless of exact count. Some mutations broke 900+ tests; aborting
  at the cap turned those from ~4.5 s into ~0.5 s.
- **Byte-identity solvability proof.** The reference patch is the exact inverse
  of the setup patch, so applying it must reproduce the original file. Comparing
  bytes against the pristine tree is stronger than re-running the suite, and free.
- **Resumable time-bounded slices.** The sandbox killed background processes
  between calls and capped each call at ~3 minutes, so validation checkpoints
  after every candidate. This later turned out to be what made recovery from
  data loss possible (§9).

Final validation outcome, measured:

| Outcome | N |
|---|---:|
| Accepted | 118 |
| Rejected — too broad (>4 tests broken) | 157 |
| Rejected — equivalent mutant (0 broken) | 124 |
| Rejected — patch did not apply | 1 |

Acceptance 29.5%.

## 6. Prompt leak

An explicit leak check was written and run against the generated prompts:
**11 of 100 named the guilty source file**, because pytest tracebacks print the
library frame where the exception surfaced. For a body-removal task that frame
is literally the function the agent must write.

`prompt_sanitize.py` was added to redact library paths, test paths and test
names from the failure block only — the instruction text legitimately contains
`pydantic/` and redacting it would make the prompt incoherent. Post-fix leak
count: **0**.

The trade-off is acknowledged in the report: real bug reports often do include
tracebacks. The harder, less realistic variant was chosen deliberately.

## 7. Scoring — the formula was wrong the first time

The first scorer was additive: `0.6·F2P + 0.3·P2P + 0.1·S`.

Running the no-op control exposed the flaw. P2P is ~1.0 for almost any patch,
because breaking a handful of tests out of 5,584 barely moves a fraction. So a
patch that fixed **nothing** but broke nothing scored **0.40** — a large floor
awarded for doing no work, compressing the range models actually compete in.

Rewritten multiplicatively:

```
score = gate × F2P × (0.9 + 0.1 × S) × 1/(1 + R)
```

Zero for no progress, one for a complete clean fix, and one regression halves
the score.

A separate bug was caught by reading the code while building the self-test: a
leftover line applied `/tmp/setup.patch` before the correct one was written,
which could have replayed the **previous** task's defect into the current one.

## 8. The self-test found a task-design flaw

`07_selftest.py` checks the scorer against four known-value controls. It failed
on `half` (one of two defects fixed on a multi-hop task).

The first reaction — that the scorer was broken — was wrong. A half-fix scoring
exactly 0.0 is **correct** when both defects sit on the same code path. The
assertion was too strict and was corrected.

But chasing the failure surfaced something worse: one T2 task scored a full
**1.000** on the half-fix control, which is only possible if half the fix is the
whole fix. `03c_verify_multihop.py` was written to apply each half of every T2
reference patch alone.

**13 of 18 "multi-hop" tasks were demoted to T1. Only 5 are genuinely
multi-hop.**

Root cause: generation samples the two mutations from different *modules* but
not from a shared *code path*, so the second mutation frequently sits where no
failing test reaches. The hard tier fell from 28 tasks to 16 as a result. This
is reported in `REPORT.md` §3.4 rather than smoothed over, and the fix — pairing
mutations along a common call path — is the top scaling recommendation.

This is the strongest argument for the self-test existing at all. Without it, a
mislabelled task family would have shipped.

## 9. Working folder loss and reconstruction

Partway through, the mounted working folder was dropped by the sandbox and its
contents were lost — all scripts and the generated task set.

Recovery was possible because of three properties built for other reasons:

- generation is **seeded**, so all 400 candidates regenerated byte-identically
  (verified by checksum);
- validation is **deterministic**, so the accepted set reproduced (118 vs 119
  originally — the single difference traced to redaction now running before
  prompt templating rather than after, changing one candidate's repro extraction);
- validation is **resumable**, so it could be re-run as time-bounded slices.

Everything was rebuilt into a host-backed folder and verified visible on disk.

## 10. Running the benchmark — blocked, and why

A Gemini API key was provided with a request to run the benchmark. Two
independent blockers, both tested rather than assumed:

- no Docker daemon in the build sandbox;
- `generativelanguage.googleapis.com` unreachable (HTTP 000), same as
  `github.com`.

Gemini support was added to the runner regardless, so the run is one command on
a machine with Docker and network. The key was never written to any file in the
deliverable; the temporary copy used for the reachability test was deleted.

**The key is in the raw chat transcript and should be rotated.** See
`logs/README.md`.

## 11. The single-command runner

`run.sh` was written to collapse every stage into one interactive command. It was
then *tested* with a stubbed `docker` and stubbed agent, which found two bugs
that would have hit the team directly:

1. **The green-baseline check aborted on a healthy image.** It matched the glob
   `*failed*` against pytest's summary line `5584 passed, 296 skipped, 23
   xfailed` — "xfailed" contains "failed". Replaced with `[0-9]+ (failed|error)`
   and verified against three real summary lines.
2. **A 5-task smoke run scored all 100 tasks**, reporting 95 never-attempted
   tasks as zeros. The scorer now skips instances absent from `preds.json`:
   "not attempted" and "attempted and failed" are different things.

Terminal handling was also hardened to degrade to defaults under cron/CI instead
of erroring on `/dev/tty`, and a Docker backend was added to the self-test so it
can run without a local checkout.

## 11b. First real Docker run — the self-test earned its keep

The first end-to-end run on a machine with Docker failed at the self-test:
`oracle` scored 0.000 where it must score 1.000. `noop` and `cheat` passed,
which narrowed it immediately — the gate logic was fine, the *environment* was
not.

Two compounding bugs, both in the Dockerfile:

1. **Missing `pytest-benchmark`.** pydantic's `pyproject.toml` puts
   `--benchmark-columns --benchmark-group-by group --benchmark-warmup on
   --benchmark-disable` in `addopts`. Without the plugin, pytest rejects those
   flags and aborts in under a second — even though `tests/benchmarks` is
   excluded from every run. The dependency list had been assembled by trial and
   error in the build sandbox rather than read from pydantic's own `dev`
   dependency group. It is now taken from that group verbatim, and the missing
   packages were `pytest-benchmark`, `pytest-pretty`, `pytz`, `packaging` and
   `coverage[toml]`.

2. **The build's smoke check could not fail.** It ran
   `pytest ... | tail -1`, and a shell pipeline reports the status of its *last*
   command, so `tail`'s 0 masked pytest's 4. Verified directly: same broken
   suite gives exit 4 with `set -o pipefail` and exit 0 without it. The build
   therefore succeeded while producing an image whose test suite could not run.

Both were reproduced locally in a clean virtualenv before being fixed, and the
corrected dependency list was confirmed green: **5,582 passed, 296 skipped, 23
xfailed in 4.8 s**.

A third bug surfaced while testing the fix. `run.sh`'s baseline check assigned
`BASE_SUMMARY=$(... | grep ...)`, and `grep` exits 1 when it matches nothing.
Under `set -e` that aborted the script *silently*, skipping the very error
handling meant to explain the failure. Adding `|| true` restored it. The
baseline check now trusts pytest's exit code rather than pattern-matching its
summary line — text parsing had already caused two separate failures here
("23 xfailed" matched as a failure, and empty output treated as a warning).

This sequence is the strongest evidence for the self-test's existence. Nothing
else in the pipeline would have noticed: the image built, the agent would have
run, and every task would have scored 0 — indistinguishable from three models
that simply could not solve anything.

## 11c. sdist versus git — a parity trap

The rebuilt image failed its own smoke check with
`ModuleNotFoundError: No module named 'hypothesis'` while collecting
`tests/pydantic_core`. The build refusing to proceed was the previous fix
working as intended.

Root cause: the entire pipeline was developed against pydantic's **sdist**
(GitHub was unreachable from the build sandbox), while the image checks out the
**git tag**. The git tree contains `tests/pydantic_core`, which the sdist omits.

The fix was not to install `hypothesis`. That directory is property-based
testing: randomised, with a persistent example database, therefore
nondeterministic — and nondeterminism in the baseline is fatal to scoring.
Including it would also have broken parity, since none of the 201 fail-to-pass
tests in the shipped task set live outside the top-level `tests/` directory.

Test scope is now defined once, in `scripts/pytest_scope.py`, and consumed by
the validator, the scorer, the repo gate, `run.sh`, and the image itself via
`PYTEST_ADDOPTS`. Previously the same ignore list was duplicated in five places
and had already drifted.

Two further defects surfaced while verifying the fix:

- **The self-test left the working tree dirty.** `score_one` resets at the start
  of each task but not at the end, so the local backend finished with the last
  task's patches still applied. Snapshotting that tree as "pristine" embedded a
  live mutation in `_internal/_generics.py`, which made the oracle control score
  0.333 instead of 1.000. Harmless under Docker (fresh container per task),
  corrupting under the local backend. The self-test now restores the tree.
- **Nothing verified that the task set matched the image.** Patches generated
  against the sdist are applied to a git checkout; if any source file differed,
  every task would report `setup_patch_failed` and all models would score 0.0 —
  indistinguishable from three useless models. `scripts/08_verify_tasks_apply.py`
  now runs `git apply --check` for all 100 tasks inside the image before any API
  spend. Verified: 100/100 apply cleanly.

## 11d. The `half` assertion was wrong a third time

With the image finally green (5,695 passed) and all 100 task patches applying,
the self-test still failed on one control: `half` scored 0.000 across both
sampled multi-hop tasks, and the assertion demanded that partial credit be
visible somewhere in the sample.

The assertion was wrong again, and the reason is worth stating because it is a
design lesson rather than a typo. `03c_verify_multihop.py` deliberately retains
only tasks where **no single half of the fix restores the fail-to-pass set**.
The stricter that filter, the more likely every half-fix scores exactly 0. The
assertion had turned evidence of task quality into a test failure.

The invariant that genuinely belongs to the scorer is only: *a partial fix must
never earn full marks*. Whether partial credit is observable end-to-end is a
property of the task set, not of the scoring code.

So the self-test was restructured:

- `half` now asserts only `score < 1.0`.
- A new `formula` control tests the arithmetic directly against six exact cases
  — complete fix, no progress, half progress, one regression, lint penalty, and
  a compound case. It needs no container, so a failure there localises the
  problem to the formula rather than the environment. This required extracting
  `compute_score()` from `score_one()` as a pure function.
- Every control now prints per-task detail — score, f2p fraction, regression
  count and gate reason. A bare mean hid which task did what, which is precisely
  why three successive assertion bugs were hard to diagnose.

Measured locally after the change: half-fixes score 0.500, 0.333, 0.500, 0.333
and 0.008. The last is instructive — 25% of the broken behaviour restored but 29
regressions introduced, so `1/(1+29)` collapses it almost to zero. That is the
regression term doing exactly its job.

Three wrong assertions about the same control, all of them "the scorer is
broken" when the scorer was right, is the clearest signal in this project that
end-to-end controls should test invariants, not incidental properties of the
data they happen to run on.

## 11e. A path with spaces

The first live benchmark run died immediately with:

    ./run.sh: line 476: /Users/.../Library/Application: No such file or directory

The agent invocation was stored as a string, `MSWEA="$VENV/bin/mini-extra
swebench"`, and expanded unquoted so that the `swebench` subcommand would be a
separate word. On macOS the default install path contains **"Application
Support"**, so word splitting tore the command in half at that space and bash
tried to execute a path fragment.

Fixed by holding the command in a bash array and expanding it quoted. The
`--slice` arguments got the same treatment, with `${SLICE[@]+"${SLICE[@]}"}` to
survive bash 3.2 -- macOS's default -- treating an empty array as unbound under
`set -u`.

Two secondary faults surfaced from the same failure: scoring crashed with a raw
`FileNotFoundError` when a run produced no `preds.json`, and the report stage
would have failed the same way. Both now report the situation and continue.

The lesson is narrow but sharp: every path in this project was tested under
`/tmp/...` and `/sessions/...`, none of which contain spaces. The bug was
invisible in the development environment and immediate in the user's. The fix
was verified by rebuilding the whole pipeline under
`/tmp/Library/Application Support/local-agent/outputs/pydanticbench` and running
all three model configurations through to a summary.

### An unresolved difference worth recording

In the container, all five multi-hop half-fixes score exactly 0.000 with no gate
reason; run locally against the sdist tree, the same patches score 0.500, 0.333,
0.500, 0.333 and 0.008. The image's suite collects 5,695 tests against the
sdist's 5,582, so the two trees are not identical outside the library source
(which `08_verify_tasks_apply.py` confirms matches exactly).

This does not affect grading: within the image the scorer is self-consistent --
baseline green, oracle 1.000, noop 0.000, cheat gated, and a partial fix never
scores full marks. Half-fixes are a diagnostic control, not part of evaluation.
But it is an unexplained environment difference and is recorded rather than
waved away.

## 11f. YAML folding destroyed the startup command

With every earlier fix in place, all five instances still failed instantly at
container startup: `syntax error near unexpected token '('` on a Python comment,
and `cat: unrecognized option '--git'`.

The per-task setup command lived in the harness config as a YAML **folded
scalar** (`>`), which collapses newlines into spaces. So this:

    cat > /tmp/setup.patch <<"PYDBENCH_EOF"
    {{ setup_patch }}
    PYDBENCH_EOF
    git apply ...

reached the shell as a single line. A heredoc needs real newlines, so it never
terminated; jinja then injected the diff *with* its newlines, and every line of
the patch was executed as a shell command. Confirmed by loading the config with
`yaml.safe_load` and counting newlines in the result: one.

Fixed by shipping the patch base64-encoded in a new `setup_patch_b64` field and
reducing the command to a single line. Base64 is inert to both YAML folding and
shell parsing: one line, characters drawn only from `[A-Za-z0-9+/=]`, no quotes,
newlines, backticks or `$`. The scorer was converted to the same transport,
since a heredoc there would break on any diff containing the delimiter.

Verified by rendering the real command for all 100 tasks and executing each
against a local checkout: 100 applied, 0 failed.

`08_verify_tasks_apply.py` did not catch this, and the reason is worth noting:
it applies patches *directly* rather than through the harness's startup path. It
validated the payload while missing the transport. A verification step is only
as good as the code path it exercises.

## 11g. A failed run poisons the next one

After the fix, the next run did nothing at all — `Skipping 5 existing instances`,
$0.00 spent, all-zero scores. mini-swe-agent treats any instance present in
`preds.json` as complete, including instances that crashed, so the previous
failed run made the retry a no-op.

The advice given was "delete `results/` first", which is exactly the kind of
instruction that gets forgotten. `run.sh` now inspects `preds.json`: if every
recorded instance has an empty patch, that is the signature of a failed run
rather than a bad model, and it offers (default yes) to clear and re-run.
`FRESH=1` forces it.

## 11h. Model identifiers rot

The first run to reach a provider failed with `NotFoundError`:
`gemini-2.5-flash-lite is no longer available to new users`. The benchmark was
working end to end -- containers started, patches applied, the agent
initialised -- and the only broken thing was a model name written weeks earlier.

The first fix was a preflight: one token per model, run before the image build,
failing with the list of models the key could reach. That was right but
insufficient, because it still ended in an error and a manual edit.

The second, better fix distinguishes outcomes that deserve different treatment:

  * **404** -- the identifier is dead. Discard it.
  * **429** -- the model exists, the key is rate-limited. Not a naming problem;
    offered rather than discarded, since quota recovers.

and then does something useful with the failure: it queries the provider,
filters out models that cannot do software engineering at all (image, audio,
TTS, video, embedding, robotics, music), ranks the remainder into lite / flash /
pro preferring stable over preview and newer over older, and offers a ladder to
accept with Enter or override by number.

Verified against the real 36-model list returned by the user's key. From that
list it correctly proposes `gemini-3.5-flash-lite / gemini-3.6-flash /
gemini-2.5-pro` -- three live, non-preview, text-capable models spanning the
capability range -- while rejecting `nano-banana-pro-preview`, `lyria-3-*`,
`gemini-robotics-*` and the TTS and image variants.

This also forced a reordering of `run.sh`: dependency installation and model
selection now happen *before* the scope prompt and cost estimate, so the
estimate reflects the models actually chosen. Per-task rates are inferred from
the tier in the model name rather than hard-coded against a fixed ladder.

The general lesson: an external identifier that can change between writing code
and running it should be validated early, and validation that ends in "edit this
file and try again" is only half a solution.

## 11i. The bug that punished correct answers

The first runs that reached the models produced 0.000 across all three Anthropic
tiers on a 5-task slice. The instinct was "the tasks are too hard". The
trajectories said otherwise — Sonnet had found the injected defect exactly:

> **Root cause:** in `pydantic/_internal/_validators.py`, `forbid_inf_nan_check`
> had inverted logic ... **Fix:** corrected the condition.

and still scored zero.

The cause: agents submit via `git diff`, and the injected defect was applied to
the working tree **without being committed**. HEAD was the pristine tree, so a
correct fix restored the file to its original content and the diff was empty.

The incentive this created is worth stating plainly. A model that repaired the
defect exactly scored 0. A model that made sloppy, unrelated edits would have
produced a non-empty diff and scored higher. The benchmark actively punished
precision. Any capability numbers collected before this fix would have been not
merely noisy but *inverted*.

Reproduced in a four-line git experiment before changing anything, then fixed by
committing the defect during task setup, then verified end to end through the
real rendered startup command: the same submission path now yields a 13-line
patch where it produced zero.

Five gates ran before this — image baseline, task-patch application, five scorer
controls, model reachability — and none caught it, because all of them hand
patches to the scorer directly and never exercise the `git diff` path an agent
uses to submit. The same lesson as §11f, learned again: **a verification step is
only as good as the code path it exercises.** The controls tested the scorer;
nothing tested the contract between agent and scorer.

A second finding from the same runs: 2 of 5 tasks ended in `LimitsExceeded` at
`step_limit: 80`. A 40% exhaustion rate confounds capability with budget, so the
limit is now 150, with `cost_limit` left as the real ceiling.

## 12. Final state

| Metric | Value |
|---|---|
| Candidates generated | 400 |
| Validated | 118 |
| Selected | 100 |
| Families | T1 86 · T2 5 · T3 9 |
| Tiers | easy 48 · medium 36 · hard 16 |
| Mean F2P tests per task | 1.99 |
| Tasks breaking exactly one test | 46 |
| Distinct modules touched | 30 |
| Scorer self-test | 4 of 4 controls PASS |
| Benchmark runs | **not executed** — see §10 |

## 13. What a reader should be sceptical of

- **There are no model results.** The headline comparison is projected, not
  measured.
- The Docker image was never built (no daemon), so the Dockerfile is reviewed
  but unexecuted. Same for live `mini-extra` invocation.
- Difficulty tiers come from structural proxies and have not been validated
  against actual model behaviour. The first real run is what tests them.
- The benchmark skews easier than designed after the multi-hop demotion.
