
/**
 * EventDetailCard — floating card with full event details.
 * Shows: domain badge, event ID, actor→target, action, turn/timestamp,
 *        rationale paragraph, citations.
 */

import { useEffect, useCallback } from 'react';
import { X } from 'lucide-react';
import { DomainBadge } from './DomainBadge';
import { CitationChip } from './CitationChip';
import { ExplainabilityCard } from './ExplainabilityCard';
import { FlagIcon } from './FlagIcon';
import { IconButton } from './ui/IconButton';
import { getDomainMeta } from '@/lib/domain';
import { getCountryName } from '@/lib/geo';
import { formatTimestamp } from '@/lib/time';
import { ESCALATION_LABELS } from '@/lib/types/sim-event';
import type { SimEvent, TriggeringFactor } from '@/lib/types/sim-event';

export interface EventDetailCardProps {
  event: SimEvent;
  onClose: () => void;
  /**
   * Optional click-through for triggering factors (kind=event opens that
   * source event's detail card; other kinds are non-interactive for now).
   */
  onFactorClick?: (factor: TriggeringFactor, event: SimEvent) => void;
}

export function EventDetailCard({ event, onClose, onFactorClick }: EventDetailCardProps) {
  const meta = getDomainMeta(event.domain);
  const actionLabel = event.action_type.replace(/_/g, ' ').toUpperCase();
  const escalationLabel = ESCALATION_LABELS[event.escalation_rung];

  // Close on Escape
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    },
    [onClose],
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  return (
    <div
      className="fixed top-20 right-4 w-[420px] z-50 bg-surface-container-low border border-outline-variant animate-fade-in"
      style={{ boxShadow: `0 0 20px ${meta.hex}33` }}
      role="dialog"
      aria-label="Event detail"
    >
      {/* Header */}
      <div
        className="flex items-center justify-between p-3 border-b"
        style={{
          backgroundColor: `${meta.hex}1a`,
          borderColor: `${meta.hex}4d`,
        }}
      >
        <div className="flex items-center gap-3">
          <DomainBadge domain={event.domain} size="md" />
          {event.payload._origin === 'scenario_seed' && (
            <span
              className="inline-flex items-center font-mono font-bold text-[9px] px-1.5 py-0.5 uppercase tracking-widest border"
              style={{
                color: 'var(--color-outline-variant, rgba(255,255,255,0.7))',
                backgroundColor: 'transparent',
                borderColor: 'var(--color-outline-variant, #1f3864)',
              }}
            >
              SEED
            </span>
          )}
          <span className="font-mono text-xs font-bold text-on-surface tracking-widest">
            EVT-{event.id.slice(0, 7).toUpperCase()}
          </span>
        </div>
        <IconButton
          icon={<X size={14} />}
          label="Close event detail"
          onClick={onClose}
          size="sm"
        />
      </div>

      {/* Body */}
      <div className="p-6 space-y-5">
        {/* Actor → Target — SVG flag + readable country name underneath. */}
        <div className="flex items-start justify-between px-2 gap-3">
          <div className="flex flex-col items-center text-center min-w-0">
            <FlagIcon
              iso3={event.actor_country}
              className="w-12 h-8 ring-1 ring-outline-variant/40 shrink-0"
            />
            <p className="font-sans text-sm font-semibold text-on-surface mt-2 leading-tight">
              {getCountryName(event.actor_country)}
            </p>
            <p className="font-mono text-[10px] text-on-surface-variant tracking-widest mt-0.5">
              {event.actor_country}
            </p>
          </div>
          <div className="flex-1 flex flex-col items-center px-2 pt-3">
            <div
              className="w-full h-px relative"
              style={{
                background: `linear-gradient(to right, transparent, ${meta.hex}, transparent)`,
              }}
            >
              <span
                className="absolute -top-1.5 left-1/2 -translate-x-1/2 w-2.5 h-2.5 animate-pulse-glow"
                style={{ backgroundColor: meta.hex }}
              />
            </div>
            <p
              className="font-mono text-[9px] mt-1 uppercase tracking-tight"
              style={{ color: meta.hex }}
            >
              {getDomainMeta(event.domain).label}
            </p>
          </div>
          <div className="flex flex-col items-center text-center min-w-0">
            {event.target_country ? (
              <FlagIcon
                iso3={event.target_country}
                className="w-12 h-8 ring-1 ring-outline-variant/40 shrink-0"
              />
            ) : (
              <span
                className="w-12 h-8 inline-flex items-center justify-center text-lg ring-1 ring-outline-variant/40 bg-surface-container-low"
                role="img"
                aria-label="Global"
                title="No specific target"
              >
                🌐
              </span>
            )}
            <p className="font-sans text-sm font-semibold text-on-surface mt-2 leading-tight">
              {event.target_country ? getCountryName(event.target_country) : 'Global'}
            </p>
            <p className="font-mono text-[10px] text-on-surface-variant tracking-widest mt-0.5">
              {event.target_country ?? 'ALL'}
            </p>
          </div>
        </div>

        {/* Action + meta */}
        <div className="space-y-1">
          <h2 className="font-sans text-xl font-bold text-on-surface tracking-tight uppercase leading-tight">
            {actionLabel}
          </h2>
          <p className="font-mono text-[10px] text-on-surface-variant">
            Turn {event.turn} · {formatTimestamp(event.timestamp)} · Rung{' '}
            <span className="text-cyber">{event.escalation_rung}</span>{' '}
            {escalationLabel}
          </p>
        </div>

        {/* Explainability — structured "did X because Y in hopes of Z" triplet,
            plus collapsed raw rationale. Falls back to rationale-only for
            legacy events that have no structured triplet. */}
        <ExplainabilityCard event={event} onFactorClick={onFactorClick} />

        {/* Citations */}
        {event.citations.length > 0 && (
          <div className="space-y-2">
            <p className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest border-b border-outline-variant/30 pb-1">
              Intelligence Sources
            </p>
            <div className="flex flex-wrap gap-2">
              {event.citations.map((cit, i) => (
                <CitationChip key={i} citation={cit} />
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Acknowledge footer */}
      <div className="p-4 border-t border-outline-variant/20 bg-background/40">
        <button
          onClick={onClose}
          className="w-full py-2.5 font-mono text-[10px] font-bold tracking-widest uppercase transition-all hover:brightness-110"
          style={{
            backgroundColor: `${meta.hex}1a`,
            border: `1px solid ${meta.hex}4d`,
            color: meta.hex,
          }}
        >
          Acknowledge Intelligence
        </button>
      </div>
    </div>
  );
}
