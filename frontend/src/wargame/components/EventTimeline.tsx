
/**
 * EventTimeline — bottom dock with horizontally scrollable event pills
 * grouped by turn, filterable by domain.
 */

import { useRef } from 'react';
import { ChevronUp, ChevronDown } from 'lucide-react';
import { EventPill } from './EventPill';
import { EmptyState } from './ui/EmptyState';
import { useSimStore } from '@/lib/store/simStore';
import type { SimEvent } from '@/lib/types/sim-event';
import type { DomainFilter } from '@/lib/domain';
import { matchesDomainFilter } from '@/lib/domain';
import { useState } from 'react';

export interface EventTimelineProps {
  expanded: boolean;
  onToggle: () => void;
  onEventClick: (event: SimEvent) => void;
  height: number;
}

const DOMAIN_FILTERS: DomainFilter[] = ['ALL', 'CYBER', 'ECONOMIC', 'KINETIC', 'INFO'];

const FILTER_COLORS: Record<DomainFilter, string> = {
  ALL: '#4386c3',
  CYBER: '#4386c3',
  ECONOMIC: '#efb16a',
  KINETIC: '#d23c3a',
  INFO: '#a9d8fb',
};

export function EventTimeline({ expanded, onToggle, onEventClick, height }: EventTimelineProps) {
  const [activeFilter, setActiveFilter] = useState<DomainFilter>('ALL');

  const getVisibleEvents = useSimStore((s) => s.visibleEvents);
  const selectedCountry = useSimStore((s) => s.selectedCountry);
  const simStatus = useSimStore((s) => s.simStatus);

  const events = getVisibleEvents();
  const filteredEvents = events.filter((e) => matchesDomainFilter(e.domain, activeFilter));
  const trackRef = useRef<HTMLDivElement>(null);

  // Group events by turn
  const byTurn = filteredEvents.reduce<Record<number, SimEvent[]>>((acc, ev) => {
    if (!acc[ev.turn]) acc[ev.turn] = [];
    acc[ev.turn].push(ev);
    return acc;
  }, {});

  const turnNumbers = Object.keys(byTurn)
    .map(Number)
    .sort((a, b) => a - b);

  return (
    <footer
      className="fixed bottom-6 left-80 right-0 z-40 bg-surface-container-low border-t border-outline-variant"
      style={{ height }}
    >
      {/* ── Toggle / header bar ── */}
      <div
        className="flex items-center justify-between px-6 h-16 cursor-pointer select-none"
        onClick={onToggle}
        role="button"
        aria-expanded={expanded}
        aria-label="Toggle event timeline"
      >
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs font-bold text-cyber uppercase tracking-widest">
            EVENTS
          </span>
          <span className="font-mono text-[10px] bg-cyber/20 text-cyber px-1.5 py-0.5">
            {filteredEvents.length}
          </span>
          {simStatus === 'running' && (
            <span className="font-mono text-[10px] text-cyber animate-pulse">
              LIVE TELEMETRY
            </span>
          )}
        </div>

        <div className="flex items-center gap-4">
          <span className="font-mono text-[10px] text-on-surface-variant">
            FILTER: [{activeFilter}]
          </span>
          {expanded ? (
            <ChevronDown size={16} className="text-on-surface-variant" />
          ) : (
            <ChevronUp size={16} className="text-on-surface-variant" />
          )}
        </div>
      </div>

      {/* ── Expanded content ── */}
      {expanded && (
        <>
          {/* Domain filter tabs */}
          <div className="flex items-center px-6 h-10 border-b border-outline-variant/30">
            {DOMAIN_FILTERS.map((filter) => {
              const isActive = activeFilter === filter;
              const color = FILTER_COLORS[filter];
              return (
                <button
                  key={filter}
                  onClick={(e) => {
                    e.stopPropagation();
                    setActiveFilter(filter);
                  }}
                  className="px-4 h-10 font-mono text-[10px] uppercase tracking-widest transition-colors"
                  style={{
                    color: isActive ? color : undefined,
                    borderBottom: isActive ? `2px solid ${color}` : '2px solid transparent',
                    backgroundColor: isActive ? `${color}0d` : undefined,
                  }}
                >
                  {filter}
                </button>
              );
            })}
          </div>

          {/* Scrollable timeline track */}
          <div
            ref={trackRef}
            className="flex-1 overflow-x-auto overflow-y-hidden flex px-6 py-3 gap-6 no-scrollbar"
            style={{ height: height - 64 - 40 }}
          >
            {filteredEvents.length === 0 ? (
              <div className="flex-1 flex items-center justify-center">
                <EmptyState
                  headline="No events"
                  subtext={
                    events.length === 0
                      ? 'Run a simulation to see events'
                      : `No ${activeFilter} events`
                  }
                />
              </div>
            ) : (
              turnNumbers.map((turn) => (
                <TurnGroup
                  key={turn}
                  turn={turn}
                  events={byTurn[turn]}
                  selectedCountry={selectedCountry}
                  onEventClick={onEventClick}
                />
              ))
            )}
          </div>
        </>
      )}
    </footer>
  );
}

// ── Turn group ──────────────────────────────────────────────────────────────

function TurnGroup({
  turn,
  events,
  selectedCountry,
  onEventClick,
}: {
  turn: number;
  events: SimEvent[];
  selectedCountry: string | null;
  onEventClick: (event: SimEvent) => void;
}) {
  return (
    <div className="flex flex-col gap-2 min-w-max border-l border-outline-variant/30 pl-4 first:border-0 first:pl-0">
      <span className="font-mono text-[9px] text-on-surface-variant/60 mb-1">
        T{turn}
      </span>
      <div className="flex gap-3">
        {events.map((ev) => (
          <EventPill
            key={ev.id}
            event={ev}
            isHighlighted={
              selectedCountry !== null &&
              (ev.actor_country === selectedCountry ||
                ev.target_country === selectedCountry)
            }
            onClick={onEventClick}
          />
        ))}
      </div>
    </div>
  );
}
