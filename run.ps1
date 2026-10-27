# Start the app on Windows: venv + deps (first run), graph load (first run), server on http://127.0.0.1:8077
# JanusGraph must be running first:  scripts\janusgraph\start.ps1  (see README)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { uv venv }
uv pip install -q -r requirements.txt
uv pip install -q -r requirements-optional.txt
uv pip install -q -e .
$sr = ".\.venv\Scripts\sr.exe"
& $sr janus-check
if (-not (Test-Path var\db\index.sqlite)) { & $sr import-v1 }
& $sr serve @args
