#!/bin/bash
set -e

# Usage: eval-gold-once.sh <eval_number> <question>
# Runs Claude Code against the Vite repo to produce a gold-standard answer for one eval question.
# Captures JSON output to extract answer + metrics (timing, tokens, tool calls, cost).

if [ -z "$1" ] || [ -z "$2" ]; then
  echo "Usage: $0 <eval_number> <question>"
  exit 1
fi

EVAL_NUM="$1"
QUESTION="$2"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VITE_DIR="$PROJECT_DIR/repos/vite"
OUTPUT_DIR="$PROJECT_DIR/docs/evals/gold"

mkdir -p "$OUTPUT_DIR"

OUTPUT_FILE="$OUTPUT_DIR/eval-${EVAL_NUM}.md"
RAW_FILE="$OUTPUT_DIR/.eval-${EVAL_NUM}-raw.json"

if [ -f "$OUTPUT_FILE" ]; then
  echo ">>> Skipping eval $EVAL_NUM — $OUTPUT_FILE already exists"
  echo ">>> Delete it to regenerate."
  exit 0
fi

echo "========================================"
echo "  Eval $EVAL_NUM — $(date '+%H:%M:%S')"
echo "  Question: ${QUESTION:0:80}..."
echo "========================================"

START_TIME=$SECONDS

claude --print \
  --output-format json \
  --permission-mode acceptEdits \
  --allowedTools "Bash(git:*)" \
  --allowedTools "Bash(wc:*)" \
  --allowedTools "Bash(ls:*)" \
  --allowedTools "Bash(find:*)" \
  --allowedTools "Bash(grep:*)" \
  --allowedTools "Bash(tree:*)" \
  -p "$(cat <<EOF
You are a senior software engineer doing deep codebase research on the Vite project.
The Vite source code is at: $VITE_DIR

YOUR TASK: Answer the following research question by thoroughly reading the Vite source code.

QUESTION: $QUESTION

INSTRUCTIONS:
- Read the actual source code. Do not guess or rely on general knowledge.
- Trace through the relevant code paths. Follow function calls across files.
- Identify the specific files, functions, and line numbers that are relevant.
- Cover ALL subsystems involved — if the answer spans multiple parts of the codebase, cover each one.
- Be precise about mechanisms: name the actual functions, variables, and code patterns.
- Distinguish between what Vite does vs what Rollup/esbuild/other dependencies do.
- Include file paths and line numbers for every claim.
- Structure your answer with clear sections.
- Your answer will be used as a gold-standard reference, so be thorough and accurate.

FORMAT YOUR ANSWER AS:
## Answer
[Your detailed, evidence-backed answer here with file:line references]

## Key Files
[Bulleted list of the most important files and what role they play]

## Summary
[2-3 sentence executive summary]
EOF
)" > "$RAW_FILE" 2>/dev/null

ELAPSED=$(( SECONDS - START_TIME ))
MINS=$(( ELAPSED / 60 ))
SECS=$(( ELAPSED % 60 ))

# Parse JSON to extract answer + append metrics to the markdown file
python3 "$SCRIPT_DIR/parse_eval_metrics.py" "$RAW_FILE" "$OUTPUT_FILE"

echo ""
echo ">>> Eval $EVAL_NUM complete: ${MINS}m ${SECS}s (wall clock)"
echo ">>> Output: $OUTPUT_FILE"

# Clean up raw JSON (metrics already extracted)
rm -f "$RAW_FILE"
