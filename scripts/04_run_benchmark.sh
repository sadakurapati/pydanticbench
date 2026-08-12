#!/usr/bin/env bash
# PydanticBench -- non-interactive benchmark runner.
#
# This is a thin wrapper around ./run.sh. It used to be a second, parallel
# implementation with its own model list, and the two drifted: run.sh was
# updated to current model identifiers while this file kept pointing at retired
# ones. Two sources of truth for the same thing is how that happens, so there is
# now only one.
#
#   MODEL_SET is replaced by PROVIDER; everything else is identical.
#
#   PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... SCOPE=full bash scripts/04_run_benchmark.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec env ASSUME_YES=1 \
  PROVIDER="${PROVIDER:-${MODEL_SET:-anthropic}}" \
  SCOPE="${SCOPE:-full}" \
  WORKERS="${WORKERS:-8}" \
  ./run.sh
