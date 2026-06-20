
import { ChevronDown, ChevronUp, ArrowRight } from 'lucide-react';
import { ExplainabilityCard } from './ExplainabilityCard';
import { FlagIcon } from './FlagIcon';
import type { SimEvent, TriggeringFactor } from '@/lib/types/sim-event';

export interface DecisionRowProps {
  turn: number;
  actionLabel: string;
  reasoningTrace: string;
  isExpanded: boolean;
  onToggle: () => void;
  /**
   * When provided, the expanded body renders the structured ExplainabilityCard
   * (which itself falls back to the raw rationale for legacy events). Without
   * an event, the row falls back to the legacy plain-text reasoning trace.
   */
  event?: SimEvent;
  onFactorClick?: (factor: TriggeringFactor, event: SimEvent) => void;
}

/**
 * Collapsed-row header line — actor flag → target flag → action label.
 * Visible even before the user clicks to expand, so the live sim reads
 * at a glance the same way the Taiwan demo does.
 */
function CollapsedHeader({
  turn,
  actionLabel,
  event,
}: {
  turn: number;
  actionLabel: string;
  event?: SimEvent;
}) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="font-mono text-[10px] text-cyber px-1.5 py-0.5 bg-cyber/10 shrink-0">
        T_{String(turn).padStart(2, '0')}
      </span>
      {event && (
        <span className="inline-flex items-center gap-1">
          <FlagIcon iso3={event.actor_country} className="w-4 h-3" />
          <span className="font-mono text-[10px] text-on-surface-variant">
            {event.actor_country}
          </span>
          {event.target_country && (
            <>
              <ArrowRight size={10} className="text-on-surface-variant" />
              <FlagIcon iso3={event.target_country} className="w-4 h-3" />
              <span className="font-mono text-[10px] text-on-surface-variant">
                {event.target_country}
              </span>
            </>
          )}
        </span>
      )}
      <span className="text-xs font-medium text-on-surface">{actionLabel}</span>
    </div>
  );
}

export function DecisionRow({
  turn,
  actionLabel,
  reasoningTrace,
  isExpanded,
  onToggle,
  event,
  onFactorClick,
}: DecisionRowProps) {
  return (
    <div className="bg-surface-container-high border border-outline-variant/30 overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full p-3 flex justify-between items-center text-left hover:bg-surface-container-highest/50 transition-colors"
        aria-expanded={isExpanded}
      >
        <CollapsedHeader turn={turn} actionLabel={actionLabel} event={event} />
        {isExpanded ? (
          <ChevronUp size={16} className="text-on-surface-variant shrink-0" />
        ) : (
          <ChevronDown size={16} className="text-on-surface-variant shrink-0" />
        )}
      </button>

      {isExpanded && (
        <div className="p-3 bg-surface-container-low border-t border-outline-variant/20 animate-expand-down">
          {event ? (
            <ExplainabilityCard
              event={event}
              onFactorClick={onFactorClick}
              compact
            />
          ) : (
            <>
              <span className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest block mb-2">
                Reasoning Analysis
              </span>
              <p className="font-sans text-xs text-on-surface leading-relaxed">
                {reasoningTrace}
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
