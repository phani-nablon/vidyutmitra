#!/usr/bin/env bash
# Start the app: venv + deps (first run), graph load (first run), server on http://127.0.0.1:8077
# JanusGraph must be running first: scripts/janusgraph/start.sh
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || uv venv
uv pip install -q -r requirements.txt -r requirements-optional.txt
uv pip install -q -e .
.venv/bin/sr janus-check
[ -f var/db/index.sqlite ] || .venv/bin/sr import-v1
exec .venv/bin/sr serve "$@"
