
/**
 * ExplainabilityCard — the structured "X did Y because Z in hopes of W" card.
 * Used in two places:
 *   1. As a section inside EventDetailCard (drill-in from the globe / timeline).
 *   2. As the row body of DecisionLogPanel (chronological browse view).
 *
 * The card never fetches data of its own — it renders the SimEvent's own
 * `explainability` triplet plus a flag emoji for the actor. Click-through on
 * `kind=event` factors is delegated to the parent via `onFactorClick`.
 */

import { ArrowRight } from 'lucide-react';
import { DomainBadge } from './DomainBadge';
import { FlagIcon } from './FlagIcon';
import { getDomainMeta } from '@/lib/domain';
import { getCountryName } from '@/lib/geo';
import { ESCALATION_LABELS } from '@/lib/types/sim-event';
import type {
  SimEvent,
  TriggeringFactor,
} from '@/lib/types/sim-event';

export interface ExplainabilityCardProps {
  event: SimEvent;
  /**
   * Called when the user clicks a triggering factor. The parent decides what
   * to do (typically: if `factor.kind === 'event'`, look up the source event
   * in the store and open its detail card / highlight its arc on the globe).
   */
  onFactorClick?: (factor: TriggeringFactor, event: SimEvent) => void;
  /**
   * When true, render the compact variant suitable for the decision-log feed
   * (smaller padding, no domain badge, narrower factor chips). The drawer /
   * detail-card use the default `compact={false}` form.
   */
  compact?: boolean;
}

export function ExplainabilityCard({
  event,
  onFactorClick,
  compact = false,
}: ExplainabilityCardProps) {
  const meta = getDomainMeta(event.domain);
  const xa = event.explainability;

  // Legacy / seed event — fall back to rationale-only view.
  if (!xa) {
    return (
      <div className="space-y-2 p-3 border border-outline-variant/30 bg-surface-container-lowest/50">
        <p className="font-mono text-[9px] text-on-surface-variant uppercase tracking-widest">
          {compact ? 'Rationale' : 'Decision Rationale (Legacy)'}
        </p>
        <p className="font-sans text-xs text-on-surface leading-relaxed">
          {event.rationale || '(no rationale recorded)'}
        </p>
      </div>
    );
  }

  const turnLabel = `T${event.turn}`;
  const escalationLabel = ESCALATION_LABELS[event.escalation_rung];

  return (
    <div
      className={[
        'border bg-surface-container-lowest/50',
        compact ? 'p-3 space-y-3' : 'p-4 space-y-4',
      ].join(' ')}
      style={{ borderColor: `${meta.hex}33` }}
      data-testid="explainability-card"
    >
      {/* ── Header strip: actor → target (with full country names) ──
          Inline-SVG flag + readable country name so the identity is
          unambiguous. The ISO3 was dropped because the country name carries
          the full identifier and reads better at a glance. */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <FlagIcon iso3={event.actor_country} />
        <span className="font-sans text-xs font-semibold text-on-surface">
          {getCountryName(event.actor_country)}
        </span>
        {event.target_country && (
          <>
            <ArrowRight size={12} className="text-on-surface-variant mx-0.5" />
            <FlagIcon iso3={event.target_country} />
            <span className="font-sans text-xs font-semibold text-on-surface">
              {getCountryName(event.target_country)}
            </span>
          </>
        )}
        <span className="ml-auto flex items-center gap-2 shrink-0">
          {!compact && <DomainBadge domain={event.domain} size="sm" />}
          <span className="font-mono text-[9px] text-on-surface-variant tracking-widest">
            {turnLabel}
          </span>
          <span
            className="font-mono text-[9px] font-bold tracking-widest"
            style={{ color: meta.hex }}
          >
            R{event.escalation_rung}
          </span>
        </span>
      </div>

      {/* ── 1. WHAT (summary) ── */}
      <Slot label="Action">
        <p className="font-sans text-sm font-semibold text-on-surface leading-snug">
          {xa.summary}
        </p>
      </Slot>

      {/* ── 2. WHY (triggering factors) ── */}
      <Slot label="Because">
        <ul className="space-y-1.5">
          {xa.triggering_factors.map((factor, i) => (
            <FactorChip
              key={`${factor.kind}-${factor.ref}-${i}`}
              factor={factor}
              onClick={
                onFactorClick ? () => onFactorClick(factor, event) : undefined
              }
            />
          ))}
        </ul>
      </Slot>

      {/* ── 3. INTENT (intended outcome) ── */}
      <Slot label="In hopes of">
        <p className="font-sans text-xs text-on-surface leading-relaxed italic">
          {xa.intended_outcome}
        </p>
      </Slot>

      {/* ── Raw rationale (collapsed by default) ── */}
      {event.rationale && (
        <details className="group">
          <summary className="font-mono text-[9px] text-on-surface-variant uppercase tracking-widest cursor-pointer hover:text-on-surface transition-colors select-none">
            ▸ Raw Reasoning
          </summary>
          <p className="font-sans text-xs text-on-surface-variant leading-relaxed mt-2 pl-3 border-l border-outline-variant/30">
            {event.rationale}
          </p>
        </details>
      )}

      {/* Sub-line: rung label, only when not compact (panel rows want to stay tight) */}
      {!compact && (
        <p className="font-mono text-[9px] text-on-surface-variant uppercase tracking-widest pt-1 border-t border-outline-variant/20">
          Escalation: {escalationLabel}
        </p>
      )}
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────

function Slot({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <p className="font-mono text-[9px] text-on-surface-variant uppercase tracking-widest">
        {label}
      </p>
      {children}
    </div>
  );
}

const KIND_LABEL: Record<TriggeringFactor['kind'], string> = {
  event: 'EVT',
  red_line: 'RED-LINE',
  memory: 'MEM',
  posture: 'POSTURE',
  perception: 'PERCEPTION',
};

function FactorChip({
  factor,
  onClick,
}: {
  factor: TriggeringFactor;
  onClick?: () => void;
}) {
  const isClickable = onClick !== undefined && factor.kind === 'event' && factor.verified;
  const refDisplay = formatFactorRef(factor);

  // Perception-kind factors come from the data-lake signal pipeline (GDELT,
  // Comtrade, FRED, …) rather than in-sim events. Color them amber so they
  // visually separate from the cyber-blue used for sim-internal evidence.
  const isPerception = factor.kind === 'perception';

  const body = (
    <>
      <span
        className={[
          'inline-flex items-center font-mono font-bold text-[8px] px-1 py-px tracking-widest border shrink-0',
          !factor.verified
            ? 'text-on-surface-variant border-outline-variant/40 bg-transparent border-dashed'
            : isPerception
              ? 'text-amber-300 border-amber-300/40 bg-amber-300/5'
              : 'text-cyber border-cyber/40 bg-cyber/5',
        ].join(' ')}
        title={factor.verified ? '' : 'Reference could not be verified against perception'}
      >
        {KIND_LABEL[factor.kind]}
      </span>
      <span
        className="font-mono text-[10px] text-on-surface-variant tracking-tight min-w-0 max-w-[40%] truncate"
        title={factor.ref}
      >
        {refDisplay}
      </span>
      {/*
        `min-w-0` + `break-words` let long notes wrap inside the flex row
        instead of stretching the parent past its bounds. Without min-w-0
        the default flex behavior ("no shrink below intrinsic min-content")
        pushes long single-line notes past the decision-log right edge.
      */}
      <span className="font-sans text-xs text-on-surface leading-snug min-w-0 break-words">
        {factor.note}
      </span>
    </>
  );

  if (isClickable) {
    return (
      <li className="min-w-0">
        <button
          type="button"
          onClick={onClick}
          className="w-full flex items-start gap-2 text-left p-1.5 transition-colors hover:bg-surface-container-low rounded-none min-w-0"
        >
          {body}
        </button>
      </li>
    );
  }

  return <li className="flex items-start gap-2 p-1.5 min-w-0">{body}</li>;
}

function formatFactorRef(factor: TriggeringFactor): string {
  switch (factor.kind) {
    case 'event':
      return `EVT-${factor.ref.slice(0, 7).toUpperCase()}`;
    case 'memory':
      return factor.ref.toUpperCase();
    case 'posture':
      return factor.ref.toUpperCase();
    case 'red_line':
      return factor.ref.length > 24 ? `${factor.ref.slice(0, 24)}…` : factor.ref;
    case 'perception':
      return factor.ref.length > 24 ? `${factor.ref.slice(0, 24)}…` : factor.ref;
    default:
      return factor.ref;
  }
}
