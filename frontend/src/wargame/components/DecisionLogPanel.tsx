
/**
 * DecisionLogPanel — chronological browse view of agent decisions.
 *
 * Renders one ExplainabilityCard per accepted SimEvent, grouped by turn.
 * Filterable by actor country (uses the existing selectedCountry from the
 * sim store — clicking a country on the globe scopes the panel automatically).
 *
 * Lives as a slide-in left-edge panel so it doesn't fight the right-side
 * AgentDrawer; the open/close toggle is owned by the parent (globe page).
 */

import { useMemo } from 'react';
import { X, Filter } from 'lucide-react';
import { ExplainabilityCard } from './ExplainabilityCard';
import { IconButton } from './ui/IconButton';
import { EmptyState } from './ui/EmptyState';
import { useSimStore } from '@/lib/store/simStore';
import type { SimEvent, TriggeringFactor } from '@/lib/types/sim-event';

export interface DecisionLogPanelProps {
  isOpen: boolean;
  onClose: () => void;
  /**
   * Width when open; 0 when closed (parent collapses the layout slot).
   */
  width: number;
  /**
   * Click on a `kind=event` factor — parent wires this to "open the source
   * SimEvent's detail card and (optionally) highlight its arc on the globe."
   */
  onFactorClick: (factor: TriggeringFactor, event: SimEvent) => void;
}

export function DecisionLogPanel({
  isOpen,
  onClose,
  width,
  onFactorClick,
}: DecisionLogPanelProps) {
  // Subscribe to the underlying state directly. Using the `visibleEvents`
  // getter would only re-run on selectedCountry change because the function
  // reference is stable — so streamed-in events would never appear.
  const allEvents = useSimStore((s) => s.events);
  const playbackMode = useSimStore((s) => s.playbackMode);
  const scrubberTurn = useSimStore((s) => s.scrubberTurn);
  const selectedCountry = useSimStore((s) => s.selectedCountry);
  const setSelectedCountry = useSimStore((s) => s.setSelectedCountry);

  const events = useMemo(() => {
    const inWindow =
      playbackMode === 'live'
        ? allEvents
        : allEvents.filter((e) => e.turn <= scrubberTurn);
    const byActor = selectedCountry
      ? inWindow.filter((e) => e.actor_country === selectedCountry)
      : inWindow;
    // Drop empty-content events — when the agent model fails to emit a tool
    // call and the content body is also empty, the card degrades to a
    // "(no rationale recorded)" row that carries zero information. Hiding
    // those here (vs in ExplainabilityCard) keeps the card reusable
    // elsewhere and keeps the decision log scannable.
    const withContent = byActor.filter((e) => e.explainability || e.rationale?.trim());
    // Reverse-chronological so newest decisions are at the top
    return [...withContent].reverse();
  }, [allEvents, playbackMode, scrubberTurn, selectedCountry]);

  const grouped = useMemo(() => groupByTurn(events), [events]);

  if (!isOpen) return null;

  return (
    <aside
      className="absolute left-80 top-0 h-full bg-surface-container-low border-r border-outline-variant flex flex-col z-30"
      style={{
        width,
        animation: 'slide-in-left 180ms cubic-bezier(0.22, 1, 0.36, 1)',
      }}
      data-testid="decision-log-panel"
      aria-label="Decision log"
    >
      {/* ── Header ── */}
      <div className="p-4 border-b border-outline-variant/30 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] font-bold text-cyber tracking-widest uppercase">
            ▌ Decision Log
          </span>
          {selectedCountry && (
            <span className="inline-flex items-center gap-1 font-mono text-[9px] px-1.5 py-0.5 border border-cyber/40 text-cyber tracking-widest">
              <Filter size={9} />
              {selectedCountry}
              <button
                type="button"
                onClick={() => setSelectedCountry(null)}
                className="ml-1 hover:text-on-surface transition-colors"
                aria-label="Clear filter"
              >
                <X size={9} />
              </button>
            </span>
          )}
        </div>
        <IconButton
          icon={<X size={14} />}
          label="Close decision log"
          onClick={onClose}
          size="sm"
        />
      </div>

      {/* ── Body ── */}
      <div className="flex-1 overflow-y-auto p-3 space-y-5">
        {grouped.length === 0 ? (
          <EmptyState
            headline="No decisions yet"
            subtext={
              selectedCountry
                ? `No decisions logged for ${selectedCountry} in this run.`
                : 'Start a simulation to see structured agent decisions appear here.'
            }
          />
        ) : (
          grouped.map(({ turn, events: turnEvents }) => (
            <section key={turn} className="space-y-2">
              <header className="sticky top-0 z-10 bg-surface-container-low py-1 -mx-3 px-3 border-b border-outline-variant">
                <p className="font-mono text-[10px] font-bold text-on-surface-variant uppercase tracking-widest">
                  Turn {turn} · {turnEvents.length}{' '}
                  {turnEvents.length === 1 ? 'decision' : 'decisions'}
                </p>
              </header>
              <div className="space-y-2">
                {turnEvents.map((e) => (
                  <ExplainabilityCard
                    key={e.id}
                    event={e}
                    onFactorClick={onFactorClick}
                    compact
                  />
                ))}
              </div>
            </section>
          ))
        )}
      </div>
    </aside>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────────────

interface TurnGroup {
  turn: number;
  events: SimEvent[];
}

function groupByTurn(events: SimEvent[]): TurnGroup[] {
  const map = new Map<number, SimEvent[]>();
  for (const e of events) {
    const bucket = map.get(e.turn);
    if (bucket) bucket.push(e);
    else map.set(e.turn, [e]);
  }
  // events are reverse-chronological coming in, so iterating the map
  // preserves newest-turn-first ordering as long as we sort descending.
  return [...map.entries()]
    .sort(([a], [b]) => b - a)
    .map(([turn, evs]) => ({ turn, events: evs }));
}
