
/**
 * AgentDrawer — right-side slide-in panel.
 * Shows: country header, posture badge, red lines, recent decisions.
 */

import { useState, useCallback } from 'react';
import { X } from 'lucide-react';
import { IconButton } from './ui/IconButton';
import { RedLineItem } from './RedLineItem';
import { DecisionRow } from './DecisionRow';
import { DomainBadge } from './DomainBadge';
import { EmptyState } from './ui/EmptyState';
import { useSimStore } from '@/lib/store/simStore';
import { useCountry } from '@/hooks/useCountries';
import { getFlag } from '@/lib/geo';
import type { SimEvent, TriggeringFactor } from '@/lib/types/sim-event';

export interface AgentDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  onEventClick: (event: SimEvent) => void;
  /**
   * Click on a triggering factor inside an expanded DecisionRow. Page-level
   * handler resolves `kind=event` factors back to the source SimEvent.
   */
  onFactorClick?: (factor: TriggeringFactor, event: SimEvent) => void;
  drawerWidth: number;
}

export function AgentDrawer({
  isOpen,
  onClose,
  onEventClick,
  onFactorClick,
  drawerWidth,
}: AgentDrawerProps) {
  const selectedCountry = useSimStore((s) => s.selectedCountry);
  const getVisibleEvents = useSimStore((s) => s.visibleEvents);
  // ``collapsedDecisions`` inverts the old "only one expanded at a time"
  // pattern: every decision is expanded by default so the full "X did Y
  // because Z in hopes of W" ExplainabilityCard is visible inline —
  // matching the Taiwan-demo visual.  Users collapse individual rows by
  // clicking the chevron.  Using a Set of collapsed ids keeps the
  // default-expanded behaviour stable as new events stream in.
  const [collapsedDecisions, setCollapsedDecisions] = useState<Set<string>>(
    () => new Set(),
  );

  const { country, isLoading } = useCountry(selectedCountry);

  // Filter events for selected country, most recent 5 decisions
  const countryEvents = useCallback(() => {
    if (!selectedCountry) return [];
    return getVisibleEvents()
      .filter((e) => e.actor_country === selectedCountry)
      .slice(-5)
      .reverse();
  }, [selectedCountry, getVisibleEvents])();

  const currentTurn = useSimStore((s) => s.currentTurn);

  if (!isOpen) return null;

  return (
    <aside
      className="absolute right-0 top-0 h-full bg-surface-container-low border-l border-outline-variant flex flex-col z-30"
      style={{
        width: drawerWidth,
        animation: 'slide-in-right 180ms cubic-bezier(0.22, 1, 0.36, 1)',
      }}
      data-testid="agent-drawer"
    >
      {/* Header */}
      <div className="p-6 border-b border-outline-variant/30">
        <div className="flex justify-between items-start mb-4">
          <div className="flex items-center gap-3">
            <span className="text-2xl" role="img" aria-label={selectedCountry ?? ''}>
              {selectedCountry ? getFlag(selectedCountry) : ''}
            </span>
            <div>
              {isLoading ? (
                <div className="h-5 w-32 bg-surface-container-high animate-pulse" />
              ) : (
                <h2 className="font-sans font-bold text-lg text-on-surface tracking-tight">
                  {country?.name ?? selectedCountry}
                  {selectedCountry && (
                    <span className="font-mono text-sm text-on-surface-variant ml-2">
                      ({selectedCountry})
                    </span>
                  )}
                </h2>
              )}
              <p className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest">
                Region: East Asia
              </p>
            </div>
          </div>
          <IconButton
            icon={<X size={16} />}
            label="Close agent drawer"
            onClick={onClose}
          />
        </div>

        {/* Posture badge — derived from world snapshot */}
        <PostureBadge iso3={selectedCountry} />
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto">
        {/* Red Lines */}
        <section className="p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-mono text-xs font-bold text-on-surface-variant uppercase tracking-widest">
              Strategic Red Lines
            </h3>
            <span className="font-mono text-[10px] text-on-surface-variant">
              MONITORING
            </span>
          </div>
          <RedLinesSection
            iso3={selectedCountry}
            country={country}
            events={countryEvents}
            onEventClick={onEventClick}
          />
        </section>

        {/* Recent Decisions */}
        <section className="p-6 border-t border-outline-variant/20">
          <h3 className="font-mono text-xs font-bold text-on-surface-variant uppercase tracking-widest mb-4">
            Recent Decisions
          </h3>
          {countryEvents.length === 0 ? (
            <EmptyState
              headline="No decisions yet"
              subtext="Decisions appear as the simulation progresses."
            />
          ) : (
            <div className="space-y-2">
              {countryEvents.map((ev) => (
                <DecisionRow
                  key={ev.id}
                  turn={ev.turn}
                  actionLabel={ev.action_type.replace(/_/g, ' ')}
                  reasoningTrace={ev.rationale || 'Reasoning trace not available.'}
                  isExpanded={!collapsedDecisions.has(ev.id)}
                  onToggle={() =>
                    setCollapsedDecisions((prev) => {
                      const next = new Set(prev);
                      if (next.has(ev.id)) next.delete(ev.id);
                      else next.add(ev.id);
                      return next;
                    })
                  }
                  event={ev}
                  onFactorClick={onFactorClick}
                />
              ))}
            </div>
          )}
        </section>
      </div>

      {/* Footer */}
      <div className="p-4 border-t border-outline-variant/30 bg-surface-container-lowest/50">
        <p className="font-mono text-[10px] text-on-surface-variant/60">
          Last updated Turn {currentTurn}
        </p>
      </div>
    </aside>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────

function PostureBadge({ iso3 }: { iso3: string | null }) {
  const worldSnapshot = useSimStore((s) => s.worldSnapshot);
  if (!iso3) return null;

  const postureMap = (
    worldSnapshot as { posture_map?: Record<string, string> }
  )?.posture_map;
  const posture = postureMap?.[iso3] ?? 'nominal';

  const postureColor = posture.includes('aggress') || posture.includes('escalat')
    ? '#d23c3a'
    : posture.includes('deterr') || posture.includes('defense')
    ? '#efb16a'
    : '#4386c3';

  return (
    <div
      className="inline-flex items-center gap-2 px-3 py-1"
      style={{
        backgroundColor: `${postureColor}1a`,
        border: `1px solid ${postureColor}4d`,
      }}
    >
      <span
        className="w-2 h-2 animate-pulse-glow"
        style={{ backgroundColor: postureColor }}
      />
      <span
        className="font-mono text-[11px] font-bold uppercase tracking-wider"
        style={{ color: postureColor }}
      >
        {posture.replace(/_/g, ' ')}
      </span>
    </div>
  );
}

function RedLinesSection({
  iso3,
  country,
  events,
  onEventClick,
}: {
  iso3: string | null;
  country: ReturnType<typeof useCountry>['country'];
  events: SimEvent[];
  onEventClick: (event: SimEvent) => void;
}) {
  if (!iso3 || !country) {
    return (
      <EmptyState
        headline="No red lines loaded"
        subtext="Red lines load with country data."
      />
    );
  }

  // Build red-line statuses from events
  const crossedDomains = new Set(events.filter((e) => e.escalation_rung >= 3).map((e) => e.domain));

  const redLines = country.red_lines ?? [];
  if (redLines.length === 0) {
    return <EmptyState headline="No red lines configured" />;
  }

  return (
    <div className="space-y-2">
      {redLines.slice(0, 6).map((rl, i) => {
        // Heuristic: mark as crossed if there's a high-escalation event this turn
        const crossed = crossedDomains.size > 0 && i === 0;
        const near = !crossed && events.some((e) => e.escalation_rung >= 2) && i <= 1;
        const status = crossed ? 'crossed' : near ? 'near' : 'clear';
        const triggerEvent = crossed ? events.find((e) => e.escalation_rung >= 3) : undefined;

        return (
          <RedLineItem
            key={i}
            label={rl}
            status={status}
            triggeredByEventId={triggerEvent?.id ?? null}
            triggeredAtTurn={triggerEvent?.turn ?? null}
            onEventClick={(evId) => {
              const ev = events.find((e) => e.id === evId);
              if (ev) onEventClick(ev);
            }}
          />
        );
      })}
    </div>
  );
}

// Needed for DomainBadge in PostureBadge usage context
void DomainBadge;
