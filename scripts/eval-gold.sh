#!/bin/bash
set -e

# Runs all 6 eval questions through Claude Code to generate gold-standard answers.
# Each question gets a fresh Claude context (clean eval).
# Skips evals that already have output files (delete to regenerate).
# Captures per-eval metrics (timing, tokens, tool calls, cost) as JSON.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="$PROJECT_DIR/docs/evals/gold"

mkdir -p "$OUTPUT_DIR"

LOOP_START=$SECONDS

# Eval questions from docs/plans/multi-agent-evals.md
declare -a QUESTIONS=(
  "Why does Vite sometimes produce different output file hashes when building the same source code multiple times? What is the root cause and what workarounds exist?"
  "How does Vite's HMR system handle modules that have circular imports? Trace what happens when a file that participates in a circular dependency is edited."
  "Why does vite build --watch perform a complete rebuild on every file change instead of doing an incremental rebuild like webpack? What would need to change for incremental builds to work?"
  "How does Vite's ssrTransform rewrite module imports for SSR, and why can it produce syntax errors when processing minified code?"
  "How does Vite handle asset references inside CSS url() declarations? Why don't Vite plugins receive resolveId and load hook calls for these assets, and what workarounds exist?"
  "Why does Vite generate empty source maps (with no sources or mappings) when optimizing dependencies with esbuild? What is the expected behavior?"
)

TOTAL=${#QUESTIONS[@]}
COMPLETED=0
SKIPPED=0

echo "========================================"
echo "  Gold Standard Eval Generation"
echo "  $TOTAL questions, output: docs/evals/gold/"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"
echo ""

for i in "${!QUESTIONS[@]}"; do
  EVAL_NUM=$((i + 1))
  QUESTION="${QUESTIONS[$i]}"

  "$SCRIPT_DIR/eval-gold-once.sh" "$EVAL_NUM" "$QUESTION"
  EXIT_CODE=$?

  if [ $EXIT_CODE -eq 0 ]; then
    if [ -f "$OUTPUT_DIR/eval-${EVAL_NUM}.md" ]; then
      COMPLETED=$((COMPLETED + 1))
    fi
  else
    echo ">>> ERROR: Eval $EVAL_NUM failed with exit code $EXIT_CODE"
  fi

  echo ""
done

TOTAL_ELAPSED=$(( SECONDS - LOOP_START ))
TOTAL_MINS=$(( TOTAL_ELAPSED / 60 ))
TOTAL_SECS=$(( TOTAL_ELAPSED % 60 ))

echo "========================================"
echo "  Done: $COMPLETED/$TOTAL evals generated"
echo "  Total wall time: ${TOTAL_MINS}m ${TOTAL_SECS}s"
echo "  Output: docs/evals/gold/"
echo "========================================"

# Per-eval metrics are embedded in each eval-N.md file under "## Eval Metrics"
