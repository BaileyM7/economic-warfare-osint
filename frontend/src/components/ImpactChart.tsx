import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import {
  Chart,
  LinearScale,
  LineElement,
  PointElement,
  LineController,
  Legend,
  Tooltip,
  Filler,
} from 'chart.js'
import Annotation from 'chartjs-plugin-annotation'
import type { SanctionsImpactResponse } from '../types'

Chart.register(LinearScale, LineElement, PointElement, LineController, Legend, Tooltip, Filler, Annotation)

export interface ImpactChartHandle {
  toggleDataset: (idx: number) => void
}

interface Props {
  data: SanctionsImpactResponse
}

// ── Legend types ──────────────────────────────────────────────────────────────

type LegendItem = {
  label: string
  lineColor?: string    // → short line swatch
  fillColor?: string    // → filled rect swatch
  datasetIndex: number  // always toggleable
}

type LegendGroup = {
  title: string
  accentColor: string
  items: LegendItem[]
}

/**
 * Dataset push order (must stay in sync with useEffect below):
 *
 *  SANCTIONED GROUP
 *  [0]         sanction_mean        (white dashed bold)
 *  [1]         sanction_band_upper  (red fill, fill:'+1')
 *  [2]         _sanction_band_lower (transparent — hidden from legend)
 *  [3 .. 3+N-1]  sanctioned comparable curves  (N = comparables.length)
 *
 *  CONTROL GROUP
 *  [3+N]         ctrl_mean          (green dashed)
 *  [3+N+1]       ctrl_band_upper    (green fill, fill:'+1')
 *  [3+N+2]       _ctrl_band_lower   (transparent — hidden from legend)
 *  [3+N+3 .. 3+N+3+M-1]  control peer curves  (M = control_comparables.length)
 */
function buildLegendGroups(data: SanctionsImpactResponse): LegendGroup[] {
  const hasControl = !!(data.control_projection?.upper.length)
  const hasSanction = data.projection.upper.length > 0
  const N = data.comparables.length

  // ── Sanctioned group ──────────────────────────────────────────────────────
  const sanctionItems: LegendItem[] = []

  if (hasSanction) {
    sanctionItems.push({
      label: `${data.target.ticker} projected mean`,
      lineColor: '#ffffff',
      datasetIndex: 0,
    })
    sanctionItems.push({
      label: 'Projected confidence band (±1σ)',
      fillColor: 'rgba(210,60,58,0.45)',
      datasetIndex: 1,
    })
  }

  data.comparables.forEach((comp, i) => {
    sanctionItems.push({
      label: `${comp.name} (${comp.sanction_date.slice(0, 4)})`,
      lineColor: comp.color,
      datasetIndex: 3 + i,
    })
  })

  // ── Control group ─────────────────────────────────────────────────────────
  const controlItems: LegendItem[] = []

  if (hasControl) {
    controlItems.push({
      label: 'Control group mean',
      lineColor: 'rgba(169,216,251,0.85)',
      datasetIndex: 3 + N,
    })
    controlItems.push({
      label: 'Control group range (±1σ)',
      fillColor: 'rgba(169,216,251,0.35)',
      datasetIndex: 3 + N + 1,
    })
  }

  ;(data.control_comparables ?? []).forEach((comp, i) => {
    controlItems.push({
      label: comp.name,
      lineColor: 'rgba(169,216,251,0.7)',
      datasetIndex: 3 + N + 3 + i,
    })
  })

  return [
    { title: 'Sanctioned Comparables', accentColor: '#d23c3a', items: sanctionItems },
    { title: 'Control Group — Non-Sanctioned Peers', accentColor: '#a9d8fb', items: controlItems },
  ]
}

// ── Swatch ────────────────────────────────────────────────────────────────────

function Swatch({ item, hidden }: { item: LegendItem; hidden: boolean }) {
  const opacity = hidden ? 0.3 : 1
  if (item.fillColor) {
    return (
      <span style={{
        display: 'inline-block', width: 12, height: 12,
        borderRadius: 2, background: item.fillColor, flexShrink: 0, opacity,
      }} />
    )
  }
  return (
    <span style={{
      display: 'inline-block', width: 20, height: 2,
      background: item.lineColor, borderRadius: 1, flexShrink: 0, opacity,
    }} />
  )
}

// ── Custom legend ─────────────────────────────────────────────────────────────

function CustomLegend({
  groups,
  hidden,
  onToggle,
}: {
  groups: LegendGroup[]
  hidden: Set<number>
  onToggle: (idx: number) => void
}) {
  return (
    <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {groups.map((group) => (
        <div key={group.title} style={{
          background: 'rgba(11,36,81,0.7)',
          border: `1px solid ${group.accentColor}2a`,
          borderLeft: `3px solid ${group.accentColor}`,
          borderRadius: 6,
          padding: '8px 12px',
        }}>
          <div style={{
            fontSize: 10, fontWeight: 700, letterSpacing: '0.07em',
            textTransform: 'uppercase', color: group.accentColor, marginBottom: 8,
          }}>
            {group.title}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px 18px', alignItems: 'center' }}>
            {group.items.map((item) => {
              const isHidden = hidden.has(item.datasetIndex)
              return (
                <button
                  key={item.datasetIndex}
                  onClick={() => onToggle(item.datasetIndex)}
                  title="Click to show/hide"
                  style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                    fontSize: 11,
                    color: isHidden ? 'rgba(255,255,255,0.1)' : 'rgba(255,255,255,0.7)',
                    opacity: isHidden ? 0.5 : 1,
                    transition: 'opacity 0.15s, color 0.15s',
                  }}
                >
                  <Swatch item={item} hidden={isHidden} />
                  <span style={{ textDecoration: isHidden ? 'line-through' : 'none' }}>
                    {item.label}
                  </span>
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Main chart component ──────────────────────────────────────────────────────

const ImpactChart = forwardRef<ImpactChartHandle, Props>(({ data }, ref) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const chartRef = useRef<Chart | null>(null)
  const [hiddenDatasets, setHiddenDatasets] = useState<Set<number>>(new Set())

  const legendGroups = buildLegendGroups(data)

  function handleToggle(idx: number) {
    const chart = chartRef.current
    if (chart) {
      // Toggling the band upper (fill dataset) also hides the lower so the fill vanishes
      const meta = chart.getDatasetMeta(idx)
      meta.hidden = !meta.hidden
      // If this is a band upper (fill: '+1'), sync the paired lower
      const ds = chart.data.datasets[idx] as { fill?: unknown; label?: string }
      if (ds.fill === '+1') {
        const lowerMeta = chart.getDatasetMeta(idx + 1)
        lowerMeta.hidden = meta.hidden
      }
      chart.update()
    }
    setHiddenDatasets((prev) => {
      const next = new Set(prev)
      next.has(idx) ? next.delete(idx) : next.add(idx)
      return next
    })
  }

  useImperativeHandle(ref, () => ({ toggleDataset: handleToggle }))

  useEffect(() => {
    if (!canvasRef.current) return
    if (chartRef.current) { chartRef.current.destroy(); chartRef.current = null }
    setHiddenDatasets(new Set())

    const ctx = canvasRef.current.getContext('2d')
    if (!ctx) return

    const hasControl = !!(data.control_projection?.upper.length)
    const hasSanction = data.projection.upper.length > 0

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const datasets: any[] = []

    // ── [0] Sanctioned projection mean ───────────────────────────────────────
    datasets.push({
      label: hasSanction ? `${data.target.ticker} projected mean` : '_empty_mean',
      data: hasSanction ? data.projection.mean.map((p) => ({ x: p.day, y: p.pct })) : [],
      borderColor: '#ffffff',
      borderWidth: 4,
      borderDash: [10, 5],
      pointRadius: 0,
      pointHoverRadius: 6,
      tension: 0.2,
      fill: false,
      order: 0,
    })

    // ── [1] Sanctioned projection band upper ──────────────────────────────────
    datasets.push({
      label: 'sanction_band_upper',
      data: hasSanction ? data.projection.upper.map((p) => ({ x: p.day, y: p.pct })) : [],
      borderColor: 'transparent',
      backgroundColor: 'rgba(210,60,58,0.13)',
      pointRadius: 0,
      fill: '+1',
      order: 10,
    })

    // ── [2] Sanctioned projection band lower (hidden from legend) ─────────────
    datasets.push({
      label: '_sanction_band_lower',
      data: hasSanction ? data.projection.lower.map((p) => ({ x: p.day, y: p.pct })) : [],
      borderColor: 'transparent',
      backgroundColor: 'transparent',
      pointRadius: 0,
      fill: false,
      order: 10,
    })

    // ── [3..3+N-1] Sanctioned comparable curves ───────────────────────────────
    data.comparables.forEach((comp) => {
      const hex = comp.color
      const r = parseInt(hex.slice(1, 3), 16)
      const g = parseInt(hex.slice(3, 5), 16)
      const b = parseInt(hex.slice(5, 7), 16)
      datasets.push({
        label: `[S] ${comp.name} (${comp.sanction_date.slice(0, 4)})`,
        data: comp.curve.map((p) => ({ x: p.day, y: p.pct })),
        borderColor: `rgba(${r},${g},${b},0.35)`,
        hoverBorderColor: hex,
        borderWidth: 1.2,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.2,
        fill: false,
        order: 15,
      })
    })

    // ── [3+N] Control group mean ──────────────────────────────────────────────
    datasets.push({
      label: 'ctrl_mean',
      data: hasControl ? data.control_projection!.mean.map((p) => ({ x: p.day, y: p.pct })) : [],
      borderColor: 'rgba(169,216,251,0.85)',
      borderWidth: 2,
      borderDash: [5, 4],
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.2,
      fill: false,
      order: 5,
    })

    // ── [3+N+1] Control band upper ────────────────────────────────────────────
    datasets.push({
      label: 'ctrl_band_upper',
      data: hasControl ? data.control_projection!.upper.map((p) => ({ x: p.day, y: p.pct })) : [],
      borderColor: 'transparent',
      backgroundColor: 'rgba(169,216,251,0.10)',
      pointRadius: 0,
      fill: '+1',
      order: 20,
    })

    // ── [3+N+2] Control band lower (hidden from legend) ───────────────────────
    datasets.push({
      label: '_ctrl_band_lower',
      data: hasControl ? data.control_projection!.lower.map((p) => ({ x: p.day, y: p.pct })) : [],
      borderColor: 'transparent',
      backgroundColor: 'transparent',
      pointRadius: 0,
      fill: false,
      order: 20,
    })

    // ── [3+N+3..3+N+3+M-1] Control peer curves ───────────────────────────────
    ;(data.control_comparables ?? []).forEach((comp) => {
      datasets.push({
        label: `[C] ${comp.name}`,
        data: comp.curve.map((p) => ({ x: p.day, y: p.pct })),
        borderColor: 'rgba(169,216,251,0.20)',
        hoverBorderColor: 'rgba(169,216,251,0.75)',
        borderWidth: 1,
        pointRadius: 0,
        pointHoverRadius: 3,
        tension: 0.2,
        fill: false,
        order: 18,
      })
    })

    chartRef.current = new Chart(ctx, {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false, axis: 'x' },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#0b2451',
            borderColor: 'rgba(255,255,255,0.1)',
            borderWidth: 1,
            titleColor: '#ffffff',
            bodyColor: 'rgba(255,255,255,0.7)',
            displayColors: true,
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            filter: (item: any) => !item.dataset.label.startsWith('_'),
            callbacks: {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              title: (items: any[]) =>
                items[0].dataset.label.replace(/^\[(S|C)\] /, ''),
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              label: (item: any) => {
                if (item.dataset.label.startsWith('_')) return ''
                const sign = item.parsed.y >= 0 ? '+' : ''
                return `Day ${item.parsed.x}:  ${sign}${item.parsed.y.toFixed(1)}%`
              },
            },
          },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          annotation: {
            annotations: {
              sanctionLine: {
                type: 'line', xMin: 0, xMax: 0,
                borderColor: '#d23c3a', borderWidth: 2, borderDash: [6, 3],
                label: {
                  display: true, content: 'SANCTIONS EVENT', position: 'start',
                  backgroundColor: 'rgba(210,60,58,0.15)', color: '#d23c3a',
                  font: { size: 10, weight: 'bold' },
                  padding: { top: 4, bottom: 4, left: 8, right: 8 },
                },
              },
              zeroLine: {
                type: 'line', yMin: 0, yMax: 0,
                borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
              },
            },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          } as any,
        },
        scales: {
          x: {
            type: 'linear',
            title: { display: true, text: 'Trading Days from Sanctions Event', color: 'rgba(255,255,255,0.7)', font: { size: 12 } },
            grid: { color: 'rgba(255,255,255,0.1)' },
            ticks: { color: 'rgba(255,255,255,0.7)', font: { size: 11 } },
          },
          y: {
            title: { display: true, text: 'Excess Return vs Sector ETF (%)', color: 'rgba(255,255,255,0.7)', font: { size: 12 } },
            grid: { color: 'rgba(255,255,255,0.1)' },
            ticks: {
              color: 'rgba(255,255,255,0.7)', font: { size: 11 },
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              callback: (v: any) => v + '%',
            },
          },
        },
      },
    })

    return () => { chartRef.current?.destroy(); chartRef.current = null }
  }, [data])

  return (
    <div>
      <div style={{ position: 'relative', height: 450 }}>
        <canvas ref={canvasRef} />
      </div>
      <CustomLegend groups={legendGroups} hidden={hiddenDatasets} onToggle={handleToggle} />
    </div>
  )
})

ImpactChart.displayName = 'ImpactChart'
export default ImpactChart
