#!/usr/bin/env bash
# PydanticBench -- stage 4: run mini-swe-agent across model configurations,
# then score each run.
#
# Prerequisites:
#   - docker daemon running
#   - pip install mini-swe-agent
#   - an API key for whichever provider MODEL_SET selects
#
# Usage:
#   MODEL_SET=anthropic ANTHROPIC_API_KEY=sk-...  bash scripts/04_run_benchmark.sh
#   MODEL_SET=gemini    GEMINI_API_KEY=...        bash scripts/04_run_benchmark.sh
#   MODEL_SET=mixed     (needs both keys)         bash scripts/04_run_benchmark.sh
#
# Never hard-code keys in this file. They are read from the environment only.
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL_SET="${MODEL_SET:-anthropic}"
WORKERS="${WORKERS:-8}"
CONFIG=configs/pydanticbench.yaml
DATASET=tasks/hf
export MSWEA_GLOBAL_COST_LIMIT="${MSWEA_GLOBAL_COST_LIMIT:-200}"

# Model sets. Any litellm-supported model string works, so extending this is a
# one-line change. The default is a single-vendor capability ladder, so score
# differences are attributable to model strength rather than API quirks.
case "$MODEL_SET" in
  anthropic)
    : "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}"
    NAMES=(haiku sonnet opus)
    MODELS=("anthropic/claude-haiku-4-5-20251001"
            "anthropic/claude-sonnet-4-5-20250929"
            "anthropic/claude-opus-4-1-20250805")
    ;;
  gemini)
    : "${GEMINI_API_KEY:?set GEMINI_API_KEY}"
    NAMES=(gemini-flash-lite gemini-flash gemini-pro)
    MODELS=("gemini/gemini-2.5-flash-lite"
            "gemini/gemini-2.5-flash"
            "gemini/gemini-2.5-pro")
    ;;
  mixed)
    : "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}"
    : "${GEMINI_API_KEY:?set GEMINI_API_KEY}"
    NAMES=(haiku sonnet gemini-pro)
    MODELS=("anthropic/claude-haiku-4-5-20251001"
            "anthropic/claude-sonnet-4-5-20250929"
            "gemini/gemini-2.5-pro")
    ;;
  *) echo "unknown MODEL_SET: $MODEL_SET" >&2; exit 1 ;;
esac

for i in "${!NAMES[@]}"; do
  name="${NAMES[$i]}"; model="${MODELS[$i]}"
  echo "=== running ${name} (${model}) ==="
  mini-extra swebench \
    --subset "$DATASET" --split train \
    --model "$model" \
    --config "$CONFIG" \
    --environment-class docker \
    --workers "$WORKERS" \
    --output "results/${name}"

  if [ ! -f "results/${name}/preds.json" ]; then
    echo "!!! ${name} produced no predictions -- skipping scoring" >&2
    continue
  fi

  echo "=== scoring ${name} ==="
  python3 scripts/05_score.py \
    --tasks tasks/tasks.jsonl \
    --preds "results/${name}/preds.json" \
    --baseline tasks/baseline_failures.json \
    --backend docker \
    --out "results/${name}/scores.json"
done

python3 scripts/06_report.py --results results --out results
echo "done -- see results/summary.md"
