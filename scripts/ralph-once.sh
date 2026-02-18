#!/bin/bash
set -e

cd "$(dirname "$0")/.."

mkdir -p .ralph
if [ ! -f .ralph/progress.md ]; then
  echo "# Ralph Progress Log" > .ralph/progress.md
  echo "" >> .ralph/progress.md
  echo "---" >> .ralph/progress.md
fi

START_TIME=$SECONDS

claude --permission-mode acceptEdits \
  --allowedTools "Bash(pip:*)" \
  --allowedTools "Bash(pip3:*)" \
  --allowedTools "Bash(python:*)" \
  --allowedTools "Bash(python3:*)" \
  --allowedTools "Bash(pytest:*)" \
  --allowedTools "Bash(ruff:*)" \
  --allowedTools "Bash(uv:*)" \
  --allowedTools "Bash(npm:*)" \
  --allowedTools "Bash(npx:*)" \
  --allowedTools "Bash(node:*)" \
  --allowedTools "Bash(curl:*)" \
  --allowedTools "Bash(uvicorn:*)" \
  --allowedTools "Bash(sqlite3:*)" \
  --allowedTools "Bash(protoc:*)" \
  --allowedTools "Bash(wget:*)" \
  --allowedTools "Bash(bash:*)" \
  --allowedTools "Bash(sh:*)" \
  --allowedTools "Bash(git:*)" \
  --allowedTools "Bash(ls:*)" \
  --allowedTools "Bash(mkdir:*)" \
  --allowedTools "Bash(cp:*)" \
  --allowedTools "Bash(mv:*)" \
  --allowedTools "Bash(rm:*)" \
  --allowedTools "Bash(chmod:*)" \
  --allowedTools "Bash(touch:*)" \
  --allowedTools "Bash(cat:*)" \
  --allowedTools "Bash(head:*)" \
  --allowedTools "Bash(tail:*)" \
  --allowedTools "Bash(wc:*)" \
  --allowedTools "Bash(find:*)" \
  --allowedTools "Bash(grep:*)" \
  --allowedTools "Bash(tree:*)" \
  --allowedTools "Bash(diff:*)" \
  --allowedTools "Bash(echo:*)" \
  --allowedTools "Bash(which:*)" \
  --allowedTools "Bash(sort:*)" \
  --allowedTools "Bash(sed:*)" \
  -p "$(cat <<'EOF'
@docs/plans/agent-loop-tier1.md
@.ralph/progress.md

You are an autonomous implementation agent working through docs/plans/agent-loop-tier1.md.

1. Read .ralph/progress.md to understand what has already been done.
2. Read docs/plans/agent-loop-tier1.md. Find the NEXT INCOMPLETE STEP — first ### Step with unchecked verification items.
3. Implement that step completely:
   - All listed changes, in order.
   - Write tests. Run them. Fix failures.
   - Run every verification check listed in the step (pytest, ruff).
   - Mark each passing check [x] in docs/plans/agent-loop-tier1.md.
4. Append a progress entry to .ralph/progress.md: step name, files created/modified, test results, verification results, issues, git commit SHA.
5. Git commit with a descriptive message.

CRITICAL: Only work on ONE STEP per session.
If ALL steps have [x] on every checklist item, output the exact text RALPH_DONE
EOF
)"

ELAPSED=$(( SECONDS - START_TIME ))
MINS=$(( ELAPSED / 60 ))
SECS=$(( ELAPSED % 60 ))
echo ""
echo ">>> Session took ${MINS}m ${SECS}s"
echo "" >> .ralph/progress.md
echo "_Session duration: ${MINS}m ${SECS}s — $(date '+%Y-%m-%d %H:%M:%S')_" >> .ralph/progress.md
