#!/usr/bin/env bash
# First run: create the uv venv, install, import the v1 corpus, start the server.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || uv venv
uv pip install -q -r requirements.txt
uv pip install -q -e .
[ -f var/db/index.sqlite ] || .venv/bin/sr import-v1
exec .venv/bin/sr serve "$@"
