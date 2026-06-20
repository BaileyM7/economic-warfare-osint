/**
 * Wargame — geopolitical agent simulation, ported from the swarm repo.
 * See `frontend/src/wargame/` for the ported components + store + hooks.
 *
 * Layout: left ScenarioComposer sidebar (320px) | center globe | right AgentDrawer
 *         (360px slide-in) | bottom EventTimeline dock.
 *
 * The page is absolutely positioned inside Emissary's AppShell so the globe
 * fills the viewport below the TopNav (48px) and to the right of SideNav (240px).
 */

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { BookOpen } from 'lucide-react'
import { ScenarioComposer } from '@/components/ScenarioComposer'
import { AgentDrawer } from '@/components/AgentDrawer'
import { EventTimeline } from '@/components/EventTimeline'
import { EventDetailCard } from '@/components/EventDetailCard'
import { DecisionLogPanel } from '@/components/DecisionLogPanel'
import { Loader } from '@/components/ui/Loader'
import { ErrorBoundary } from '@/components/ui/ErrorBoundary'
import { useSimStore } from '@/lib/store/simStore'
import type { SimEvent, TriggeringFactor } from '@/lib/types/sim-event'

// Heavy WebGL view — lazy-loaded.
const WorldView = lazy(() =>
  import('@/components/Globe/WorldView').then((m) => ({ default: m.WorldView })),
)

export default function WargamePage() {
  const selectedCountry = useSimStore((s) => s.selectedCountry)
  const setSelectedCountry = useSimStore((s) => s.setSelectedCountry)
  const simStatus = useSimStore((s) => s.simStatus)
  const events = useSimStore((s) => s.events)

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [timelineExpanded, setTimelineExpanded] = useState(false)
  const [decisionLogOpen, setDecisionLogOpen] = useState(false)
  const [selectedEvent, setSelectedEvent] = useState<SimEvent | null>(null)

  useEffect(() => {
    setDrawerOpen(!!selectedCountry)
  }, [selectedCountry])

  // Auto-open the Decision Log on the first event of a sim, but don't fight a manual close.
  const hasAutoOpenedLog = useRef(false)
  const hasEvents = events.length > 0
  useEffect(() => {
    if (hasEvents && !hasAutoOpenedLog.current) {
      setDecisionLogOpen(true)
      hasAutoOpenedLog.current = true
    }
  }, [hasEvents])

  const handleCountryClick = useCallback(
    (iso3: string) => {
      setSelectedCountry(iso3)
      setDrawerOpen(true)
    },
    [setSelectedCountry],
  )

  const handleDrawerClose = useCallback(() => {
    setDrawerOpen(false)
    setSelectedCountry(null)
  }, [setSelectedCountry])

  const handleEventClick = useCallback((event: SimEvent) => {
    setSelectedEvent(event)
  }, [])

  const handleEventDetailClose = useCallback(() => {
    setSelectedEvent(null)
  }, [])

  const handleFactorClick = useCallback(
    (factor: TriggeringFactor) => {
      if (factor.kind !== 'event' || !factor.verified) return
      const source = events.find((e) => e.id === factor.ref)
      if (source) setSelectedEvent(source)
    },
    [events],
  )

  const timelineHeight = timelineExpanded ? 256 : 64
  const drawerWidth = drawerOpen ? 360 : 0
  const decisionLogWidth = decisionLogOpen ? 380 : 0

  return (
    <div
      className="wargame-page fixed top-12 left-[240px] right-0 bottom-6 flex overflow-hidden"
      style={{ background: 'var(--color-surface-dim)' }}
    >
      {/* Left sidebar: ScenarioComposer — sits flush against Emissary's SideNav.
          marginBottom matches the bottom timeline dock so the Execute button isn't covered. */}
      <aside
        className="w-64 shrink-0 bg-surface-container-lowest border-r border-outline-variant/30 flex flex-col z-30 transition-all duration-200"
        style={{ marginBottom: timelineHeight }}
      >
        <ScenarioComposer />
      </aside>

      {/* Decision log slide-in */}
      <DecisionLogPanel
        isOpen={decisionLogOpen}
        onClose={() => setDecisionLogOpen(false)}
        width={decisionLogWidth}
        onFactorClick={handleFactorClick}
      />

      {/* Center: Deck.gl globe */}
      <main
        className="flex-1 relative overflow-hidden transition-all duration-200"
        style={{ marginBottom: timelineHeight, marginLeft: decisionLogWidth }}
        data-testid="globe-canvas"
      >
        <ErrorBoundary>
          <Suspense fallback={<Loader message="Loading renderer…" />}>
            <WorldView onCountryClick={handleCountryClick} onEventClick={handleEventClick} />
          </Suspense>
        </ErrorBoundary>

        {/* Turn counter HUD (top-right) */}
        <div className="absolute top-4 right-4 z-20 flex flex-col items-end gap-1">
          {simStatus !== null && <TurnCounterHud />}
        </div>

        <SimIdWatermark />
      </main>

      {/* Right drawer: AgentDrawer */}
      <AgentDrawer
        isOpen={drawerOpen}
        onClose={handleDrawerClose}
        onEventClick={handleEventClick}
        onFactorClick={handleFactorClick}
        drawerWidth={drawerWidth}
      />

      {/* Bottom dock: EventTimeline */}
      <EventTimeline
        expanded={timelineExpanded}
        onToggle={() => setTimelineExpanded((v) => !v)}
        onEventClick={handleEventClick}
        height={timelineHeight}
      />

      {/* Decision log toggle */}
      <button
        type="button"
        onClick={() => setDecisionLogOpen((v) => !v)}
        className={[
          'absolute top-4 z-40 flex items-center gap-2 px-3 py-2 font-mono text-[10px] font-bold tracking-widest uppercase border transition-all',
          decisionLogOpen
            ? 'bg-cyber/15 border-cyber/60 text-cyber'
            : 'bg-surface-container border-outline-variant text-on-surface-variant hover:text-on-surface hover:border-cyber/40',
        ].join(' ')}
        style={{ left: 256 + decisionLogWidth + 16 }}
        aria-pressed={decisionLogOpen}
        aria-label="Toggle decision log"
        data-testid="decision-log-toggle"
      >
        <BookOpen size={12} />
        Decision Log
      </button>

      {/* Floating event detail modal */}
      {selectedEvent && (
        <EventDetailCard
          event={selectedEvent}
          onClose={handleEventDetailClose}
          onFactorClick={handleFactorClick}
        />
      )}
    </div>
  )
}

function TurnCounterHud() {
  const currentTurn = useSimStore((s) => s.currentTurn)
  const maxTurns = useSimStore((s) => s.maxTurns)
  const simStatus = useSimStore((s) => s.simStatus)
  if (simStatus === null) return null
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="bg-surface-container border-l-4 border-cyber px-4 py-2">
        <span className="font-mono text-xl font-bold text-cyber tracking-tight">
          Turn {Math.min(currentTurn + 1, maxTurns)} / {maxTurns}
        </span>
      </div>
      <span className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest">
        {simStatus === 'running' ? 'LIVE' : simStatus.toUpperCase()}
      </span>
    </div>
  )
}

function SimIdWatermark() {
  const simId = useSimStore((s) => s.currentSimId)
  if (!simId) return null
  const short = `SIM-${simId.slice(0, 8).toUpperCase()}`
  return (
    <div className="absolute bottom-4 left-4 z-10">
      <span className="font-mono text-[10px] text-outline/60 tracking-[0.2em]">
        {short} // SECURE_LINE_GAMMA
      </span>
    </div>
  )
}
