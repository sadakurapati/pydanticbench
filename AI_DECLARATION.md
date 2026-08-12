# Declaration of AI assistance

AI was used extensively throughout this assignment. This document states where
and how.

## Tool

Claude (Anthropic), used interactively in an agentic session with shell access.

`logs/SESSION_LOG.md` is an engineering log of the build — decisions,
measurements, bugs found and what changed as a result — written by the assistant
from its own working context. It is a **reconstruction, not a verbatim
transcript**: the conversation is stored in an application-internal directory the
assistant could not read, so the raw transcript must be exported manually and
placed at `logs/chat-transcript.md`. See `logs/README.md`, which also covers
credential scrubbing.

## What AI did

**Design.** The benchmark architecture was developed in dialogue: interpreting
the requirements, arguing through repository selection criteria, choosing the
task taxonomy, and designing the scoring formula and its integrity gate. The
central design constraint — that avoiding SWE-bench's twelve repositories
matters more than any other selection criterion, because contamination makes
scores meaningless — came out of that discussion.

**Implementation.** All code in `scripts/`, `docker/`, `bench/` and `configs/`
was AI-written, then run, debugged and revised in the same session. It is not
untested output: every script was executed, and several were corrected in
response to what execution revealed.

**Research.** The mini-swe-agent documentation was read to determine the
integration surface — that `--subset` accepts any `datasets`-loadable path, that
each instance may carry its own `image_name`, and that `run.env_startup_command`
is Jinja-rendered against the instance dict. Those three facts are why this
benchmark needs no fork of the harness.

**Writing.** `PLAN.md`, `README.md`, `REPORT.md` and this file were AI-drafted.

## What the empirical work actually settled

Five things in the final design exist because running the code contradicted the
plan, not because they were designed up front:

1. **Copying the repository broke the editable install**, producing 11 phantom
   test failures unrelated to any mutation. This forced the baseline-subtraction
   design, without which scores would have been partly unearnable.
2. **The additive scoring formula gave 0.40 to a patch that did nothing.**
   Measured on the no-op control. The scorer was rewritten to be multiplicative.
3. **11 of 100 generated prompts named the guilty source file** in the pytest
   traceback. Caught by an explicit leak check; fixed with `prompt_sanitize.py`.
4. **The self-test's `half` assertion was wrong**, not the scorer: on some
   multi-hop tasks, fixing one of two defects correctly restores no tests.
5. **13 of 18 "multi-hop" tasks were not multi-hop at all.** Chasing failure (4)
   revealed that a T2 task could be fully solved by half its reference patch.
   `03c_verify_multihop.py` was written to detect and demote these. This is the
   most consequential finding in the project: it means the generator's premise
   was wrong 72% of the time, and it is documented in `REPORT.md` §3.4 rather
   than hidden.

Items 4 and 5 are the clearest argument for the self-test existing at all. A
benchmark whose scorer has not been checked against known-value inputs would
have shipped with a mislabelled task family and nobody would have noticed.

## Limits of the delegation

Final architectural decisions, the choice of repository, the interpretation of
results, and every empirical claim in `REPORT.md` were checked against actual
execution output. Numbers quoted are measured, not estimated; where a figure is
a projection it is labelled as one. The benchmark runs themselves were **not**
performed — see `REPORT.md` §5 for why.
