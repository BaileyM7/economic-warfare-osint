
/**
 * AnalysisConfirmation — the confirmation card shown after the user clicks
 * Analyze in the free-form pane. Surfaces the extracted seed events, the
 * auto-selected top-5 countries, and a provenance badge derived from the
 * response's ``source`` field.
 *
 * The user can remove/add countries and remove seed events before clicking
 * Execute. Edits are written back up through onChange callbacks so the
 * parent (FreeformPane) owns the authoritative state; this component is
 * otherwise presentational.
 */

import { Sparkles, AlertTriangle, Cog, X } from 'lucide-react';
import { Button } from './ui/Button';
import { SelectedCountryList } from './SelectedCountryList';
import { DomainBadge } from './DomainBadge';
import type {
  ExtractEventsResponse,
  SeedEvent,
  SelectedCountry,
} from '@/lib/types/scenario';
import type { Domain } from '@/lib/types/sim-event';

export interface AnalysisConfirmationProps {
  result: ExtractEventsResponse;
  selectedCountries: SelectedCountry[];
  onSelectedCountriesChange: (c: SelectedCountry[]) => void;
  seedEvents: SeedEvent[];
  onSeedEventsChange: (events: SeedEvent[]) => void;
  onExecute: () => void;
  onDiscard: () => void;
  isSubmitting?: boolean;
}

const SOURCE_COPY: Record<
  ExtractEventsResponse['source'],
  { icon: typeof Sparkles; label: string; tone: string; hint: string }
> = {
  llm: {
    icon: Sparkles,
    label: 'LLM',
    tone: 'text-cyber border-cyber/40',
    hint: 'Haiku-scored selection.',
  },
  partial_fallback: {
    icon: Cog,
    label: 'Mixed',
    tone: 'text-economic border-economic/40',
    hint: 'LLM partial + heuristic top-up.',
  },
  fallback: {
    icon: AlertTriangle,
    label: 'Heuristic',
    tone: 'text-on-surface-variant border-outline-variant/60',
    hint: 'Deterministic scorer only (LLM unavailable or unusable).',
  },
};

/** ISOs that cannot be removed: actors and targets of any seed event. */
function protectedFromEvents(events: SeedEvent[]): string[] {
  const s = new Set<string>();
  for (const e of events) {
    if (e.actor_country) s.add(e.actor_country);
    if (e.target_country) s.add(e.target_country);
  }
  return [...s];
}

export function AnalysisConfirmation({
  result,
  selectedCountries,
  onSelectedCountriesChange,
  seedEvents,
  onSeedEventsChange,
  onExecute,
  onDiscard,
  isSubmitting = false,
}: AnalysisConfirmationProps) {
  const source = SOURCE_COPY[result.source];
  const SourceIcon = source.icon;
  const protectedIsos = protectedFromEvents(seedEvents);

  const removeEvent = (idx: number) =>
    onSeedEventsChange(seedEvents.filter((_, i) => i !== idx));

  const canExecute = selectedCountries.length >= 2 && !isSubmitting;

  return (
    <div
      className="space-y-3 border border-cyber/30 bg-cyber/[0.03] p-3"
      data-testid="analysis-confirmation"
    >
      {/* Provenance badge */}
      <div className="flex items-center justify-between">
        <span
          className={[
            'inline-flex items-center gap-1 px-1.5 py-0.5',
            'font-mono text-[9px] uppercase tracking-widest',
            'border',
            source.tone,
          ].join(' ')}
          title={source.hint}
        >
          <SourceIcon size={10} />
          {source.label}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-widest text-on-surface-variant">
          Confirm scenario
        </span>
      </div>

      {/* Countries */}
      <SelectedCountryList
        countries={selectedCountries}
        protectedIsos={protectedIsos}
        onChange={onSelectedCountriesChange}
      />

      {/* Seed events */}
      <div className="space-y-1.5">
        <span className="font-mono text-[10px] uppercase tracking-widest text-on-surface-variant">
          Seed events · {seedEvents.length}
        </span>
        {seedEvents.length === 0 ? (
          <p className="font-mono text-[10px] text-on-surface-variant/70">
            No seed events — sim will start from a flat world.
          </p>
        ) : (
          <ul className="space-y-1">
            {seedEvents.map((event, idx) => (
              <li
                key={`${event.actor_country}-${event.action_type}-${idx}`}
                className="flex items-start gap-2 bg-surface-container-lowest px-2 py-1.5 border-l border-outline-variant/40"
              >
                <DomainBadge domain={event.domain as Domain} size="sm" />
                <div className="flex-1 min-w-0 space-y-0.5">
                  <div className="flex items-center gap-1.5 font-mono text-[11px]">
                    <span className="font-bold text-cyber">{event.actor_country}</span>
                    <span className="text-on-surface-variant">→</span>
                    <span className="font-bold text-on-surface">
                      {event.target_country ?? '—'}
                    </span>
                    <span className="text-on-surface-variant text-[10px] truncate">
                      {event.action_type}
                    </span>
                  </div>
                  <p className="font-mono text-[10px] text-on-surface-variant leading-snug line-clamp-2">
                    {event.rationale}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => removeEvent(idx)}
                  className="text-on-surface-variant hover:text-kinetic transition-colors shrink-0"
                  aria-label={`Remove seed event ${idx + 1}`}
                >
                  <X size={12} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Actions */}
      <div className="flex gap-2 pt-1">
        <Button
          variant="ghost"
          size="sm"
          className="flex-1"
          onClick={onDiscard}
          disabled={isSubmitting}
        >
          Discard
        </Button>
        <Button
          variant="primary"
          size="sm"
          className="flex-1"
          onClick={onExecute}
          disabled={!canExecute}
          loading={isSubmitting}
        >
          Execute
        </Button>
      </div>
    </div>
  );
}
