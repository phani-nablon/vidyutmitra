// Run once in the Gremlin console against the configured graph:  :load scripts/janusgraph/schema.groovy
// Mirrors substation_retrieval.kg.build: vertex labels, edge labels, the vid/pk keys and their indexes.
mgmt = graph.openManagement()
['document','layout','substation','equipment','opening','structure','route','site_feature','concept','facet','catalogue_item'].each {
  if (!mgmt.containsVertexLabel(it)) mgmt.makeVertexLabel(it).make() }
['HAS_LAYOUT','HAS_SUBSTATION','HAS_FEATURE','SECTION_OF','DRAWN_IN','CONTAINS','ON_WALL','UNDER','BELOW_BEAM','CLEARANCE','FACES',
 'CONNECTS','DISTANCE_TO','IS_A','SUBCLASS_OF','MATCHES_CATALOGUE','HAS_FACET','SIMILAR_TO'].each {
  if (!mgmt.containsEdgeLabel(it)) mgmt.makeEdgeLabel(it).make() }
def key = { name, type -> mgmt.containsPropertyKey(name) ? mgmt.getPropertyKey(name) : mgmt.makePropertyKey(name).dataType(type).make() }
vid = key('vid', String); pk = key('pk', String); cls = key('cls', String)
['name','label','type','kind','wall','polygon_mm','polyline_mm','geo_wkt','key','value','status','method','sha256','installation',
 'arrangement','floor','shape','equipment_type','subtype','kva_source','title','uses','kg_vertex','reason','layout_id','counts',
 'centre_mm','by','at'].each { key(it, String) }
['area_m2','length_mm','width_mm','clear_height_mm','beam_bottom_mm','plinth_mm','kva','L_mm','B_mm','H_mm','weight_kg','width_mm',
 'height_mm','bottom_mm','depth_mm','mm','norm_mm','distance_mm','clearance_mm','score','quality','mm_per_px'].each { key(it, Double) }
['depth','page','substation_count'].each { key(it, Integer) }
['meets_norm','unique','inferred'].each { key(it, Boolean) }
if (!mgmt.containsGraphIndex('byVid')) mgmt.buildIndex('byVid', Vertex.class).addKey(vid).unique().buildCompositeIndex()
if (!mgmt.containsGraphIndex('byPk')) mgmt.buildIndex('byPk', Vertex.class).addKey(pk).buildCompositeIndex()
if (!mgmt.containsGraphIndex('byCls')) mgmt.buildIndex('byCls', Vertex.class).addKey(cls).buildCompositeIndex()
if (!mgmt.containsGraphIndex('edgeByPk')) mgmt.buildIndex('edgeByPk', Edge.class).addKey(pk).buildCompositeIndex()
mgmt.commit()
