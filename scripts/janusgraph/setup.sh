#!/usr/bin/env bash
# One-time JanusGraph setup on Linux/macOS (no docker). Needs Java 11+ on PATH.
set -euo pipefail
VERSION="${1:-1.1.0}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$REPO/var/janusgraph"; HOME_="$ROOT/janusgraph-$VERSION"
mkdir -p "$ROOT"
java -version >/dev/null 2>&1 || { echo "Java not found: install a JDK 17"; exit 1; }
if [ ! -d "$HOME_" ]; then
  [ -f "$ROOT/jg.zip" ] || curl -fL -o "$ROOT/jg.zip" "https://github.com/JanusGraph/janusgraph/releases/download/v$VERSION/janusgraph-$VERSION.zip"
  (cd "$ROOT" && unzip -q jg.zip)
fi
cat > "$HOME_/conf/css-berkeleyje-lucene.properties" <<P
gremlin.graph=org.janusgraph.core.JanusGraphFactory
storage.backend=berkeleyje
storage.directory=$ROOT/data/berkeley
index.search.backend=lucene
index.search.directory=$ROOT/data/lucene
schema.default=default
P
sed 's#graph: conf/janusgraph-inmemory.properties#graph: conf/css-berkeleyje-lucene.properties#; s#^host: 0.0.0.0#host: 127.0.0.1#' \
  "$HOME_/conf/gremlin-server/gremlin-server.yaml" > "$HOME_/conf/gremlin-server/gremlin-server-css.yaml"
sed -i.bak 's/-Xms4096m/-Xms512m/; s/-Xmx4096m/-Xmx1536m/' "$HOME_/conf/jvm-11.options"
echo "JanusGraph ready at $HOME_ ; start with scripts/janusgraph/start.sh"
