import type { ReactNode } from 'react';

export interface EmptyStateProps {
  icon?: ReactNode;
  headline: string;
  subtext?: string;
}

export function EmptyState({ icon, headline, subtext }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-10 px-6 text-center">
      {icon && (
        <span className="text-outline opacity-60">{icon}</span>
      )}
      <p className="font-mono text-xs font-bold text-on-surface-variant uppercase tracking-widest">
        {headline}
      </p>
      {subtext && (
        <p className="font-sans text-xs text-outline max-w-[200px] leading-relaxed">
          {subtext}
        </p>
      )}
    </div>
  );
}
