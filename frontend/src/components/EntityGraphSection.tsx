import { useCallback, useRef, useState } from 'react'
import GraphViewer, { type GraphViewerHandle } from './GraphViewer'
import UBOPanel from './UBOPanel'
import RiskReportPanel from './RiskReportPanel'
import { fetchSayariResolve, fetchSayariRelated, fetchEntityRiskReport, fetchSanctionsScreenBatch } from '../api'
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
}

export default function EntityGraphSection({
  graphData,
  graphLoading,
  uboOwners = [],
  uboLoading = false,
  uboTargetName = '',
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

  const knownIdsRef = useRef<Set<string>>(new Set())
  const allNodesMap = useRef<Map<string, GraphNode>>(new Map())

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
  } else if (knownIdsRef.current.size === 0 && baseNodes.length > 0) {
    knownIdsRef.current = new Set(baseNodes.map((n) => n.id))
    for (const n of baseNodes) allNodesMap.current.set(n.id, n)
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
          const node: GraphNode = { id: nid, label: truncate(ent.label), title: tooltip, group: ent.type || 'sayari', color: baseColor, sayariId: ent.entity_id }
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

  const selectedNode = selectedNodeId ? allNodesMap.current.get(selectedNodeId) : null
  const selectedNodeName = selectedNode ? fullName(selectedNode) : null
  const alreadyExpanded = selectedNodeId ? expandedNodes.has(selectedNodeId) : false

  return (
    <div className="mt-8">
      <div className="text-sm text-outline uppercase tracking-wider mb-3 pb-2 border-b border-outline-variant/10">
        Entity Relationship Graph
      </div>
      <div className="flex flex-wrap gap-3 mb-3">
        {LEGEND.map((item) => (
          <span key={item.label} className="flex items-center gap-1.5 text-xs text-outline">
            <span className="w-3 h-3 rounded-full shrink-0" style={{ background: item.color }} />
            {item.label}
          </span>
        ))}
      </div>

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
          </div>
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
        {hasNodes && (
          <GraphViewer
            ref={graphRef}
            nodes={baseNodes}
            edges={baseEdges}
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
