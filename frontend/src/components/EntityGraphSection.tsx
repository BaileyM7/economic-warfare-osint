import { useCallback, useRef, useState } from 'react'
import GraphViewer, { type GraphViewerHandle } from './GraphViewer'
import GraphMapView from './GraphMapView'
import GraphMatrixView from './GraphMatrixView'
import UBOPanel from './UBOPanel'
import RiskReportPanel from './RiskReportPanel'
import { fetchSayariResolve, fetchSayariRelated, fetchEntityRiskReport, fetchSanctionsScreenBatch, saveKnowledgeEntity, saveKnowledgeEdge, fetchSimilarEntities, discoverActions } from '../api'
import type { SimilarEntitiesResponse, DiscoverActionsResponse } from '../api'
import type { EntityGraphResponse, GraphNode, GraphEdge, SayariUBOOwner, EntityRiskReport } from '../types'

const LEGEND = [
  { label: 'Company', color: '#4386c3' },
  { label: 'Person', color: '#a9d8fb' },
  { label: 'Government', color: '#d23c3a' },
  { label: 'Sanctioned', color: '#d23c3a' },
  { label: 'Vessel', color: '#0092ff' },
  { label: 'Sector', color: '#4386c3' },
  { label: 'Theme/Offshore', color: '#efb16a' },
  { label: 'Sayari', color: '#ff5a58' },
]

const SANCTIONED_COLOR = '#d23c3a'

const SAYARI_ENTITY_COLORS: Record<string, string> = {
  company: '#4386c3',
  person: '#a9d8fb',
  vessel: '#0092ff',
  asset: '#efb16a',
}

function truncate(s: string, n = 28) {
  return s.length <= n ? s : s.slice(0, n - 1) + '\u2026'
}

function fullName(node: GraphNode): string {
  let name = ''
  if (node.title) name = node.title.split('\n')[0].trim()
  if (!name) name = node.label.replace(/\u2026$/, '')
  name = name.replace(/\s*\([^)]*\)\s*$/, '').trim()
  return name
}

function extractTickerFromNode(node: GraphNode): string | undefined {
  const text = node.title || node.label
  const m = text.match(/\(([A-Z]{1,5}(?:\.[A-Z]{2})?)\)/)
  return m ? m[1] : undefined
}

interface Props {
  graphData: EntityGraphResponse | null
  graphLoading: boolean
  uboOwners?: SayariUBOOwner[]
  uboLoading?: boolean
  uboTargetName?: string
  // Opt-in (#37): when provided (Knowledge Graph page), the node action bar shows
  // a "Remove" button that deletes the entity from the shared store.
  onRemoveNode?: (entityId: string, name: string) => void | Promise<void>
  heading?: string
}

export default function EntityGraphSection({
  graphData,
  graphLoading,
  uboOwners = [],
  uboLoading = false,
  uboTargetName = '',
  onRemoveNode,
  heading = 'Entity Relationship Graph',
}: Props) {
  const graphRef = useRef<GraphViewerHandle>(null)
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set())
  const [expandingNode, setExpandingNode] = useState<string | null>(null)
  const [addedCounts, setAddedCounts] = useState({ nodes: 0, edges: 0 })
  const [expandMessage, setExpandMessage] = useState<string | null>(null)

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)

  const [riskReport, setRiskReport] = useState<EntityRiskReport | null>(null)
  const [riskReportLoading, setRiskReportLoading] = useState(false)
  const [riskReportEntityName, setRiskReportEntityName] = useState<string | null>(null)

  // Knowledge store (#29): which nodes the analyst has saved this session, and
  // which save is in flight, so the action bar can reflect state without a refetch.
  const [savedNodeIds, setSavedNodeIds] = useState<Set<string>>(new Set())
  const [savingNode, setSavingNode] = useState<string | null>(null)
  // Whole-graph save in flight, and a short-lived summary of what the last save
  // persisted ("3 entities · 2 relationships") so the connection-preserving
  // behaviour is visible to the analyst.
  const [savingAll, setSavingAll] = useState(false)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)

  // Similarity (#30): "find similar entities" results for the selected node.
  const [similar, setSimilar] = useState<SimilarEntitiesResponse | null>(null)
  const [similarLoading, setSimilarLoading] = useState(false)

  // Graph view mode (#35 scaffold): 'clustered' is live; focus/map/matrix land in
  // later phases. Kept here so the switcher and the renderer share one source.
  const [viewMode, setViewMode] = useState<'clustered' | 'focus' | 'map' | 'matrix'>('clustered')

  // Discovery / target generation (#31): proposed-action input + suggestions.
  const [discoverOpen, setDiscoverOpen] = useState(false)
  const [discoverAction, setDiscoverAction] = useState('')
  const [discover, setDiscover] = useState<DiscoverActionsResponse | null>(null)
  const [discoverLoading, setDiscoverLoading] = useState(false)

  const knownIdsRef = useRef<Set<string>>(new Set())
  const allNodesMap = useRef<Map<string, GraphNode>>(new Map())
  // Every edge currently shown (base graph + Sayari expansions). We track these
  // ourselves because expansions live only inside Cytoscape otherwise, and saving
  // an entity's *connections* needs the edge list, not just the nodes.
  const allEdgesRef = useRef<GraphEdge[]>([])

  const expandedRef = useRef(expandedNodes)
  expandedRef.current = expandedNodes
  const expandingRef = useRef(expandingNode)
  expandingRef.current = expandingNode

  const baseNodes = graphData?.nodes ?? []
  const baseEdges = graphData?.edges ?? []

  const prevQueryRef = useRef(graphData?.meta?.query)
  if (graphData?.meta?.query !== prevQueryRef.current) {
    prevQueryRef.current = graphData?.meta?.query
    knownIdsRef.current = new Set(baseNodes.map((n) => n.id))
    const map = new Map<string, GraphNode>()
    for (const n of baseNodes) map.set(n.id, n)
    allNodesMap.current = map
    allEdgesRef.current = [...baseEdges]
    setExpandedNodes(new Set())
    setExpandingNode(null)
    setAddedCounts({ nodes: 0, edges: 0 })
    setExpandMessage(null)
    // Auto-select the central (first) node so the Sayari/Risk action bar is
    // visible as soon as the graph renders. Backends order nodes with the
    // central entity first (see api.py::person_profile, vessel_track, etc.).
    setSelectedNodeId(baseNodes[0]?.id ?? null)
    setRiskReport(null)
    setRiskReportLoading(false)
    setRiskReportEntityName(null)
    setSavedNodeIds(new Set())
    setSavingNode(null)
    setSavingAll(false)
    setSaveMessage(null)
    setSimilar(null)
    setSimilarLoading(false)
    setDiscoverOpen(false)
    setDiscoverAction('')
    setDiscover(null)
    setDiscoverLoading(false)
  } else if (knownIdsRef.current.size === 0 && baseNodes.length > 0) {
    knownIdsRef.current = new Set(baseNodes.map((n) => n.id))
    for (const n of baseNodes) allNodesMap.current.set(n.id, n)
    allEdgesRef.current = [...baseEdges]
  }

  const handleNodeClick = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId)
  }, [])

  const handleSayariExpand = useCallback(async (nodeId: string) => {
    if (expandedRef.current.has(nodeId) || expandingRef.current) return
    const currentKnown = knownIdsRef.current
    if (!currentKnown.has(nodeId)) return
    const clickedNode = allNodesMap.current.get(nodeId)
    if (!clickedNode) return

    setExpandingNode(nodeId)
    setExpandMessage(null)
    try {
      const nameForResolve = fullName(clickedNode)
      let sayariEntityId = clickedNode.sayariId
      const groupToType: Record<string, string> = { company: 'company', person: 'person', vessel: 'vessel' }
      const sayariType = groupToType[clickedNode.group]

      if (!sayariEntityId) {
        let resolved = await fetchSayariResolve(nameForResolve, sayariType)
        if (resolved.entities.length === 0 && sayariType) {
          resolved = await fetchSayariResolve(nameForResolve)
        }
        if (resolved.entities.length === 0) {
          setExpandMessage(`No Sayari results for "${nameForResolve}"`)
          setExpandedNodes((prev) => new Set(prev).add(nodeId))
          setExpandingNode(null)
          return
        }
        sayariEntityId = resolved.entities[0].entity_id
      }

      const traversal = await fetchSayariRelated(sayariEntityId, 1, 10)
      const newNodes: GraphNode[] = []
      const newEdges: GraphEdge[] = []
      const entityNameById = new Map<string, string>()

      for (const ent of traversal.entities) {
        const nid = `sayari_${ent.entity_id}`
        if (!currentKnown.has(nid)) {
          const baseColor = ent.sanctioned ? SANCTIONED_COLOR : SAYARI_ENTITY_COLORS[ent.type] ?? '#ff5a58'
          const tooltip = [ent.label, ent.type, ent.country, ent.sanctioned ? 'SANCTIONED (Sayari)' : null, ent.pep ? 'PEP' : null].filter(Boolean).join(' \u00B7 ')
          const node: GraphNode = { id: nid, label: truncate(ent.label), title: tooltip, group: ent.type || 'sayari', color: baseColor, sayariId: ent.entity_id, value: 3, riskLevel: ent.sanctioned ? 'HIGH' : undefined }
          newNodes.push(node)
          currentKnown.add(nid)
          allNodesMap.current.set(nid, node)
          entityNameById.set(nid, ent.label)
        }
      }

      for (const rel of traversal.relationships) {
        const fromId = currentKnown.has(`sayari_${rel.source_id}`) ? `sayari_${rel.source_id}` : nodeId
        const toId = `sayari_${rel.target_id}`
        if (currentKnown.has(fromId) && currentKnown.has(toId) && fromId !== toId) {
          newEdges.push({ from: fromId, to: toId, label: rel.relationship_type.replace(/_/g, ' '), arrows: 'to', dashes: false })
        }
      }

      for (const nn of newNodes) {
        const hasEdge = newEdges.some((e) => e.to === nn.id || e.from === nn.id)
        if (!hasEdge) {
          newEdges.push({ from: nodeId, to: nn.id, label: 'related', arrows: 'to', dashes: true })
        }
      }

      if (newNodes.length > 0) {
        graphRef.current?.addData(newNodes, newEdges, nodeId)
        // Keep our edge list in sync with what the viewer now shows, so these
        // Sayari connections are included when the analyst saves the graph.
        allEdgesRef.current.push(...newEdges)
        setAddedCounts((prev) => ({ nodes: prev.nodes + newNodes.length, edges: prev.edges + newEdges.length }))
        setExpandMessage(null)

        const namesToScreen = Array.from(entityNameById.values())
        if (namesToScreen.length > 0) {
          try {
            const screening = await fetchSanctionsScreenBatch(namesToScreen)
            const nodeUpdates: Array<{ id: string; color: string; title: string }> = []
            for (const [nid, name] of entityNameById.entries()) {
              const result = screening.results[name]
              if (result?.sanctioned) {
                const node = allNodesMap.current.get(nid)
                const newTitle = (node?.title || name) + `\nSANCTIONED (${result.lists?.join(', ') || 'OFAC/CSL'})`
                nodeUpdates.push({ id: nid, color: SANCTIONED_COLOR, title: newTitle })
                if (node) { node.color = SANCTIONED_COLOR; node.title = newTitle; allNodesMap.current.set(nid, node) }
              }
            }
            if (nodeUpdates.length > 0) graphRef.current?.updateNodes(nodeUpdates)
          } catch (err) {
            console.warn('Sanctions screening failed (non-blocking):', err)
          }
        }
      } else {
        setExpandMessage(`No new connections found for "${nameForResolve}"`)
      }
      setExpandedNodes((prev) => new Set(prev).add(nodeId))
    } catch (err) {
      console.warn('Sayari expand failed:', err)
      setExpandMessage(`Expansion failed: ${(err as Error).message}`)
    } finally {
      setExpandingNode(null)
    }
  }, [])

  const handleRunRiskReport = useCallback(async (nodeId: string) => {
    const node = allNodesMap.current.get(nodeId)
    if (!node) return
    const entityName = fullName(node)
    const ticker = extractTickerFromNode(node)
    const entityType = node.group || 'company'
    setRiskReportEntityName(entityName)
    setRiskReport(null)
    setRiskReportLoading(true)
    try {
      const report = await fetchEntityRiskReport(entityName, entityType, ticker)
      setRiskReport(report)
    } catch (err) {
      console.warn('Risk report failed:', err)
    } finally {
      setRiskReportLoading(false)
    }
  }, [])

  // Persist a set of nodes AND the edges among them into the shared store, so
  // relationships survive — not just isolated entities (the whole point of the
  // knowledge graph). Idempotent: the backend upserts, so re-saving is safe.
  // Returns how many entities/relationships were written.
  const persistSubgraph = useCallback(async (nodeIds: Iterable<string>) => {
    const idSet = new Set<string>()
    for (const id of nodeIds) if (allNodesMap.current.has(id)) idSet.add(id)

    let entityCount = 0
    for (const id of idSet) {
      const node = allNodesMap.current.get(id)!
      // Parse country out of the tooltip ("Name\ntype · country"), if present.
      const country = node.title?.split('\n')[1]?.split('·')[1]?.trim() || undefined
      await saveKnowledgeEntity({
        entity_id: id,
        name: fullName(node),
        entity_type: node.group || 'company',
        country,
      })
      entityCount += 1
    }

    // Save every edge whose endpoints are both in the saved set (the store drops
    // dangling edges anyway). Dedupe on (from, to, relationship).
    const seen = new Set<string>()
    let edgeCount = 0
    for (const e of allEdgesRef.current) {
      if (e.from === e.to || !idSet.has(e.from) || !idSet.has(e.to)) continue
      const relationshipType = (e.label || '').trim().replace(/\s+/g, '_') || 'related'
      const key = `${e.from}|${e.to}|${relationshipType}`
      if (seen.has(key)) continue
      seen.add(key)
      await saveKnowledgeEdge({ source_id: e.from, target_id: e.to, relationship_type: relationshipType })
      edgeCount += 1
    }

    setSavedNodeIds((prev) => {
      const next = new Set(prev)
      idSet.forEach((id) => next.add(id))
      return next
    })
    return { entityCount, edgeCount }
  }, [])

  const summariseSave = useCallback(({ entityCount, edgeCount }: { entityCount: number; edgeCount: number }) => {
    const parts = [`${entityCount} ${entityCount === 1 ? 'entity' : 'entities'}`]
    if (edgeCount > 0) parts.push(`${edgeCount} ${edgeCount === 1 ? 'relationship' : 'relationships'}`)
    setSaveMessage(`Saved ${parts.join(' · ')} to the knowledge graph`)
  }, [])

  const handleSaveToGraph = useCallback(async (nodeId: string) => {
    const node = allNodesMap.current.get(nodeId)
    if (!node || savingNode) return
    setSavingNode(nodeId)
    setSaveMessage(null)
    try {
      // Save the entity together with its immediate connections (incident edges
      // + the neighbours on the other end), so its graph is preserved — not just
      // the lone node.
      const ids = new Set<string>([nodeId])
      for (const e of allEdgesRef.current) {
        if (e.from === nodeId) ids.add(e.to)
        else if (e.to === nodeId) ids.add(e.from)
      }
      summariseSave(await persistSubgraph(ids))
    } catch (err) {
      console.warn('Save to graph failed:', err)
      setSaveMessage('Save failed — see console for details')
    } finally {
      setSavingNode(null)
    }
  }, [savingNode, persistSubgraph, summariseSave])

  const handleSaveEntireGraph = useCallback(async () => {
    if (savingAll) return
    setSavingAll(true)
    setSaveMessage(null)
    try {
      summariseSave(await persistSubgraph(allNodesMap.current.keys()))
    } catch (err) {
      console.warn('Save entire graph failed:', err)
      setSaveMessage('Save failed — see console for details')
    } finally {
      setSavingAll(false)
    }
  }, [savingAll, persistSubgraph, summariseSave])

  const handleFindSimilar = useCallback(async (nodeId: string) => {
    const node = allNodesMap.current.get(nodeId)
    if (!node || similarLoading) return
    setSimilar(null)
    setSimilarLoading(true)
    try {
      // Rank by name so it works whether or not the node is saved yet; the
      // backend compares against the shared knowledge store.
      const res = await fetchSimilarEntities({ name: fullName(node), entity_type: node.group, top_k: 5 })
      setSimilar(res)
    } catch (err) {
      console.warn('Find similar failed:', err)
    } finally {
      setSimilarLoading(false)
    }
  }, [similarLoading])

  const handleDiscover = useCallback(async (nodeId: string) => {
    const node = allNodesMap.current.get(nodeId)
    if (!node || discoverLoading) return
    setDiscoverLoading(true)
    setDiscover(null)
    try {
      const res = await discoverActions({
        entity: fullName(node),
        entity_type: node.group,
        proposed_action: discoverAction.trim() || undefined,
      })
      setDiscover(res)
    } catch (err) {
      console.warn('Discover actions failed:', err)
    } finally {
      setDiscoverLoading(false)
    }
  }, [discoverLoading, discoverAction])

  const handleNodeDoubleClick = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId)
    handleRunRiskReport(nodeId)
  }, [handleRunRiskReport])

  const closeRiskReport = useCallback(() => {
    setRiskReport(null)
    setRiskReportLoading(false)
    setRiskReportEntityName(null)
  }, [])

  if (!graphData && !graphLoading) return null

  const hasNodes = baseNodes.length > 0

  let emptyText = ''
  if (graphLoading && !graphData) emptyText = 'Loading entity graph...'
  else if (graphData && !hasNodes) emptyText = 'No entity relationships found'

  const totalNodes = baseNodes.length + addedCounts.nodes
  const totalEdges = baseEdges.length + addedCounts.edges
  const summary = graphData?.meta?.summary ?? null

  const selectedNode = selectedNodeId ? allNodesMap.current.get(selectedNodeId) : null
  const selectedNodeName = selectedNode ? fullName(selectedNode) : null
  const alreadyExpanded = selectedNodeId ? expandedNodes.has(selectedNodeId) : false

  return (
    <div className="mt-8">
      <div className="flex items-center justify-between mb-3 pb-2 border-b border-outline-variant/10">
        <div className="text-sm text-outline uppercase tracking-wider">{heading}</div>
        <div className="flex items-center gap-2">
        {/* Save the whole displayed subgraph (nodes + relationships) at once.
            Hidden on the Knowledge Graph page itself (where onRemoveNode is set),
            since everything there is already saved. */}
        {!onRemoveNode && hasNodes && (
          <button
            onClick={() => void handleSaveEntireGraph()}
            disabled={savingAll || !!savingNode}
            className="flex items-center gap-1.5 bg-surface-container border border-outline-variant/20 text-on-surface-variant text-[11px] px-3 py-1.5 rounded-lg hover:bg-surface-bright transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title="Save every entity and relationship shown here to the shared knowledge graph"
          >
            {savingAll ? (
              <><span className="inline-block w-2.5 h-2.5 border-2 border-outline-variant border-t-primary rounded-full animate-spin" />Saving&hellip;</>
            ) : (
              <><span className="material-symbols-outlined text-sm">hub</span>Save Entire Graph</>
            )}
          </button>
        )}
        {/* View-mode switcher (#35 scaffold). Clustered is live; Focus/Map/Matrix
            are wired in later phases (#36/#38/#39). */}
        <div data-vn="graph-views" className="flex items-center gap-1 bg-surface-container-lowest border border-outline-variant/15 rounded-lg p-0.5">
          {(['clustered', 'focus', 'map', 'matrix'] as const).map((m) => (
            <button
              key={m}
              onClick={() => setViewMode(m)}
              title={
                m === 'clustered'
                  ? 'Clustered node-link'
                  : m === 'focus'
                    ? 'Ego view — centre on the selected entity, ring neighbours by hops'
                    : m === 'map'
                      ? 'Geographic — entities by country with relationship arcs'
                      : 'Adjacency matrix — cluster-ordered, reveals blocs & cross-bloc ties'
              }
              className={`text-[11px] capitalize px-2.5 py-1 rounded-md transition-colors ${
                viewMode === m
                  ? 'bg-primary-container text-on-primary-container font-medium'
                  : 'text-outline hover:text-on-surface-variant disabled:opacity-30 disabled:hover:text-outline disabled:cursor-not-allowed'
              }`}
            >
              {m}
            </button>
          ))}
        </div>
        </div>
      </div>
      <div className="flex flex-wrap gap-3 mb-3">
        {LEGEND.map((item) => (
          <span key={item.label} className="flex items-center gap-1.5 text-xs text-outline">
            <span className="w-3 h-3 rounded-full shrink-0" style={{ background: item.color }} />
            {item.label}
          </span>
        ))}
      </div>

      {/* Graph summary — at-a-glance digest from /api/entity-graph meta (#28) */}
      {summary && hasNodes && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg px-3.5 py-2.5 mb-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-on-surface-variant">
            <span className="font-medium">{totalNodes} entities · {totalEdges} relationships</span>
            {summary.sanctioned_count > 0 && (
              <span className="text-[#d23c3a] font-medium">{summary.sanctioned_count} sanctioned</span>
            )}
            {Object.entries(summary.by_type)
              .sort((a, b) => b[1] - a[1])
              .map(([type, count]) => (
                <span key={type} className="text-outline capitalize">
                  {count} {type.replace(/_/g, ' ')}
                </span>
              ))}
          </div>
          {summary.high_risk_entities.length > 0 && (
            <div className="mt-1.5 text-[11px] text-outline">
              <span className="text-[#d23c3a] font-medium">High-risk:</span>{' '}
              {summary.high_risk_entities.join(' · ')}
            </div>
          )}
        </div>
      )}

      {/* Selected node action bar */}
      {selectedNode && (
        <div className="flex items-center justify-between gap-3 bg-surface-container-low border border-outline-variant/10 rounded-lg px-3.5 py-2 mb-2 flex-wrap">
          <div className="flex items-center gap-2 text-sm text-on-surface-variant font-medium min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
            <span className="w-3 h-3 rounded-full shrink-0" style={{ background: selectedNode.color || 'rgba(255,255,255,0.7)' }} />
            <span>{selectedNodeName}</span>
            <span className="text-[10px] text-outline capitalize ml-1">({selectedNode.group})</span>
          </div>
          <div className="flex gap-2 shrink-0">
            <button
              className="bg-surface-container border border-outline-variant/20 text-on-surface-variant text-xs px-3.5 py-1.5 rounded-lg hover:bg-surface-bright transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center"
              disabled={savingNode === selectedNodeId || savedNodeIds.has(selectedNodeId!)}
              onClick={() => handleSaveToGraph(selectedNodeId!)}
              title="Save this entity and its connections to the shared knowledge graph"
            >
              {savingNode === selectedNodeId ? (
                <><span className="inline-block w-2.5 h-2.5 border-2 border-outline-variant border-t-primary rounded-full animate-spin mr-1" />Saving&hellip;</>
              ) : savedNodeIds.has(selectedNodeId!) ? (
                '✓ Saved'
              ) : (
                'Save to Graph'
              )}
            </button>
            <button
              className="bg-surface-container border border-outline-variant/20 text-on-surface-variant text-xs px-3.5 py-1.5 rounded-lg hover:bg-surface-bright transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center"
              disabled={!!expandingNode || alreadyExpanded}
              onClick={() => handleSayariExpand(selectedNodeId!)}
              title="Fetch related entities from Sayari Graph"
            >
              {expandingNode === selectedNodeId ? (
                <><span className="inline-block w-2.5 h-2.5 border-2 border-outline-variant border-t-primary rounded-full animate-spin mr-1" />Expanding&hellip;</>
              ) : alreadyExpanded ? (
                'Expanded'
              ) : (
                'Expand via Sayari'
              )}
            </button>
            <button
              className="bg-secondary-container text-on-secondary-container text-xs px-3.5 py-1.5 rounded-lg hover:brightness-110 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center"
              disabled={riskReportLoading}
              onClick={() => handleRunRiskReport(selectedNodeId!)}
              title="Generate a full risk report for this entity"
            >
              {riskReportLoading && riskReportEntityName === selectedNodeName ? (
                <><span className="inline-block w-2.5 h-2.5 border-2 border-outline-variant border-t-on-secondary-container rounded-full animate-spin mr-1" />Analyzing&hellip;</>
              ) : (
                'Run Risk Report'
              )}
            </button>
            <button
              className="bg-surface-container border border-outline-variant/20 text-on-surface-variant text-xs px-3.5 py-1.5 rounded-lg hover:bg-surface-bright transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center"
              disabled={similarLoading}
              onClick={() => handleFindSimilar(selectedNodeId!)}
              title="Find saved entities with similar characteristics"
            >
              {similarLoading ? (
                <><span className="inline-block w-2.5 h-2.5 border-2 border-outline-variant border-t-primary rounded-full animate-spin mr-1" />Matching&hellip;</>
              ) : (
                'Find Similar'
              )}
            </button>
            <button
              className="bg-surface-container border border-outline-variant/20 text-on-surface-variant text-xs px-3.5 py-1.5 rounded-lg hover:bg-surface-bright transition-colors flex items-center"
              onClick={() => setDiscoverOpen((v) => !v)}
              title="Generate non-conflicting actions for this entity"
            >
              Discover Actions
            </button>
            {onRemoveNode && (
              <button
                className="bg-surface-container border border-error/30 text-error text-xs px-3.5 py-1.5 rounded-lg hover:bg-error-container/20 transition-colors flex items-center"
                onClick={() => onRemoveNode(selectedNodeId!, selectedNodeName || selectedNodeId!)}
                title="Remove this entity from the shared knowledge graph"
              >
                Remove
              </button>
            )}
          </div>
        </div>
      )}

      {/* Save confirmation — shows that connections (not just the node) were persisted. */}
      {saveMessage && (
        <div className="flex items-center justify-between gap-2 bg-surface-container-low border border-outline-variant/10 rounded-lg px-3.5 py-2 mb-2 text-xs text-on-surface-variant">
          <span className="flex items-center gap-1.5">
            <span className="material-symbols-outlined text-sm text-primary">check_circle</span>
            {saveMessage}
          </span>
          <button className="text-[11px] text-outline hover:text-on-surface-variant" onClick={() => setSaveMessage(null)}>
            Dismiss
          </button>
        </div>
      )}

      {/* Similar-entities results (#30) */}
      {similar && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg px-3.5 py-2.5 mb-2">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs font-medium text-on-surface-variant">
              Similar to {similar.target.name}
              <span className="text-[10px] text-outline ml-1.5">({similar.backend_used})</span>
            </span>
            <button className="text-[11px] text-outline hover:text-on-surface-variant" onClick={() => setSimilar(null)}>
              Dismiss
            </button>
          </div>
          {similar.results.length === 0 ? (
            <div className="text-[11px] text-outline italic">
              No comparable entities saved yet — use “Save to Graph” to build the knowledge store.
            </div>
          ) : (
            <ul className="flex flex-col gap-1">
              {similar.results.map((r) => (
                <li key={r.entity.entity_id} className="flex items-center gap-2 text-xs">
                  <span className="text-on-surface-variant font-medium min-w-0 truncate">{r.entity.name}</span>
                  <span className="text-[10px] text-outline capitalize">{r.entity.entity_type}</span>
                  <span className="ml-auto text-[10px] text-primary font-mono">{(r.score * 100).toFixed(0)}%</span>
                  <span className="flex gap-1 shrink-0">
                    {r.basis.same_type && <span className="text-[9px] text-outline bg-surface-container px-1 rounded">type</span>}
                    {r.basis.same_country && <span className="text-[9px] text-outline bg-surface-container px-1 rounded">country</span>}
                    {r.basis.shared_terms.slice(0, 2).map((t) => (
                      <span key={t} className="text-[9px] text-outline bg-surface-container px-1 rounded">{t}</span>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {similar.note && <div className="mt-1.5 text-[10px] text-outline italic">{similar.note}</div>}
        </div>
      )}

      {/* Target generation / discovery (#31) */}
      {discoverOpen && selectedNode && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg px-3.5 py-2.5 mb-2">
          <div className="text-xs font-medium text-on-surface-variant mb-1.5">
            Discover non-conflicting actions for {selectedNodeName}
          </div>
          <div className="flex gap-2 mb-1">
            <input
              value={discoverAction}
              onChange={(e) => setDiscoverAction(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleDiscover(selectedNodeId!) }}
              placeholder="Proposed action (optional), e.g. Add to BIS Entity List"
              className="flex-1 bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-1.5 text-xs text-on-surface placeholder:text-outline focus:outline-none"
            />
            <button
              className="bg-secondary-container text-on-secondary-container text-xs px-3.5 py-1.5 rounded-lg hover:brightness-110 disabled:opacity-50 flex items-center"
              disabled={discoverLoading}
              onClick={() => handleDiscover(selectedNodeId!)}
            >
              {discoverLoading ? (
                <><span className="inline-block w-2.5 h-2.5 border-2 border-outline-variant border-t-on-secondary-container rounded-full animate-spin mr-1" />Generating&hellip;</>
              ) : (
                'Discover'
              )}
            </button>
          </div>
          {discover && (
            <div className="mt-1.5">
              {discover.context.neighbors.length > 0 && (
                <div className="text-[10px] text-outline mb-1.5">
                  Grounded in graph neighbors: {discover.context.neighbors.map((n) => n.name).join(', ')}
                </div>
              )}
              {discover.suggested_actions.length > 0 ? (
                <ol className="list-decimal list-inside flex flex-col gap-1.5 text-xs text-on-surface-variant">
                  {discover.suggested_actions.map((a, i) => (
                    <li key={i} className="leading-snug">{a}</li>
                  ))}
                </ol>
              ) : (
                <div className="text-[11px] text-outline italic">{discover.note || 'No suggestions returned.'}</div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="bg-surface-container-lowest border border-outline-variant/10 rounded-lg h-[560px] relative mb-3">
        {emptyText && (
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-outline text-sm text-center">
            {emptyText}
          </div>
        )}
        {expandingNode && (
          <div className="absolute top-2 right-3 z-10 text-[11px] text-[#ff5a58] bg-surface-container-lowest/85 px-2.5 py-1 rounded border border-[#ff5a5840]">
            Expanding via Sayari...
          </div>
        )}
        {expandMessage && !expandingNode && (
          <div className="absolute top-2 right-3 z-10 text-[11px] text-outline bg-surface-container-lowest/85 px-2.5 py-1 rounded border border-outline-variant/20">
            {expandMessage}
          </div>
        )}
        {hasNodes && viewMode === 'map' && (
          <GraphMapView nodes={baseNodes} edges={baseEdges} onNodeClick={handleNodeClick} />
        )}
        {hasNodes && viewMode === 'matrix' && (
          <GraphMatrixView nodes={baseNodes} edges={baseEdges} onNodeClick={handleNodeClick} />
        )}
        {hasNodes && (viewMode === 'clustered' || viewMode === 'focus') && (
          <GraphViewer
            ref={graphRef}
            nodes={baseNodes}
            edges={baseEdges}
            mode={viewMode === 'focus' ? 'focus' : 'clustered'}
            focusId={selectedNodeId}
            onNodeClick={handleNodeClick}
            onNodeDoubleClick={handleNodeDoubleClick}
          />
        )}
      </div>
      {hasNodes && (
        <div className="text-[11px] text-outline text-center py-1">
          {totalNodes} entities &middot; {totalEdges} relationships
          {expandedNodes.size > 0 && (
            <span className="text-[#ff5a58]"> &middot; {expandedNodes.size} expanded via Sayari</span>
          )}
          <span className="text-outline italic"> — click any node to enable Sayari expand &amp; risk report &middot; double-click runs risk report</span>
        </div>
      )}

      <UBOPanel targetName={uboTargetName} owners={uboOwners} loading={uboLoading} />

      {(riskReportLoading || riskReport) && (
        <RiskReportPanel report={riskReport} loading={riskReportLoading} entityName={riskReportEntityName} onClose={closeRiskReport} />
      )}
    </div>
  )
}
