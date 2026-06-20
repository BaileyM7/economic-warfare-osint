
export type RedLineStatus = 'clear' | 'near' | 'crossed';

export interface RedLineItemProps {
  label: string;
  status: RedLineStatus;
  triggeredByEventId?: string | null;
  triggeredAtTurn?: number | null;
  onEventClick?: (eventId: string) => void;
}

const statusColor: Record<RedLineStatus, string> = {
  clear: '#4386c3',
  near: '#efb16a',
  crossed: '#d23c3a',
};

const statusLabel: Record<RedLineStatus, string> = {
  clear: 'CLEAR',
  near: 'NEAR',
  crossed: 'CROSSED',
};

export function RedLineItem({
  label,
  status,
  triggeredByEventId,
  triggeredAtTurn,
  onEventClick,
}: RedLineItemProps) {
  const color = statusColor[status];
  const isCrossed = status === 'crossed';

  return (
    <div
      className="p-3 bg-surface-container-low"
      style={{ borderLeft: `2px solid ${color}` }}
    >
      <div className="flex justify-between items-start mb-1">
        <span
          className={[
            'text-xs font-medium',
            isCrossed ? 'text-kinetic' : 'text-on-surface',
          ].join(' ')}
        >
          {label}
        </span>
        <span
          className="font-mono text-[10px] font-bold shrink-0 ml-2"
          style={{ color }}
        >
          {statusLabel[status]}
        </span>
      </div>

      {isCrossed && triggeredAtTurn != null && (
        <p className="font-mono text-[9px] text-kinetic/70 uppercase">
          Triggered Turn {triggeredAtTurn}
          {triggeredByEventId && onEventClick && (
            <button
              onClick={() => onEventClick(triggeredByEventId)}
              className="ml-2 underline hover:text-kinetic transition-colors"
            >
              View
            </button>
          )}
        </p>
      )}

      {status === 'near' && (
        <div className="w-full h-1 bg-surface-container mt-1.5">
          <div className="h-full w-4/5" style={{ backgroundColor: color }} />
        </div>
      )}
    </div>
  );
}
