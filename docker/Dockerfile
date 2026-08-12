# PydanticBench evaluation environment.
#
# ONE image serves all 100 tasks. Every task pins the same base commit, and
# per-task starting state is delivered as a small patch at agent start. This is
# a deliberate trade: it costs codebase-state diversity, and buys a ~10 minute
# build instead of a multi-hour one, plus a single artifact to ship.
#
# Build (from the repo root, so the bench/ COPY resolves):
#   docker build -f docker/Dockerfile --build-arg BASE_TAG=v2.13.4 -t pydanticbench:base .

FROM python:3.12-slim

ARG BASE_TAG=v2.13.4

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential ca-certificates curl rsync patch \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /testbed

RUN git clone --depth 1 --branch ${BASE_TAG} https://github.com/pydantic/pydantic.git . \
    && git rev-parse HEAD > /opt/base_sha

# Test dependencies. This list is pydantic's own `dev` dependency group from
# pyproject.toml, not a guess.
#
# pytest-benchmark is REQUIRED even though tests/benchmarks is excluded from
# every run: pydantic's pyproject sets
#     addopts = --benchmark-columns --benchmark-group-by group
#               --benchmark-warmup on --benchmark-disable
# and pytest rejects those flags outright if the plugin is absent, aborting in
# under a second with "unrecognized arguments". An earlier version of this file
# omitted it and produced an image whose test suite could not run at all.
#
# pytest itself is pinned: newer versions turn several pydantic-internal
# warnings into collection errors, which would make the baseline non-green.
RUN pip install --no-cache-dir -e . \
    && pip install --no-cache-dir \
        "pytest==8.3.5" \
        pytest-mock \
        pytest-pretty \
        pytest-examples \
        pytest-benchmark \
        "pytest-run-parallel>=0.3.1" \
        dirty-equals \
        eval-type-backport \
        faker \
        jsonschema \
        packaging \
        pytz \
        "coverage[toml]" \
        cloudpickle \
        tzdata \
        ruff

# Pristine snapshot of the TESTS only. These are restored immediately before
# scoring, so tampering with the grading criteria cannot succeed.
#
# Deliberately NOT a copy of pydantic/. An earlier version snapshotted the clean
# source here too, which meant `diff -r /opt/pristine/pydantic /testbed/pydantic`
# printed the injected defect and solved any task in one command. Source is
# restored from the git tag instead (see bench/reset_state.sh), which the agent
# cannot use the same way because its history is re-initialised at task start.
RUN mkdir -p /opt/pristine \
    && cp -a tests /opt/pristine/tests

# Remove every route back to the upstream fix. Without this an agent can simply
# `git log` / `git fetch` and read the answer, silently invalidating results.
RUN rm -rf .git \
    && git init -q . \
    && git config user.email bench@local && git config user.name bench \
    && git add -A && git commit -qm "pydanticbench base" \
    && git tag pydanticbench-base

# Pin the test scope for EVERY pytest invocation inside this image -- the
# agent's exploratory runs and the scorer's grading runs alike. Keeping the
# scope in one place is what stops the baseline and the graded runs from
# drifting apart. Mirrors scripts/pytest_scope.py.
#   tests/pydantic_core -- git-only (absent from the sdist the tasks were built
#                          from) and hypothesis-based, therefore nondeterministic
#   tests/test_docs.py  -- needs the docs/ tree
#   tests/benchmarks    -- timing-sensitive
ENV PYTEST_ADDOPTS="--ignore=tests/pydantic_core --ignore=tests/test_docs.py --ignore=tests/benchmarks"

ENV PYTHONHASHSEED=0 \
    TZ=UTC \
    PIP_PROGRESS_BAR=off \
    TQDM_DISABLE=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY bench/ /opt/bench/
RUN chmod +x /opt/bench/*.sh

# Smoke check: the suite MUST be green in the built image, or every score is
# wrong. The exit code is the authority here -- do not pipe pytest into `tail`
# and test the result, because the pipeline then reports tail's status (0) and a
# completely broken suite builds "successfully". That exact mistake shipped an
# image in which no test could run.
RUN python -m pytest tests/ -q -p no:cacheprovider --tb=short \
        --ignore=tests/pydantic_core --ignore=tests/test_docs.py --ignore=tests/benchmarks \
        > /opt/baseline_summary.txt 2>&1; \
    code=$?; \
    tail -25 /opt/baseline_summary.txt; \
    if [ "$code" -ne 0 ]; then \
        echo "=== BASELINE NOT GREEN (pytest exit $code) -- refusing to build ==="; \
        exit 1; \
    fi; \
    echo "=== baseline green ==="
