import { useEffect, useRef, useImperativeHandle, forwardRef } from 'react'
import cytoscape from 'cytoscape'
import type { Core, ElementDefinition, NodeSingular } from 'cytoscape'
import type { GraphNode, GraphEdge } from '../types'

// Phase 2 (#35): the entity graph is rendered with Cytoscape.js. Two view modes:
//  - 'clustered' (#35): entities grouped into labelled community boxes (by type),
//    cose layout — tames the "hairball".
//  - 'focus' (#36): ego/radial — the focus entity is centred and every other node
//    sits on a ring by hop distance, with far context faded (concentric layout).
// Public handle/props are unchanged aside from the additive mode/focusId props.

export interface GraphViewerHandle {
  addData: (nodes: GraphNode[], edges: GraphEdge[], anchorId?: string) => void
  updateNodes: (updates: Array<{ id: string; color?: string; title?: string }>) => void
}

type ViewMode = 'clustered' | 'focus'

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  mode?: ViewMode
  focusId?: string | null
  onStabilized?: () => void
  onNodeClick?: (nodeId: string) => void
  onNodeDoubleClick?: (nodeId: string) => void
}

const GROUP_LABELS: Record<string, string> = {
  company: 'Companies',
  person: 'People',
  government: 'Government',
  sanctions_list: 'Sanctions Lists',
  vessel: 'Vessels',
  sector: 'Sectors',
  theme: 'Themes / Offshore',
}
function groupLabel(g: string): string {
  return GROUP_LABELS[g] || (g ? g.charAt(0).toUpperCase() + g.slice(1).replace(/_/g, ' ') : 'Other')
}
function groupId(g: string | undefined): string {
  return `grp_${g || 'other'}`
}

const SANCTIONED_COLORS = new Set(['#F85149', '#d23c3a', '#ff5a58'])
function isSanctioned(n: GraphNode): boolean {
  return n.riskLevel === 'HIGH' || (n.color ? SANCTIONED_COLORS.has(n.color) : false)
}

function validEdges(edges: GraphEdge[], nodeIds: Set<string>): GraphEdge[] {
  return edges.filter((e) => nodeIds.has(e.from) && nodeIds.has(e.to) && e.from !== e.to)
}
function edgeEl(e: GraphEdge, i: number): ElementDefinition {
  return {
    data: {
      id: `e_${i}_${e.from}_${e.to}`,
      source: e.from,
      target: e.to,
      label: e.label,
      width: e.width ?? 2,
      dashed: e.dashes ? 1 : 0,
    },
  }
}

/** Clustered: compound community parents + child nodes + edges. */
function clusteredElements(nodes: GraphNode[], edges: GraphEdge[]): ElementDefinition[] {
  const nodeIds = new Set(nodes.map((n) => n.id))
  const parents: ElementDefinition[] = [...new Set(nodes.map((n) => n.group || 'other'))].map((g) => ({
    data: { id: groupId(g), label: groupLabel(g), isGroup: 1 },
  }))
  const nodeEls: ElementDefinition[] = nodes.map((n) => ({
    data: {
      id: n.id,
      label: n.label,
      title: n.title,
      parent: groupId(n.group),
      color: n.color || '#8899bb',
      value: n.value ?? 1,
      sanctioned: isSanctioned(n) ? 1 : 0,
    },
  }))
  return [...parents, ...nodeEls, ...validEdges(edges, nodeIds).map(edgeEl)]
}

/** Focus/ego: flat nodes tagged with hop distance from `focusId`, + edges. */
function focusElements(
  nodes: GraphNode[],
  edges: GraphEdge[],
  focusId: string | null | undefined,
): ElementDefinition[] {
  const nodeIds = new Set(nodes.map((n) => n.id))
  const ve = validEdges(edges, nodeIds)
  // undirected adjacency for hop BFS
  const adj = new Map<string, string[]>()
  nodes.forEach((n) => adj.set(n.id, []))
  ve.forEach((e) => {
    adj.get(e.from)!.push(e.to)
    adj.get(e.to)!.push(e.from)
  })
  // pick a focus: requested, else most-connected, else first
  let focus = focusId && nodeIds.has(focusId) ? focusId : null
  if (!focus) {
    focus = [...nodes].sort((a, b) => (adj.get(b.id)!.length || 0) - (adj.get(a.id)!.length || 0))[0]?.id ?? null
  }
  const hop = new Map<string, number>()
  if (focus) {
    hop.set(focus, 0)
    const q = [focus]
    while (q.length) {
      const u = q.shift()!
      for (const v of adj.get(u) ?? []) {
        if (!hop.has(v)) {
          hop.set(v, (hop.get(u) ?? 0) + 1)
          q.push(v)
        }
      }
    }
  }
  const nodeEls: ElementDefinition[] = nodes.map((n) => {
    const h = hop.has(n.id) ? (hop.get(n.id) as number) : 99
    return {
      data: {
        id: n.id,
        label: n.label,
        title: n.title,
        color: n.color || '#8899bb',
        value: n.value ?? 1,
        sanctioned: isSanctioned(n) ? 1 : 0,
        hop: h,
        concentric: -h, // higher = inner ring; focus (hop 0) is centre
        isFocus: n.id === focus ? 1 : 0,
      },
    }
  })
  return [...nodeEls, ...ve.map(edgeEl)]
}

const BASE_NODE_STYLE: cytoscape.Css.Node = {
  'background-color': 'data(color)',
  width: 'mapData(value, 1, 12, 30, 74)',
  height: 'mapData(value, 1, 12, 30, 74)',
  label: 'data(label)',
  color: '#eaf1ff',
  'font-size': 13,
  'font-weight': 600,
  'text-valign': 'bottom',
  'text-margin-y': 4,
  'text-outline-color': '#061033',
  'text-outline-width': 2,
  'border-width': 2,
  'border-color': '#1f3864',
  'min-zoomed-font-size': 3,
}

const CLUSTERED_STYLE = [
  { selector: 'node', style: BASE_NODE_STYLE },
  { selector: 'node[sanctioned = 1]', style: { 'border-width': 4, 'border-color': '#ff3b3b' } },
  {
    selector: ':parent',
    style: {
      'background-color': '#4386c3',
      'background-opacity': 0.06,
      'border-color': '#2a3f66',
      'border-width': 1,
      shape: 'round-rectangle',
      label: 'data(label)',
      color: '#a9c8ee',
      'font-size': 15,
      'font-weight': 700,
      'text-valign': 'top',
      'text-halign': 'center',
      'text-margin-y': -4,
      'text-transform': 'uppercase',
      'text-outline-color': '#061033',
      'text-outline-width': 2,
      'min-zoomed-font-size': 3,
      padding: 22,
    },
  },
  { selector: 'node:selected', style: { 'border-width': 4, 'border-color': '#4386c3' } },
  {
    selector: 'edge',
    style: {
      width: 'data(width)',
      'line-color': '#4386c3',
      opacity: 0.55,
      'target-arrow-color': '#4386c3',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.7,
      'curve-style': 'bezier',
      label: 'data(label)',
      'font-size': 9,
      color: 'rgba(255,255,255,0.65)',
      'text-outline-color': '#061033',
      'text-outline-width': 2,
      'min-zoomed-font-size': 7,
    },
  },
  { selector: 'edge[dashed = 1]', style: { 'line-style': 'dashed', opacity: 0.4 } },
] as cytoscape.Stylesheet[]

const FOCUS_STYLE = [
  {
    selector: 'node',
    style: {
      ...BASE_NODE_STYLE,
      // fade nodes by hop distance from the focus (degree-of-interest)
      opacity: 'mapData(hop, 0, 3, 1, 0.4)',
    },
  },
  { selector: 'node[sanctioned = 1]', style: { 'border-width': 4, 'border-color': '#ff3b3b' } },
  {
    selector: 'node[isFocus = 1]',
    style: { 'border-width': 4, 'border-color': '#4386c3', opacity: 1 },
  },
  {
    selector: 'edge',
    style: {
      width: 'data(width)',
      'line-color': '#4386c3',
      opacity: 0.3,
      'target-arrow-color': '#4386c3',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.7,
      'curve-style': 'bezier',
      'min-zoomed-font-size': 8,
    },
  },
] as cytoscape.Stylesheet[]

function clusteredLayout(): cytoscape.LayoutOptions {
  return {
    name: 'cose',
    animate: false,
    fit: true,
    padding: 40,
    nodeRepulsion: () => 14000,
    idealEdgeLength: () => 110,
    nestingFactor: 1.2,
    gravity: 0.55,
    numIter: 1400,
    randomize: false,
    componentSpacing: 140,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any
}
function focusLayout(): cytoscape.LayoutOptions {
  return {
    name: 'concentric',
    animate: false,
    fit: true,
    padding: 50,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    concentric: (node: any) => node.data('concentric'),
    levelWidth: () => 1,
    minNodeSpacing: 55,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any
}

const GraphViewer = forwardRef<GraphViewerHandle, Props>(
  function GraphViewer(
    { nodes, edges, mode = 'clustered', focusId, onStabilized, onNodeClick, onNodeDoubleClick },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null)
    const cyRef = useRef<Core | null>(null)
    const modeRef = useRef<ViewMode>(mode)
    const onNodeClickRef = useRef(onNodeClick)
    const onStabilizedRef = useRef(onStabilized)
    const onNodeDoubleClickRef = useRef(onNodeDoubleClick)
    const lastTapRef = useRef<{ id: string; t: number }>({ id: '', t: 0 })

    modeRef.current = mode
    onNodeClickRef.current = onNodeClick
    onStabilizedRef.current = onStabilized
    onNodeDoubleClickRef.current = onNodeDoubleClick

    useImperativeHandle(ref, () => ({
      addData(newNodes: GraphNode[], newEdges: GraphEdge[], anchorId?: string) {
        const cy = cyRef.current
        if (!cy) return
        const existing = new Set(cy.nodes().map((n) => n.id()))
        const toAdd = newNodes.filter((n) => !existing.has(n.id))
        if (toAdd.length === 0) return
        const clustered = modeRef.current === 'clustered'

        if (clustered) {
          for (const g of new Set(toAdd.map((n) => n.group || 'other'))) {
            if (cy.getElementById(groupId(g)).empty()) {
              cy.add({ data: { id: groupId(g), label: groupLabel(g), isGroup: 1 } })
            }
          }
        }
        let ax = 0
        let ay = 0
        if (anchorId) {
          const a = cy.getElementById(anchorId)
          if (a.nonempty()) {
            const p = (a as NodeSingular).position()
            ax = p.x
            ay = p.y
          }
        }
        const allIds = new Set([...existing, ...toAdd.map((n) => n.id)])
        cy.add(
          toAdd.map((n, i) => {
            const angle = (2 * Math.PI * i) / toAdd.length
            const r = 140 + Math.random() * 60
            const data: Record<string, unknown> = {
              id: n.id,
              label: n.label,
              title: n.title,
              color: n.color || '#8899bb',
              value: n.value ?? 2,
              sanctioned: isSanctioned(n) ? 1 : 0,
            }
            if (clustered) data.parent = groupId(n.group)
            else Object.assign(data, { hop: 1, concentric: -1, isFocus: 0 })
            return { data, position: { x: ax + Math.cos(angle) * r, y: ay + Math.sin(angle) * r } }
          }),
        )
        cy.add(validEdges(newEdges, allIds).map((e, i) => ({
          data: {
            id: `sayari_e_${Date.now()}_${i}`,
            source: e.from,
            target: e.to,
            label: e.label,
            width: e.width ?? 2,
            dashed: e.dashes ? 1 : 0,
          },
        })))
      },
      updateNodes(updates) {
        const cy = cyRef.current
        if (!cy) return
        updates.forEach((u) => {
          const n = cy.getElementById(u.id)
          if (n.empty()) return
          if (u.color) {
            n.data('color', u.color)
            if (SANCTIONED_COLORS.has(u.color)) n.data('sanctioned', 1)
          }
          if (u.title) n.data('title', u.title)
        })
      },
    }))

    useEffect(() => {
      if (!containerRef.current || !nodes.length) return

      const focus = mode === 'focus'
      const cy = cytoscape({
        container: containerRef.current,
        elements: focus ? focusElements(nodes, edges, focusId) : clusteredElements(nodes, edges),
        style: focus ? FOCUS_STYLE : CLUSTERED_STYLE,
        wheelSensitivity: 0.25,
        minZoom: 0.2,
        maxZoom: 3,
      })
      cyRef.current = cy

      const layout = cy.layout(focus ? focusLayout() : clusteredLayout())
      layout.one('layoutstop', () => {
        cy.fit(cy.elements(), 36)
        onStabilizedRef.current?.()
      })
      layout.run()

      cy.on('tap', 'node', (evt) => {
        const n = evt.target as NodeSingular
        if (n.isParent()) return
        const id = n.id()
        const now = Date.now()
        if (lastTapRef.current.id === id && now - lastTapRef.current.t < 300) {
          onNodeDoubleClickRef.current?.(id)
          lastTapRef.current = { id: '', t: 0 }
        } else {
          onNodeClickRef.current?.(id)
          lastTapRef.current = { id, t: now }
        }
      })

      return () => {
        cy.destroy()
        cyRef.current = null
      }
    }, [nodes, edges, mode, focusId])

    return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
  },
)

export default GraphViewer
