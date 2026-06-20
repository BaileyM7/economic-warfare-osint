import { useEffect, useMemo, useState } from 'react'
import { fetchPersonNetwork } from '../api'
import type {
  GraphEdge,
  GraphNode,
  PersonNetworkNode,
  PersonNetworkEdge,
  PersonNetworkResponse,
} from '../types'
import GraphViewer from './GraphViewer'

interface Props {
  name: string
}

const DEPTH_OPTIONS: Array<{ value: 1 | 2; label: string }> = [
  { value: 1, label: 'Depth 1' },
  { value: 2, label: 'Depth 2' },
]

function colorFor(node: PersonNetworkNode): string {
  if (node.depth === 0) return '#d23c3a'                       // central — brand red
  if (node.sanctioned) return '#ff5a58'                        // sanctioned — bright red
  if (node.group === 'company') return '#4386c3'               // company — brand blue
  return node.depth === 1 ? '#efb16a' : '#a9d8fb'              // L1 orange / L2 light blue
}

function adaptNodes(nodes: PersonNetworkNode[]): GraphNode[] {
  return nodes.map((n) => ({
    id: n.id,
    label: n.label,
    title: `${n.group} · depth ${n.depth}${n.sanctioned ? ' · SANCTIONED' : ''}`,
    group: n.group,
    color: colorFor(n),
  }))
}

function adaptEdges(edges: PersonNetworkEdge[]): GraphEdge[] {
  return edges.map((e) => ({
    from: e.from,
    to: e.to,
    label: e.label ?? '',
    arrows: 'to',
    dashes: false,
  }))
}

export default function PersonNetworkPreview({ name }: Props) {
  const [expanded, setExpanded] = useState(false)
  const [depth, setDepth] = useState<1 | 2>(1)
  const [data, setData] = useState<PersonNetworkResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Only fetch once the user expands (keeps the autocomplete fast) and when
  // depth or name changes.
  useEffect(() => {
    if (!expanded || !name.trim()) return
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchPersonNetwork(name, depth, 15)
      .then((res) => {
        if (cancelled) return
        setData(res)
      })
      .catch((e) => {
        if (cancelled) return
        setError((e as Error).message || 'Network load failed')
        setData(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [expanded, name, depth])

  const { nodes, edges, stats } = useMemo(() => {
    if (!data) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[], stats: null as null | { persons: number; companies: number; sanctioned: number } }
    const persons = data.nodes.filter((n) => n.group === 'person').length
    const companies = data.nodes.filter((n) => n.group === 'company').length
    const sanctioned = data.nodes.filter((n) => n.sanctioned && n.depth > 0).length
    return {
      nodes: adaptNodes(data.nodes),
      edges: adaptEdges(data.edges),
      stats: { persons, companies, sanctioned },
    }
  }, [data])

  return (
    <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg overflow-hidden">
      {/* Collapsed header / toggle */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 hover:bg-surface-container-high/40 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="material-symbols-outlined text-outline text-base">hub</span>
          <span className="text-sm font-semibold text-on-surface truncate">
            Co-officer network for {name}
          </span>
          {stats && !loading && (
            <span className="text-[11px] text-outline ml-2 whitespace-nowrap">
              {stats.persons} people · {stats.companies} companies
              {stats.sanctioned > 0 && (
                <span className="text-error"> · {stats.sanctioned} sanctioned</span>
              )}
            </span>
          )}
        </div>
        <span className="material-symbols-outlined text-outline">
          {expanded ? 'expand_less' : 'expand_more'}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-outline-variant/10">
          {/* Depth toggle */}
          <div className="px-4 py-2 flex items-center gap-2 border-b border-outline-variant/10">
            <span className="text-[11px] text-outline uppercase tracking-wider mr-1">Depth</span>
            {DEPTH_OPTIONS.map((opt) => {
              const active = depth === opt.value
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setDepth(opt.value)}
                  className={`px-2.5 py-1 rounded text-[11px] font-semibold uppercase tracking-wider border transition-colors ${
                    active
                      ? 'bg-primary-container text-on-primary-container border-primary-container'
                      : 'bg-surface-container border-outline-variant/30 text-on-surface-variant hover:bg-surface-container-high'
                  }`}
                >
                  {opt.label}
                </button>
              )
            })}
            {loading && (
              <span className="ml-auto text-[11px] text-outline flex items-center gap-1.5">
                <span className="material-symbols-outlined text-xs animate-spin">progress_activity</span>
                Loading network…
              </span>
            )}
          </div>

          {/* Graph canvas */}
          <div className="relative" style={{ height: 360 }}>
            {error ? (
              <div className="absolute inset-0 flex items-center justify-center text-xs text-error px-4 text-center">
                {error}
              </div>
            ) : !loading && nodes.length === 0 && data ? (
              <div className="absolute inset-0 flex items-center justify-center text-xs text-outline italic px-4 text-center">
                No co-officer relationships found.
              </div>
            ) : (
              <GraphViewer nodes={nodes} edges={edges} />
            )}
          </div>
        </div>
      )}
    </div>
  )
}
