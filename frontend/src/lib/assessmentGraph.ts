/* Graph-integration Phase 1 (#34): map an orchestrator ("Ask Anything") assessment's
 * entity_graph into the SAME EntityGraphResponse shape that EntityGraphSection
 * consumes for typed Company/Person/Sector searches — so the deep-analysis path
 * gets the full graph panel (summary + Save/Find-Similar/Discover/Risk), one renderer. */

import type { EntityGraphResponse, GraphNode, GraphEdge, ImpactAssessmentResult } from '../types';

// Mirror src/common/graph_helpers.py ENTITY_COLORS so orchestrator graphs match
// the colours of the /api/entity-graph path.
const ENTITY_COLORS: Record<string, string> = {
  company: '#58a6ff',
  person: '#a371f7',
  government: '#DC143C',
  vessel: '#3fb950',
  sanctions_list: '#F85149',
  theme: '#F0883E',
  sector: '#f0883e',
};

function truncate(s: string, n = 28): string {
  return s.length <= n ? s : s.slice(0, n - 1) + '…';
}

export function assessmentToEntityGraph(
  data: ImpactAssessmentResult,
  query = 'Assessment entity graph',
): EntityGraphResponse | null {
  const eg = data.entity_graph;
  if (!eg?.entities?.length) return null;

  const ids = new Set(eg.entities.map((e) => e.id));
  // degree over relationships whose endpoints are both present
  const degree: Record<string, number> = {};
  eg.entities.forEach((e) => (degree[e.id] = 0));
  const rels = (eg.relationships ?? []).filter((r) => ids.has(r.source_id) && ids.has(r.target_id));
  rels.forEach((r) => {
    degree[r.source_id] = (degree[r.source_id] || 0) + 1;
    degree[r.target_id] = (degree[r.target_id] || 0) + 1;
  });

  const nodes: GraphNode[] = eg.entities.map((e) => {
    const group = e.entity_type || 'company';
    return {
      id: e.id,
      label: truncate(e.name),
      title: `${e.name}\n${group}${e.country ? ` · ${e.country}` : ''}`,
      group,
      color: ENTITY_COLORS[group] ?? '#808080',
      value: 1 + (degree[e.id] || 0), // #28 degree-based sizing
    };
  });

  const edges: GraphEdge[] = rels.map((r) => ({
    from: r.source_id,
    to: r.target_id,
    label: r.relationship_type.replace(/_/g, ' '),
    arrows: 'to',
    dashes: r.relationship_type.includes('sanction') || r.relationship_type.includes('linked'),
    width: 2,
  }));

  // summary digest (same shape as the #28 backend _graph_summary)
  const by_type: Record<string, number> = {};
  let sanctioned = 0;
  nodes.forEach((n) => {
    by_type[n.group] = (by_type[n.group] || 0) + 1;
    if (n.group === 'sanctions_list') sanctioned += 1;
  });

  return {
    nodes,
    edges,
    meta: {
      query,
      node_count: nodes.length,
      edge_count: edges.length,
      summary: {
        by_type,
        sanctioned_count: sanctioned,
        high_risk_count: 0,
        high_risk_entities: [],
      },
    },
  };
}
