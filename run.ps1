# Install (first run), start Neo4j, first-run import, then serve on http://127.0.0.1:8077
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { uv venv }
uv pip install -q -r requirements.txt
uv pip install -q -r requirements-optional.txt
uv pip install -q -e .
$sr = ".\.venv\Scripts\sr.exe"
& $sr neo4j-start
if ($LASTEXITCODE -ne 0) { Write-Host "Neo4j did not start - see above (sr neo4j-status)"; exit 1 }
if (-not (Test-Path var\db\index.sqlite)) { & $sr import-v1 }
& $sr serve @args
