#!/usr/bin/env bash
# Build the MCPB bundle into dist/ — the ONE packing path, shared by
# local builds and CI, so the two can't drift. Version comes from
# pyproject.toml (the contract tests already pin manifest.json to it).
#
# Requires: node/npx (for the mcpb CLI). Network on first run only.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 - <<'EOF'
import re
print(re.search(r'^version = "([^"]+)"', open("pyproject.toml").read(), re.M).group(1))
EOF
)

mkdir -p dist
npx -y @anthropic-ai/mcpb validate manifest.json
npx -y @anthropic-ai/mcpb pack . "dist/gnucash-mcp-${VERSION}.mcpb"
echo "Built dist/gnucash-mcp-${VERSION}.mcpb"
