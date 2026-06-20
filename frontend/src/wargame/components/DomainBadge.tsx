import type { Domain } from '@/lib/types/sim-event';
import { getDomainLabel, getDomainMeta } from '@/lib/domain';

export type DomainBadgeSize = 'sm' | 'md';

export interface DomainBadgeProps {
  domain: Domain;
  size?: DomainBadgeSize;
}

const sizeClasses: Record<DomainBadgeSize, string> = {
  sm: 'text-[9px] px-1.5 py-0.5',
  md: 'text-[10px] px-2 py-0.5',
};

export function DomainBadge({ domain, size = 'md' }: DomainBadgeProps) {
  const meta = getDomainMeta(domain);
  return (
    <span
      className={[
        'inline-flex items-center font-mono font-bold uppercase tracking-widest',
        sizeClasses[size],
      ].join(' ')}
      style={{
        color: meta.hex,
        backgroundColor: `${meta.hex}1a`, // hex + 10% opacity
        border: `1px solid ${meta.hex}4d`, // 30% opacity border
      }}
    >
      {getDomainLabel(domain)}
    </span>
  );
}
