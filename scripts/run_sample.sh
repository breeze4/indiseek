#!/usr/bin/env bash
# Run a query with a given strategy, stream progress, and save results.
# Usage: run_sample.sh <mode> <output_file> <prompt>
set -euo pipefail

MODE="$1"
OUTFILE="$2"
PROMPT="$3"
API="http://localhost:8000/api"

echo "[$MODE] Submitting query..."

# Build JSON body safely via python
BODY=$(python3 -c "
import json, sys
print(json.dumps({'prompt': sys.argv[1], 'mode': sys.argv[2], 'force': True}))
" "$PROMPT" "$MODE")

# Submit the query (async)
RESPONSE=$(curl -s -X POST "$API/run/query" \
  -H "Content-Type: application/json" \
  -d "$BODY")

echo "[$MODE] Response: $RESPONSE"

# Check for error
if echo "$RESPONSE" | grep -qi "error\|internal"; then
  echo "[$MODE] ERROR: $RESPONSE"
  exit 1
fi

# Check if we got a cached response
if echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if d.get('cached') else 1)" 2>/dev/null; then
  echo "[$MODE] Got cached response, saving..."
  python3 -c "
import json, sys
d = json.loads(sys.argv[1])
out = {
    'mode': sys.argv[2],
    'prompt': sys.argv[3],
    'cached': True,
    'status': 'completed',
    'total_tool_calls': 0,
    'tool_calls': [],
    'answer': d['answer'],
    'evidence': d.get('evidence', [])
}
with open(sys.argv[4], 'w') as f:
    json.dump(out, f, indent=2)
" "$RESPONSE" "$MODE" "$PROMPT" "$OUTFILE"
  echo "[$MODE] Done -> $OUTFILE"
  exit 0
fi

TASK_ID=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['task_id'])")
echo "[$MODE] Task ID: $TASK_ID — streaming..."

# Stream SSE events, collect them all
EVENTS_FILE=$(mktemp)
curl -s -N "$API/tasks/$TASK_ID/stream" | while IFS= read -r line; do
  if [[ "$line" == data:* ]]; then
    DATA="${line#data: }"
    echo "$DATA" >> "$EVENTS_FILE"
    TYPE=$(echo "$DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('type',''))" 2>/dev/null || true)
    if [ "$TYPE" = "progress" ]; then
      TOOL=$(echo "$DATA" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f'[{d.get(\"iteration\",\"?\")}] {d.get(\"tool\",\"?\")}({json.dumps(d.get(\"args\",{}))})')
" 2>/dev/null || true)
      echo "[$MODE]   $TOOL"
    elif [ "$TYPE" = "phase" ]; then
      PHASE=$(echo "$DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null || true)
      echo "[$MODE] === Phase: $PHASE ==="
    elif [ "$TYPE" = "done" ] || [ "$TYPE" = "error" ]; then
      echo "[$MODE] Stream ended: $TYPE"
      break
    fi
  fi
done

# Parse collected events into the output file
python3 -c "
import json, sys

mode = sys.argv[1]
prompt = sys.argv[2]
events_file = sys.argv[3]
outfile = sys.argv[4]

events = []
with open(events_file) as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass

progress = [e for e in events if e.get('type') == 'progress']
phases = [e for e in events if e.get('type') == 'phase']
done_evt = next((e for e in events if e.get('type') == 'done'), None)
error_evt = next((e for e in events if e.get('type') == 'error'), None)

tool_calls = []
for p in progress:
    tool_calls.append({
        'iteration': p.get('iteration'),
        'tool': p.get('tool'),
        'args': p.get('args', {}),
        'summary': p.get('summary', ''),
    })

result = {
    'mode': mode,
    'prompt': prompt,
    'tool_calls': tool_calls,
    'total_tool_calls': len(tool_calls),
}

if phases:
    result['phases'] = [p.get('phase') for p in phases]

if done_evt and done_evt.get('result'):
    r = done_evt['result']
    result['answer'] = r.get('answer', '')
    result['evidence'] = r.get('evidence', [])
    result['status'] = 'completed'
elif error_evt:
    result['error'] = error_evt.get('error', 'Unknown error')
    result['status'] = 'failed'
else:
    result['status'] = 'unknown'

with open(outfile, 'w') as f:
    json.dump(result, f, indent=2)

print(f'[{mode}] Saved to {outfile} ({len(tool_calls)} tool calls)')
" "$MODE" "$PROMPT" "$EVENTS_FILE" "$OUTFILE"

rm -f "$EVENTS_FILE"
