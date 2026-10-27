# JanusGraph backend (optional, no docker)

The default graph store is SQLite on disk (`var/db/kg.sqlite`). It is persistent and needs no server.
Switch to JanusGraph when the corpus grows or several services must share the graph:
the code talks Gremlin bytecode (`gremlinpython`), so the same store works for JanusGraph locally and on GCP.

## Local (single machine)
1. Install Java 11+ and download the JanusGraph *full* zip (1.0.x) from the JanusGraph releases page; unzip it.
2. Copy `janusgraph-berkeleyje-lucene.properties` into `<janusgraph>/conf/` and point
   `conf/gremlin-server/gremlin-server.yaml` -> `graphs: { graph: conf/janusgraph-berkeleyje-lucene.properties }`.
3. Start: `bin/janusgraph-server.sh console` (Linux/macOS) or `bin\janusgraph-server.bat` (Windows).
4. Create the schema once: `bin/gremlin.sh` then `:remote connect tinkerpop.server conf/remote.yaml session`,
   `:remote console`, `:load <repo>/scripts/janusgraph/schema.groovy`.
5. In the repo: `uv pip install gremlinpython`, set `SR_GRAPH_BACKEND=gremlin` (and `SR_GREMLIN_URL` if not
   `ws://localhost:8182/gremlin`), then `sr rebuild` to load every stored extraction into JanusGraph.

## GCP
Use `janusgraph-bigtable.properties` (Cloud Bigtable storage + Elasticsearch for mixed indexes), run JanusGraph
Server on a GCE VM or GKE, and point `SR_GREMLIN_URL` at it. Nothing in the application changes.

If the Gremlin server is unreachable at start-up the app logs it and falls back to SQLite; in `auto` retrieval
mode a slow or failing graph also falls back to RAG for that query.
