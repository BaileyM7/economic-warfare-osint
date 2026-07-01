import { useMemo, type ReactElement } from 'react'
import type { GraphNode, GraphEdge } from '../types'

// Phase 6 (#39): adjacency-matrix view. Every entity is a row and a column;
// coloured cells = relationships (colour = type). Rows/cols are ordered by
// community (entity type), so relationship blocs light up as diagonal squares and
// cross-bloc ties show off-diagonal — "beyond the hairball" for dense graphs.

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  onNodeClick?: (nodeId: string) => void
}

const GROUP_COLOR: Record<string, string> = {
  company: '#58a6ff',
  person: '#a371f7',
  government: '#DC143C',
  sanctions_list: '#F85149',
  vessel: '#3fb950',
  sector: '#F0883E',
  theme: '#F0883E',
}
function groupColor(g: string): string {
  return GROUP_COLOR[g] || '#8899bb'
}

// relationship label (already de-underscored) → cell colour
function relColor(label: string): string {
  const l = label.toLowerCase()
  if (l.includes('sanction')) return '#F85149'
  if (l.includes('subsidiary') || l.includes('parent')) return '#4386c3'
  if (l.includes('suppl')) return '#7aa2c9'
  if (l.includes('linked')) return '#a371f7'
  if (l.includes('trade') || l.includes('partner')) return '#3fb950'
  if (l.includes('owner') || l.includes('holds') || l.includes('shareholder')) return '#58a6ff'
  if (l.includes('officer')) return '#a371f7'
  return '#5b83b8'
}

const GROUP_ORDER = ['company', 'government', 'sanctions_list', 'vessel', 'sector', 'person', 'theme']

export default function GraphMatrixView({ nodes, edges, onNodeClick }: Props) {
  const { ordered, cell, mL, mT, W, H, matrix } = useMemo(() => {
    const nodeIds = new Set(nodes.map((n) => n.id))
    const degree: Record<string, number> = {}
    nodes.forEach((n) => (degree[n.id] = 0))
    const ve = edges.filter((e) => nodeIds.has(e.from) && nodeIds.has(e.to) && e.from !== e.to)
    ve.forEach((e) => {
      degree[e.from] = (degree[e.from] || 0) + 1
      degree[e.to] = (degree[e.to] || 0) + 1
    })
    const ordered = [...nodes].sort((a, b) => {
      const ga = GROUP_ORDER.indexOf(a.group)
      const gb = GROUP_ORDER.indexOf(b.group)
      const oa = ga === -1 ? 99 : ga
      const ob = gb === -1 ? 99 : gb
      return oa - ob || (degree[b.id] || 0) - (degree[a.id] || 0) || a.label.localeCompare(b.label)
    })
    const idx: Record<string, number> = {}
    ordered.forEach((n, i) => (idx[n.id] = i))
    const N = ordered.length
    const matrix: (string | null)[][] = Array.from({ length: N }, () => Array(N).fill(null))
    ve.forEach((e) => {
      const i = idx[e.from]
      const j = idx[e.to]
      matrix[i][j] = e.label
      matrix[j][i] = e.label
    })
    const cell = 30
    const mL = 200
    const mT = 150
    const W = mL + N * cell + 20
    const H = mT + N * cell + 20
    return { ordered, cell, mL, mT, W, H, matrix }
  }, [nodes, edges])

  const N = ordered.length

  return (
    <div className="w-full h-full overflow-auto p-2">
      <svg width={W} height={H} style={{ minWidth: '100%' }}>
        <g transform={`translate(${mL},${mT})`}>
          {/* community diagonal bands */}
          {(() => {
            const bands: ReactElement[] = []
            let start = 0
            while (start < N) {
              const g = ordered[start].group
              let end = start
              while (end < N && ordered[end].group === g) end++
              bands.push(
                <rect
                  key={`band_${start}`}
                  x={start * cell}
                  y={start * cell}
                  width={(end - start) * cell}
                  height={(end - start) * cell}
                  fill={groupColor(g)}
                  opacity={0.1}
                />,
              )
              start = end
            }
            return bands
          })()}

          {/* grid lines */}
          {Array.from({ length: N + 1 }).map((_, i) => (
            <g key={`grid_${i}`}>
              <line x1={0} x2={N * cell} y1={i * cell} y2={i * cell} stroke="#16264d" />
              <line x1={i * cell} x2={i * cell} y1={0} y2={N * cell} stroke="#16264d" />
            </g>
          ))}

          {/* cells */}
          {ordered.map((_, i) =>
            ordered.map((_, j) => {
              if (i === j) {
                return (
                  <rect
                    key={`d_${i}`}
                    x={j * cell + 6}
                    y={i * cell + 6}
                    width={cell - 12}
                    height={cell - 12}
                    rx={3}
                    fill={groupColor(ordered[i].group)}
                    opacity={0.4}
                  />
                )
              }
              const rel = matrix[i][j]
              if (!rel) return null
              return (
                <rect
                  key={`c_${i}_${j}`}
                  x={j * cell + 4}
                  y={i * cell + 4}
                  width={cell - 8}
                  height={cell - 8}
                  rx={3}
                  fill={relColor(rel)}
                  opacity={0.9}
                >
                  <title>{`${ordered[i].label} — ${rel} — ${ordered[j].label}`}</title>
                </rect>
              )
            }),
          )}

          {/* row labels */}
          {ordered.map((n, i) => (
            <text
              key={`r_${n.id}`}
              x={-10}
              y={i * cell + cell / 2 + 4}
              textAnchor="end"
              fontSize={11}
              fontWeight={600}
              fill={groupColor(n.group)}
              style={{ cursor: onNodeClick ? 'pointer' : 'default' }}
              onClick={() => onNodeClick?.(n.id)}
            >
              {n.label.length > 26 ? n.label.slice(0, 25) + '…' : n.label}
            </text>
          ))}

          {/* column labels (rotated) */}
          {ordered.map((n, j) => (
            <text
              key={`col_${n.id}`}
              transform={`translate(${j * cell + cell / 2 + 4},-10) rotate(-45)`}
              fontSize={11}
              fontWeight={600}
              fill={groupColor(n.group)}
            >
              {n.label.length > 22 ? n.label.slice(0, 21) + '…' : n.label}
            </text>
          ))}
        </g>
      </svg>
    </div>
  )
}
