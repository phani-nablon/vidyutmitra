# First run on Windows: create the uv venv, install, import the v1 corpus, start the server.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { uv venv }
uv pip install -q -r requirements.txt
uv pip install -q -e .
if (-not (Test-Path var\db\index.sqlite)) { .\.venv\Scripts\sr.exe import-v1 }
.\.venv\Scripts\sr.exe serve @args
