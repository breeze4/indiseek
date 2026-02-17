#!/bin/bash
set -e

if [ -z "$1" ]; then
  echo "Usage: $0 <iterations>"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
loop_start=$SECONDS

for ((i=1; i<=$1; i++)); do
  echo "========================================"
  echo "  Iteration $i / $1 — $(date '+%H:%M:%S')"
  echo "========================================"

  iter_start=$SECONDS
  result=$("$SCRIPT_DIR/ralph-once.sh" 2>&1) || true
  iter_elapsed=$(( SECONDS - iter_start ))
  iter_mins=$(( iter_elapsed / 60 ))
  iter_secs=$(( iter_elapsed % 60 ))

  echo "$result"
  echo ""
  echo ">>> Iteration $i took ${iter_mins}m ${iter_secs}s"

  if [[ "$result" == *"RALPH_DONE"* ]]; then
    total=$(( SECONDS - loop_start ))
    echo ""
    echo "=== PLAN COMPLETE after $i iterations ($(( total / 60 ))m $(( total % 60 ))s total) ==="
    exit 0
  fi
done

total=$(( SECONDS - loop_start ))
echo ""
echo "=== Completed $1 iterations ($(( total / 60 ))m $(( total % 60 ))s total) ==="
