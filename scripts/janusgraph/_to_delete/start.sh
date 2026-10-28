#!/usr/bin/env bash
# Start JanusGraph in the background (ws://127.0.0.1:8182/gremlin); logs in var/janusgraph/server.log
set -euo pipefail
VERSION="${1:-1.1.0}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"; HOME_="$REPO/var/janusgraph/janusgraph-$VERSION"
cd "$HOME_" && nohup bin/janusgraph-server.sh console conf/gremlin-server/gremlin-server-css.yaml > "$REPO/var/janusgraph/server.log" 2>&1 &
for i in $(seq 1 60); do (exec 3<>/dev/tcp/127.0.0.1/8182) 2>/dev/null && { echo "JanusGraph up on 8182"; exit 0; }; sleep 2; done
echo "JanusGraph did not start; see var/janusgraph/server.log"; exit 1
