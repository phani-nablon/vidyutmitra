# JanusGraph (default knowledge-graph store, no docker)

The app talks to JanusGraph over Gremlin (`ws://127.0.0.1:8182/gremlin`, `gremlinpython` bytecode).
Storage is embedded BerkeleyDB-JE with a Lucene index, kept under `var/janusgraph/data` (gitignored).
On first connect the app creates the composite indexes it needs (`schema.groovy`: vid, pk, cls, edge pk).

## Windows
1. Java 17: `winget install Microsoft.OpenJDK.17` (open a new terminal afterwards).
2. `powershell -ExecutionPolicy Bypass -File scripts\janusgraph\setup.ps1`
   downloads JanusGraph 1.1.0 (~400 MB, once) and `winutils.exe`, writes the config.
3. `powershell -ExecutionPolicy Bypass -File scripts\janusgraph\start.ps1` (leave the window open).
4. In another terminal: `sr janus-check`, then `sr import-v1` (or `sr rebuild`) to load the graph.

## Linux / macOS
`scripts/janusgraph/setup.sh` then `scripts/janusgraph/start.sh`.

## GCP
Point `graph.gremlin_url` (or `SR_GREMLIN_URL`) at a JanusGraph Server whose graph uses Cloud Bigtable
(`janusgraph-bigtable.properties`) and Elasticsearch for mixed indexes. Nothing in the app changes.

## If JanusGraph is down
The KG engine reports unavailable; `auto` retrieval mode answers with RAG and says so in the trace;
ingestion jobs fail with a clear message; the ontology view still works. `graph.backend: sqlite` exists only
for tests and offline work.
