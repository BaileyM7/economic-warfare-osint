import { useEffect, useRef } from 'react'
import { Network } from 'vis-network/standalone'
import type { GraphData } from '../types'

interface Props {
  graphData: GraphData
}

export default function GraphViewer({ graphData }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current || !graphData.nodes.length) return

    const network = new Network(
      containerRef.current,
      {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        nodes: graphData.nodes as any,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        edges: graphData.edges as any,
      },
      {
        nodes: { shape: 'dot', size: 16, font: { color: '#c9d1d9', size: 12 } },
        edges: {
          color: { color: '#30363d', highlight: '#58a6ff' },
          font: { color: '#8b949e', size: 10 },
        },
        physics: {
          solver: 'forceAtlas2Based',
          forceAtlas2Based: { gravitationalConstant: -30 },
        },
        interaction: { hover: true, tooltipDelay: 100 },
        layout: { improvedLayout: true },
      }
    )

    return () => network.destroy()
  }, [graphData])

  return <div id="graph-container" ref={containerRef} />
}
