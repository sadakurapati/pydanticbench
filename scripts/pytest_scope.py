"""
Single source of truth for which tests the benchmark runs.

Every stage -- validation, scoring, the image smoke check and run.sh -- must use
the SAME test scope. If they diverge, the baseline computed in one place stops
matching the runs graded in another, and regression counts become meaningless.

Excluded, with reasons:

  tests/pydantic_core   Present ONLY in the git checkout, not in the sdist the
                        task set was generated from, so including it would break
                        parity between task generation and scoring. It is also
                        hypothesis-based property testing: randomised, with a
                        persistent example database, therefore not reproducible.
                        Nondeterminism in the baseline is fatal to scoring.
  tests/test_docs.py    Requires the docs/ tree, absent from the sdist; asserts
                        on documentation examples rather than library behaviour.
  tests/benchmarks      Timing-sensitive; measures speed, not correctness.

All 201 fail-to-pass tests in the shipped task set live directly under tests/,
so none of these exclusions removes gradeable behaviour.
"""

IGNORES = [
    "tests/pydantic_core",
    "tests/test_docs.py",
    "tests/benchmarks",
]


def ignore_args() -> list[str]:
    return [f"--ignore={p}" for p in IGNORES]


def ignore_str() -> str:
    return " ".join(ignore_args())
