#!/usr/bin/env bash
# Install (first run), start Neo4j, first-run import, then serve on http://127.0.0.1:8077
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || uv venv
uv pip install -q -r requirements.txt -r requirements-optional.txt
uv pip install -q -e .
.venv/bin/sr neo4j-start
[ -f var/db/index.sqlite ] || .venv/bin/sr import-v1
exec .venv/bin/sr serve "$@"
