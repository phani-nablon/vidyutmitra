# One-time JanusGraph setup on Windows (no docker). Needs Java 11 or 17 on PATH.
#   powershell -ExecutionPolicy Bypass -File scripts\janusgraph\setup.ps1
# Installs into <repo>\var\janusgraph (gitignored); data lives in <repo>\var\janusgraph\data.
param([string]$Version = "1.1.0")
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$Root = Join-Path $Repo "var\janusgraph"
$Home_ = Join-Path $Root "janusgraph-$Version"
New-Item -ItemType Directory -Force -Path $Root | Out-Null

try { $jv = (& java -version 2>&1 | Select-Object -First 1) } catch { $jv = $null }
if (-not $jv) {
  Write-Host "Java not found. Install a JDK 17, e.g.:  winget install Microsoft.OpenJDK.17   then open a new terminal." -ForegroundColor Yellow
  exit 1
}
Write-Host "Java: $jv"

if (-not (Test-Path $Home_)) {
  $zip = Join-Path $Root "janusgraph-$Version.zip"
  if (-not (Test-Path $zip)) {
    Write-Host "Downloading JanusGraph $Version (~400 MB)..."
    Invoke-WebRequest "https://github.com/JanusGraph/janusgraph/releases/download/v$Version/janusgraph-$Version.zip" -OutFile $zip
  }
  Write-Host "Extracting..."
  Expand-Archive -Path $zip -DestinationPath $Root -Force
}

# hadoop winutils.exe is required by the Windows launcher
$winutils = Join-Path $Home_ "bin\winutils.exe"
if (-not (Test-Path $winutils)) {
  Invoke-WebRequest "https://github.com/cdarlint/winutils/raw/master/hadoop-2.8.5/bin/winutils.exe" -OutFile $winutils
}

# graph config: embedded BerkeleyDB-JE storage + Lucene index, data under var\janusgraph\data
$data = (Join-Path $Root "data").Replace("\", "/")
@"
gremlin.graph=org.janusgraph.core.JanusGraphFactory
storage.backend=berkeleyje
storage.directory=$data/berkeley
index.search.backend=lucene
index.search.directory=$data/lucene
schema.default=default
"@ | Set-Content -Encoding ascii (Join-Path $Home_ "conf\css-berkeleyje-lucene.properties")

$yaml = Get-Content (Join-Path $Home_ "conf\gremlin-server\gremlin-server.yaml") -Raw
$yaml = $yaml -replace "graph: conf/janusgraph-inmemory.properties", "graph: conf/css-berkeleyje-lucene.properties"
$yaml = $yaml -replace "(?m)^host: 0.0.0.0", "host: 127.0.0.1"
$yaml | Set-Content -Encoding ascii (Join-Path $Home_ "conf\gremlin-server\gremlin-server-css.yaml")

Write-Host "JanusGraph ready at $Home_" -ForegroundColor Green
Write-Host "Start it with:  powershell -ExecutionPolicy Bypass -File scripts\janusgraph\start.ps1"
