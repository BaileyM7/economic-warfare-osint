
import { Lock } from 'lucide-react';
import type { Domain } from '@/lib/types/sim-event';
import { DomainBadge } from './DomainBadge';

export interface PresetCardProps {
  title: string;
  subtitle: string;
  domains: Domain[];
  isComingSoon?: boolean;
  isSelected?: boolean;
  onClick?: () => void;
}

export function PresetCard({
  title,
  subtitle,
  domains,
  isComingSoon = false,
  isSelected = false,
  onClick,
}: PresetCardProps) {
  return (
    <button
      onClick={isComingSoon ? undefined : onClick}
      disabled={isComingSoon}
      className={[
        'w-full p-3 flex flex-col gap-1 text-left transition-all duration-150',
        'border-l-2',
        isSelected
          ? 'bg-cyber/10 border-cyber'
          : isComingSoon
          ? 'bg-surface-container-low/50 border-outline-variant/30 opacity-50 cursor-not-allowed'
          : 'bg-surface-container-low/30 border-outline-variant/30 hover:border-cyber/40 hover:bg-cyber/5',
      ].join(' ')}
      aria-pressed={isSelected}
    >
      <div className="flex items-center justify-between">
        <span
          className={[
            'font-mono text-xs font-bold',
            isSelected ? 'text-cyber' : isComingSoon ? 'text-on-surface-variant' : 'text-on-surface',
          ].join(' ')}
        >
          {title}
        </span>
        {isComingSoon && <Lock size={12} className="text-outline" />}
      </div>
      <span className="font-mono text-[10px] text-on-surface-variant uppercase">
        {subtitle}
      </span>
      {!isComingSoon && (
        <div className="flex gap-1 mt-1 flex-wrap">
          {domains.map((d) => (
            <DomainBadge key={d} domain={d} size="sm" />
          ))}
        </div>
      )}
    </button>
  );
}
