# JanusGraph (default knowledge-graph store, no docker)

Managed by the `sr` command; no launcher scripts, no HADOOP_HOME, no winutils:

```
sr janus-start     # first time: checks Java, downloads JanusGraph 1.1.0 (~400 MB) into var/janusgraph, writes config;
                   # then starts it in the background and waits until ws://127.0.0.1:8182/gremlin answers
sr janus-status    # up / down, Java version, log tail (var/janusgraph/server.log)
sr janus-stop
```

Any `sr` command that needs the graph (`serve`, `import-v1`, `rebuild`, `search`, ...) starts JanusGraph by itself
when it is configured on localhost and not running (`graph.autostart: true`).

Java: an installed Java 11+ (JAVA_HOME or PATH) is used; if there is none, `sr janus-start` downloads a portable
Eclipse Temurin 17 runtime into `var/jre` (~46 MB, no admin rights).
Storage: embedded BerkeleyDB-JE + Lucene under `var/janusgraph/data`. On connect the app creates the composite
indexes in `schema.groovy` (vid, pk, cls, edge pk).

GCP: point `graph.gremlin_url` (or `SR_GREMLIN_URL`) at a JanusGraph Server using Cloud Bigtable
(`janusgraph-bigtable.properties`) + Elasticsearch; autostart is skipped for non-local URLs.
