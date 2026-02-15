#!/usr/bin/env bash
# Generate a SCIP index for a TypeScript repository using scip-typescript.
#
# Prerequisites:
#   - Node.js >= 18
#   - npm
#
# Usage:
#   bash scripts/generate_scip.sh /path/to/repo
#
# Output:
#   Creates index.scip in the repository root.

set -euo pipefail

REPO_PATH="${1:?Usage: $0 /path/to/repo}"

if [ ! -d "$REPO_PATH" ]; then
  echo "Error: $REPO_PATH is not a directory" >&2
  exit 1
fi

# Check prerequisites
if ! command -v node &> /dev/null; then
  echo "Error: Node.js is required. Install it from https://nodejs.org/" >&2
  exit 1
fi

if ! command -v npx &> /dev/null; then
  echo "Error: npx is required (comes with npm)." >&2
  exit 1
fi

echo "Generating SCIP index for: $REPO_PATH"

cd "$REPO_PATH"

# Install scip-typescript locally if not present
if ! npx --no-install scip-typescript --version &> /dev/null; then
  echo "Installing @sourcegraph/scip-typescript..."
  npm install --no-save @sourcegraph/scip-typescript
fi

# Run scip-typescript index
echo "Running scip-typescript index..."
npx scip-typescript index --infer-tsconfig

if [ -f "$REPO_PATH/index.scip" ]; then
  SIZE=$(du -h "$REPO_PATH/index.scip" | cut -f1)
  echo "Success: $REPO_PATH/index.scip ($SIZE)"
else
  echo "Error: index.scip was not created" >&2
  exit 1
fi
