
import type { SimEvent } from '@/lib/types/sim-event';
import { getDomainMeta } from '@/lib/domain';
import { formatShortTime } from '@/lib/time';
import { FlagIcon } from './FlagIcon';

export interface EventPillProps {
  event: SimEvent;
  isHighlighted: boolean;
  onClick: (event: SimEvent) => void;
}

/** Returns true when the event was injected as a scenario seed. */
function isSeedEvent(event: EventPillProps['event']): boolean {
  return event.payload._origin === 'scenario_seed';
}

export function EventPill({ event, isHighlighted, onClick }: EventPillProps) {
  const meta = getDomainMeta(event.domain);
  const actionLabel = event.action_type.replace(/_/g, ' ').toUpperCase();
  const seed = isSeedEvent(event);

  return (
    <button
      onClick={() => onClick(event)}
      className={[
        'group flex items-center gap-2 border bg-surface-container-low p-2 transition-all duration-150',
        isHighlighted
          ? 'border-cyber/50 bg-cyber/5'
          : 'border-outline-variant/30 hover:border-outline-variant',
      ].join(' ')}
      title={`${event.actor_country} → ${event.target_country ?? 'ALL'}: ${actionLabel}`}
    >
      {/* Domain color bar */}
      <div
        className="w-1 h-8 shrink-0"
        style={{ backgroundColor: meta.hex }}
      />
      <div className="pr-4 text-left">
        <div className="flex items-center gap-1">
          <p
            className="font-mono text-[10px]"
            style={{ color: meta.hex }}
          >
            {formatShortTime(event.timestamp)}
          </p>
          {seed && <SeedBadge />}
        </div>
        <div className="flex items-center gap-1 w-32 truncate text-xs font-bold text-on-surface">
          <FlagIcon iso3={event.actor_country} className="w-4 h-3 shrink-0" />
          <span className="truncate">
            {event.actor_country}
            {event.target_country ? (
              <>
                {' → '}
                <FlagIcon
                  iso3={event.target_country}
                  className="inline-block w-4 h-3 align-middle mx-0.5"
                />
                {event.target_country}
              </>
            ) : (
              ''
            )}
          </span>
        </div>
        <p className="font-mono text-[9px] text-on-surface-variant truncate w-32">
          {actionLabel.slice(0, 18)}
        </p>
      </div>
    </button>
  );
}

/** Small "meta" badge for seed events — uses outline-variant color, not a domain color. */
function SeedBadge() {
  return (
    <span
      className="inline-flex items-center font-mono font-bold text-[8px] px-1 py-0.5 uppercase tracking-widest border"
      style={{
        color: 'var(--color-outline-variant, rgba(255,255,255,0.7))',
        backgroundColor: 'transparent',
        borderColor: 'var(--color-outline-variant, #1f3864)',
      }}
    >
      SEED
    </span>
  );
}
