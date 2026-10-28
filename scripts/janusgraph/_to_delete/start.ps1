# Start JanusGraph (Gremlin Server on ws://127.0.0.1:8182/gremlin) in this window. Ctrl+C stops it.
param([string]$Version = "1.1.0", [string]$Heap = "1g")
$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$Home_ = Join-Path $Repo "var\janusgraph\janusgraph-$Version"
if (-not (Test-Path $Home_)) { Write-Host "Run scripts\janusgraph\setup.ps1 first" -ForegroundColor Yellow; exit 1 }
Set-Location $Home_
$env:JANUSGRAPH_HOME = $Home_
$env:HADOOP_HOME = $Home_
$env:JAVA_ARGS = "-Xmx$Heap"
& "$Home_\bin\gremlin-server.bat" "$Home_\conf\gremlin-server\gremlin-server-css.yaml"
