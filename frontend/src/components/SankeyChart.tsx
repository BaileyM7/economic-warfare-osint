import { useMemo } from 'react'
import { Chart as ChartJS } from 'chart.js/auto'
import { SankeyController, Flow } from 'chartjs-chart-sankey'
import { Chart } from 'react-chartjs-2'

ChartJS.register(SankeyController, Flow)

interface SankeyFlow {
  from: string
  to: string
  flow: number
}

interface Props {
  flows: SankeyFlow[]
}

// Colors: origins (blue) → commodities (orange) → destinations (light blue)
const COLOR_ORIGIN = '#4386c3'
const COLOR_COMMODITY = '#efb16a'
const COLOR_DEST = '#a9d8fb'

export default function SankeyChart({ flows }: Props) {
  // Classify nodes: origins only appear as "from" in first hop,
  // destinations only appear as "to" in second hop,
  // commodities appear as both "to" (first hop) and "from" (second hop)
  const nodeColors = useMemo(() => {
    const fromSet = new Set(flows.map(f => f.from))
    const toSet = new Set(flows.map(f => f.to))

    const colors: Record<string, string> = {}
    for (const label of new Set([...fromSet, ...toSet])) {
      const isFrom = fromSet.has(label)
      const isTo = toSet.has(label)
      if (isFrom && isTo) {
        // Appears on both sides — this is a commodity (middle column)
        colors[label] = COLOR_COMMODITY
      } else if (isFrom) {
        // Only appears as source — origin country (left column)
        colors[label] = COLOR_ORIGIN
      } else {
        // Only appears as target — destination country (right column)
        colors[label] = COLOR_DEST
      }
    }
    return colors
  }, [flows])

  if (!flows || flows.length < 6) return null

  return (
    <div style={{ height: '300px', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, background: '#061033' }}>
      <Chart
        type="sankey"
        data={{
          datasets: [
            {
              data: flows.map(f => ({ from: f.from, to: f.to, flow: f.flow })),
              colorFrom: (c: any) => nodeColors[c.dataset.data[c.dataIndex]?.from] || COLOR_ORIGIN,
              colorTo: (c: any) => nodeColors[c.dataset.data[c.dataIndex]?.to] || COLOR_DEST,
              colorMode: 'gradient' as const,
              size: 'max' as const,
            },
          ],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
          },
        }}
      />
    </div>
  )
}
