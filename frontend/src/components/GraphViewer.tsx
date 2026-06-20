import { useEffect, useRef, useImperativeHandle, forwardRef } from 'react'
import { Network, DataSet } from 'vis-network/standalone'
import type { GraphNode, GraphEdge } from '../types'

export interface GraphViewerHandle {
  addData: (nodes: GraphNode[], edges: GraphEdge[], anchorId?: string) => void
  updateNodes: (updates: Array<{ id: string; color?: string; title?: string }>) => void
}

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  onStabilized?: () => void
  onNodeClick?: (nodeId: string) => void
  onNodeDoubleClick?: (nodeId: string) => void
}

/**
 * Collect all descendant node IDs reachable via outgoing edges from `rootId`.
 * Uses BFS over the edge DataSet.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function collectDescendants(rootId: string, edgesDS: DataSet<any>): Set<string> {
  const children = new Set<string>()
  const queue = [rootId]
  const visited = new Set<string>([rootId])

  // Build adjacency from edges (from → to)
  const adj = new Map<string, string[]>()
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  edgesDS.forEach((edge: any) => {
    const from = edge.from as string
    const to = edge.to as string
    if (!adj.has(from)) adj.set(from, [])
    adj.get(from)!.push(to)
  })

  while (queue.length > 0) {
    const current = queue.shift()!
    const neighbors = adj.get(current) ?? []
    for (const n of neighbors) {
      if (!visited.has(n)) {
        visited.add(n)
        children.add(n)
        queue.push(n)
      }
    }
  }
  return children
}

const GraphViewer = forwardRef<GraphViewerHandle, Props>(
  function GraphViewer({ nodes, edges, onStabilized, onNodeClick, onNodeDoubleClick }, ref) {
    const containerRef = useRef<HTMLDivElement>(null)
    const networkRef = useRef<Network | null>(null)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const nodesDS = useRef<DataSet<any> | null>(null)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const edgesDS = useRef<DataSet<any> | null>(null)
    const onNodeClickRef = useRef(onNodeClick)
    const onStabilizedRef = useRef(onStabilized)
    const onNodeDoubleClickRef = useRef(onNodeDoubleClick)

    // Subtree drag state
    const dragChildrenRef = useRef<Map<string, { dx: number; dy: number }>>(new Map())
    const dragStartPosRef = useRef<{ x: number; y: number } | null>(null)

    onNodeClickRef.current = onNodeClick
    onStabilizedRef.current = onStabilized
    onNodeDoubleClickRef.current = onNodeDoubleClick

    useImperativeHandle(ref, () => ({
      addData(newNodes: GraphNode[], newEdges: GraphEdge[], anchorId?: string) {
        if (!nodesDS.current || !edgesDS.current || !networkRef.current) return

        const existingIds = new Set(nodesDS.current.getIds() as string[])
        const toAddNodes = newNodes.filter((n) => !existingIds.has(n.id))
        const toAddEdges = newEdges.map((e, i) => ({
          ...e,
          id: `sayari_edge_${Date.now()}_${i}`,
        }))

        if (toAddNodes.length === 0) return

        let anchorX = 0
        let anchorY = 0
        if (anchorId) {
          try {
            const pos = networkRef.current.getPositions([anchorId])
            if (pos[anchorId]) {
              anchorX = pos[anchorId].x
              anchorY = pos[anchorId].y
            }
          } catch { /* node may not exist */ }
        }

        const positioned = toAddNodes.map((n, i) => {
          const angle = (2 * Math.PI * i) / toAddNodes.length
          const radius = 150 + Math.random() * 50
          return {
            ...n,
            x: anchorX + Math.cos(angle) * radius,
            y: anchorY + Math.sin(angle) * radius,
          }
        })

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        nodesDS.current.add(positioned as any)
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        edgesDS.current.add(toAddEdges as any)

        networkRef.current.setOptions({ physics: { enabled: true } })
        setTimeout(() => {
          networkRef.current?.setOptions({ physics: { enabled: false } })
        }, 1500)
      },
      updateNodes(updates: Array<{ id: string; color?: string; title?: string }>) {
        if (!nodesDS.current) return
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        nodesDS.current.update(updates as any)
      },
    }))

    useEffect(() => {
      if (!containerRef.current || !nodes.length) return

      if (networkRef.current) {
        networkRef.current.destroy()
        networkRef.current = null
      }

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const nds = new DataSet(nodes as any)
      const eds = new DataSet(
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        edges.map((e, i) => ({ ...e, id: `edge_${i}` })) as any,
      )
      nodesDS.current = nds
      edgesDS.current = eds

      const network = new Network(
        containerRef.current,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { nodes: nds as any, edges: eds as any },
        {
          physics: {
            solver: 'repulsion',
            repulsion: {
              nodeDistance: 180,
              centralGravity: 0.15,
              springLength: 200,
              springConstant: 0.04,
              damping: 0.09,
            },
            stabilization: { iterations: 300 },
          },
          nodes: {
            shape: 'dot',
            size: 18,
            font: { color: '#ffffff', size: 12, strokeWidth: 3, strokeColor: '#061033' },
            borderWidth: 2,
            color: {
              border: '#1f3864',
              highlight: { border: '#4386c3' },
              hover: { border: '#4386c3' },
            },
          },
          edges: {
            font: {
              color: 'rgba(255,255,255,0.7)',
              size: 10,
              align: 'middle',
              strokeWidth: 2,
              strokeColor: '#061033',
            },
            color: { color: '#4386c3', highlight: '#ffffff', opacity: 0.6 },
            width: 2,
            smooth: { enabled: true, type: 'continuous', roundness: 0.5 },
            arrows: { to: { enabled: true, scaleFactor: 0.5 } },
          },
          interaction: { hover: true, tooltipDelay: 150, dragNodes: true },
          layout: { randomSeed: 42 },
        },
      )

      networkRef.current = network

      network.once('stabilized', () => {
        network.setOptions({ physics: { enabled: false } })
        network.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } })
        onStabilizedRef.current?.()
      })

      network.on('selectNode', (params) => {
        const nodeId = params.nodes?.[0]
        if (nodeId) {
          onNodeClickRef.current?.(nodeId)
        }
      })

      network.on('doubleClick', (params) => {
        const nodeId = params.nodes?.[0]
        if (nodeId) {
          onNodeDoubleClickRef.current?.(nodeId)
        }
      })

      // --- Subtree drag: move children along with the dragged node ---

      network.on('dragStart', (params) => {
        const draggedIds: string[] = params.nodes ?? []
        if (draggedIds.length !== 1) return
        const rootId = draggedIds[0]

        const rootPos = network.getPositions([rootId])[rootId]
        if (!rootPos) return
        dragStartPosRef.current = { x: rootPos.x, y: rootPos.y }

        const descendants = collectDescendants(rootId, eds)
        const offsets = new Map<string, { dx: number; dy: number }>()
        if (descendants.size > 0) {
          const allIds = Array.from(descendants)
          const positions = network.getPositions(allIds)
          for (const cid of allIds) {
            const cp = positions[cid]
            if (cp) {
              offsets.set(cid, { dx: cp.x - rootPos.x, dy: cp.y - rootPos.y })
            }
          }
        }
        dragChildrenRef.current = offsets
      })

      network.on('dragging', (params) => {
        const draggedIds: string[] = params.nodes ?? []
        if (draggedIds.length !== 1 || dragChildrenRef.current.size === 0) return

        const rootId = draggedIds[0]
        const rootPos = network.getPositions([rootId])[rootId]
        if (!rootPos) return

        for (const [cid, offset] of dragChildrenRef.current) {
          network.moveNode(cid, rootPos.x + offset.dx, rootPos.y + offset.dy)
        }
      })

      network.on('dragEnd', () => {
        dragChildrenRef.current = new Map()
        dragStartPosRef.current = null
      })

      return () => {
        networkRef.current = null
        nodesDS.current = null
        edgesDS.current = null
        network.destroy()
      }
    }, [nodes, edges])

    return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
  },
)

export default GraphViewer
