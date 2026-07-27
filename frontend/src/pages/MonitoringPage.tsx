import { useEffect, useState } from 'react'
import MonitoringMap from '../components/MonitoringMap'
import { fetchKPIs, fetchActivity, fetchMapData, fetchMacro } from '../api'
import { useMonitoringSocket } from '../hooks/useMonitoringSocket'
import type { KPIData, ActivityEntry, MapMarker, MacroData } from '../types'

function relativeTime(iso: string | null): string {
  if (!iso) return '--:--:--'
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 0) return 'just now'
  const secs = Math.floor(diff / 1000)
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

function severityIcon(severity: string): { icon: string; bgClass: string; colorClass: string } {
  switch (severity) {
    case 'warning':
      return { icon: 'warning', bgClass: 'bg-tertiary-container/20', colorClass: 'text-tertiary' }
    case 'error':
      return { icon: 'error', bgClass: 'bg-error-container/20', colorClass: 'text-error' }
    default:
      return { icon: 'info', bgClass: 'bg-surface-container-highest', colorClass: 'text-outline' }
  }
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC' }) + ' Z'
}

export default function MonitoringPage() {
  const [kpis, setKpis] = useState<KPIData | null>(null)
  const [activity, setActivity] = useState<ActivityEntry[]>([])
  const [markers, setMarkers] = useState<MapMarker[]>([])
  const [macro, setMacro] = useState<MacroData | null>(null)
  const [alertsOnly, setAlertsOnly] = useState(false)

  // WebSocket for real-time updates
  const { kpis: wsKpis, latestActivity: wsLatestActivity, connected: wsConnected } = useMonitoringSocket()

  // Merge WebSocket KPI updates into local state
  useEffect(() => {
    if (wsKpis) setKpis(wsKpis)
  }, [wsKpis])

  // Prepend WebSocket activity updates to the activity list
  useEffect(() => {
    if (wsLatestActivity) {
      setActivity((prev) => {
        // Avoid duplicates by checking id
        if (prev.some((a) => a.id === wsLatestActivity.id)) return prev
        return [wsLatestActivity, ...prev]
      })
    }
  }, [wsLatestActivity])

  const filteredActivity = alertsOnly
    ? activity.filter((a) => a.severity === 'warning' || a.severity === 'error')
    : activity

  useEffect(() => {
    // Initial fetch
    fetchKPIs().then(setKpis).catch(() => {})
    fetchActivity().then(setActivity).catch(() => {})
    fetchMapData().then(setMarkers).catch(() => {})
    fetchMacro().then(setMacro).catch(() => {})

    // KPIs + activity: 30s polling fallback (WebSocket provides instant updates when connected)
    const fastInterval = setInterval(() => {
      fetchKPIs().then(setKpis).catch(() => {})
      fetchActivity().then(setActivity).catch(() => {})
    }, 30_000)

    // Macro + map: 15-minute refresh (backend caches FRED data for 15 min)
    const slowInterval = setInterval(() => {
      fetchMacro().then(setMacro).catch(() => {})
      fetchMapData().then(setMarkers).catch(() => {})
    }, 900_000)

    return () => {
      clearInterval(fastInterval)
      clearInterval(slowInterval)
    }
  }, [])

  const kpiCards = [
    { label: 'Active COAs', value: kpis ? String(kpis.active_coas) : '--', accent: 'border-primary' },
    { label: 'Total COAs', value: kpis ? String(kpis.total_coas) : '--', accent: 'border-tertiary' },
    { label: 'Total Activity', value: kpis ? String(kpis.total_activity) : '--', accent: 'border-secondary' },
    { label: 'Last Event', value: kpis ? relativeTime(kpis.last_event) : '--:--:--', accent: 'border-outline' },
  ]

  return (
    <div className="h-[calc(100vh-48px)] p-6 flex flex-col gap-6 overflow-hidden">
      {/* KPI Cards */}
      <section data-vn="kpis" className="grid grid-cols-1 md:grid-cols-4 gap-4 flex-none">
        {kpiCards.map((kpi) => (
          <div
            key={kpi.label}
            className={`bg-surface-container p-4 rounded-lg border-l-2 ${kpi.accent} transition-all hover:bg-surface-container-high`}
          >
            <div className="text-[10px] font-headline uppercase tracking-widest text-outline mb-1">
              {kpi.label}
            </div>
            <div className="text-3xl font-headline font-bold text-on-surface">
              {kpi.value}
            </div>
          </div>
        ))}
      </section>

      {/* Main Content Grid */}
      <section className="flex-1 grid grid-cols-12 gap-6 min-h-0">
        {/* Map + Financial Indicators */}
        <div className="col-span-12 lg:col-span-8 flex flex-col gap-6 min-h-0">
          {/* Map */}
          <div className="flex-1 bg-surface-container-low rounded-lg relative overflow-hidden">
            {/*
              HUD overlay — temporarily hidden because the labels are decorative placeholders:
              - SCANNING_GRID_44X2 is static text with no real grid system behind it
              - COORD just shows the lat/lon of the first marker, not an active scanning target
              Restore this block once we have a real scanning/geofence feature to surface.
            */}
            {/*
            <div className="absolute top-4 left-4 z-[1000] flex flex-col gap-2">
              <div className="glass-panel px-3 py-1.5 rounded text-[10px] font-mono text-primary flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                SCANNING_GRID_44X2
              </div>
              <div className="glass-panel px-3 py-1.5 rounded text-[10px] font-mono text-tertiary">
                COORD: {markers.length > 0
                  ? `${markers[0].lat.toFixed(3)}N / ${markers[0].lon.toFixed(3)}E`
                  : '---.---N / ---.---E'}
              </div>
            </div>
            */}
            <MonitoringMap markers={markers} />
            <div className="absolute inset-0 bg-gradient-to-t from-surface-dim/80 to-transparent pointer-events-none" />
          </div>

          {/* Financial Indicators — Dense Sparklines */}
          {(() => {
            const brentData = macro?.sparklines.capital_flight ?? []
            const vixData = macro?.sparklines.currency_vol ?? []
            const dxyData = macro?.sparklines.equity_swap ?? []

            const sparklines = [
              {
                label: 'Brent Crude (USD/bbl)',
                data: brentData,
                latest: macro?.brent ? `$${macro.brent.toFixed(2)}` : '--',
              },
              {
                label: 'VIX Volatility',
                data: vixData,
                latest: macro?.vix ? `${macro.vix.toFixed(2)}` : '--',
              },
              {
                label: 'Dollar Index (DXY)',
                data: dxyData,
                latest: dxyData.length > 0 ? dxyData[dxyData.length - 1].toFixed(2) : '--',
              },
            ]

            return (
              <div className="h-40 bg-surface-container rounded-lg p-4 flex flex-col">
                <div className="flex justify-between items-center mb-4">
                  <h3 className="text-[10px] font-headline uppercase tracking-widest text-outline">
                    Macroeconomic Flows
                  </h3>
                  <span className="text-[10px] font-mono text-outline uppercase tracking-wider">
                    Last 10 Trading Days
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-6 flex-1 min-h-0">
                  {sparklines.map(({ label, data, latest }) => {
                    const hasData = data.length >= 2
                    const max = hasData ? Math.max(...data) : 1
                    const min = hasData ? Math.min(...data) : 0
                    // Pad the y-axis range slightly so the line never touches the edges
                    const padding = (max - min) * 0.15 || 1
                    const yMin = min - padding
                    const yMax = max + padding
                    const yRange = yMax - yMin || 1
                    // % change from first to last observation
                    const pctChange = hasData ? ((data[data.length - 1] - data[0]) / data[0]) * 100 : 0
                    const isUp = pctChange >= 0
                    // Direction-based color: blue if up, red if down (brand has no green)
                    const lineColor = isUp ? '#a9d8fb' : '#ff5a58'
                    const fillColor = isUp ? 'rgba(169, 216, 251, 0.15)' : 'rgba(255, 90, 88, 0.15)'

                    // Build SVG path (viewBox 100x40)
                    const w = 100
                    const h = 40
                    const points = hasData ? data.map((v, i) => {
                      const x = (i / (data.length - 1)) * w
                      const y = h - ((v - yMin) / yRange) * h
                      return `${x},${y}`
                    }) : []
                    const linePath = points.length > 0 ? `M ${points.join(' L ')}` : ''
                    const areaPath = points.length > 0 ? `M 0,${h} L ${points.join(' L ')} L ${w},${h} Z` : ''
                    const lastX = hasData ? w : 0
                    const lastY = hasData ? h - ((data[data.length - 1] - yMin) / yRange) * h : h / 2

                    return (
                      <div key={label} className="flex flex-col min-h-0 overflow-hidden">
                        <div className="flex justify-between items-baseline text-[10px] mb-1 flex-none">
                          <span className="text-on-surface-variant truncate">{label}</span>
                          <span className="text-on-surface font-mono font-bold shrink-0 ml-2">{latest}</span>
                        </div>
                        <div className="flex-1 relative min-h-0 overflow-hidden">
                          {hasData ? (
                            <>
                              <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="absolute inset-0 w-full h-full block">
                                <path d={areaPath} fill={fillColor} />
                                <path d={linePath} fill="none" stroke={lineColor} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
                                <circle cx={lastX} cy={lastY} r="2.5" fill={lineColor} vectorEffect="non-scaling-stroke" />
                              </svg>
                              <div className="absolute bottom-0 left-0 text-[9px] font-mono text-outline pointer-events-none">
                                {min.toFixed(2)}
                              </div>
                              <div className={`absolute top-0 right-0 text-[9px] font-mono pointer-events-none ${isUp ? 'text-secondary' : 'text-error'}`}>
                                {isUp ? '+' : ''}{pctChange.toFixed(2)}%
                              </div>
                            </>
                          ) : (
                            <div className="absolute inset-0 flex items-center justify-center text-[10px] text-outline">
                              No data
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })()}
        </div>

        {/* Activity Log */}
        <div data-vn="activity-log" className="col-span-12 lg:col-span-4 flex flex-col min-h-0">
          <div className="bg-surface-container rounded-lg flex flex-col h-full overflow-hidden">
            <div className="p-4 border-b border-outline-variant/15 flex justify-between items-center">
              <div className="flex items-center gap-2">
                <h3 className="text-[10px] font-headline uppercase tracking-widest text-on-surface">
                  Chronological Activity Log
                </h3>
                {/* WebSocket connection indicator */}
                <span
                  className={`w-2 h-2 rounded-full flex-none ${wsConnected ? 'bg-primary' : 'bg-tertiary animate-pulse'}`}
                  title={wsConnected ? 'Live (WebSocket connected)' : 'Polling fallback (WebSocket disconnected)'}
                />
              </div>
              <button
                onClick={() => setAlertsOnly((v) => !v)}
                className={`material-symbols-outlined text-sm cursor-pointer transition-colors ${alertsOnly ? 'text-error' : 'text-outline hover:text-primary'}`}
                title={alertsOnly ? 'Show all events' : 'Show alerts only'}
              >
                {alertsOnly ? 'filter_list_off' : 'filter_list'}
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {filteredActivity.length === 0 && (
                <div className="flex gap-3 items-start">
                  <div className="w-8 h-8 rounded bg-surface-container-highest flex items-center justify-center flex-none mt-0.5">
                    <span className="material-symbols-outlined text-outline text-sm">info</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex justify-between items-center mb-1">
                      <span className="text-[10px] font-mono text-tertiary">--:--:-- Z</span>
                      <span className="text-[9px] text-outline uppercase tracking-tighter">SYSTEM</span>
                    </div>
                    <p className="text-xs text-on-surface-variant leading-relaxed">No activity yet — create a COA, generate a briefing, or start an exercise to see events here.</p>
                  </div>
                </div>
              )}
              {filteredActivity.map((entry) => {
                const { icon, bgClass, colorClass } = severityIcon(entry.severity)
                return (
                  <div key={entry.id} className="flex gap-3 items-start">
                    <div className={`w-8 h-8 rounded ${bgClass} flex items-center justify-center flex-none mt-0.5`}>
                      <span className={`material-symbols-outlined ${colorClass} text-sm`}>
                        {icon}
                      </span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-[10px] font-mono text-tertiary">
                          {formatTimestamp(entry.timestamp)}
                        </span>
                        <span className="text-[9px] text-outline uppercase tracking-tighter">
                          {entry.source}
                        </span>
                      </div>
                      <p className="text-xs text-on-surface-variant leading-relaxed">{entry.message}</p>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
