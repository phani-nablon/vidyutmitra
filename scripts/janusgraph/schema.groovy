// Composite indexes the app relies on (vid lookups, partition deletes). Idempotent.
// The app submits this automatically on start-up (GremlinGraphStore.ensure_schema); you can also run it
// in the Gremlin console:  :remote connect tinkerpop.server conf/remote.yaml session  ->  :> <paste>
mgmt = graph.openManagement()
def key = { n -> mgmt.containsPropertyKey(n) ? mgmt.getPropertyKey(n) : mgmt.makePropertyKey(n).dataType(String.class).make() }
vid = key('vid'); pk = key('pk'); cls = key('cls')
created = []
if (!mgmt.containsGraphIndex('byVid')) { mgmt.buildIndex('byVid', Vertex.class).addKey(vid).buildCompositeIndex(); created << 'byVid' }
if (!mgmt.containsGraphIndex('byPk')) { mgmt.buildIndex('byPk', Vertex.class).addKey(pk).buildCompositeIndex(); created << 'byPk' }
if (!mgmt.containsGraphIndex('byCls')) { mgmt.buildIndex('byCls', Vertex.class).addKey(cls).buildCompositeIndex(); created << 'byCls' }
if (!mgmt.containsGraphIndex('edgeByPk')) { mgmt.buildIndex('edgeByPk', Edge.class).addKey(pk).buildCompositeIndex(); created << 'edgeByPk' }
mgmt.commit()
created
