
/**
 * RelevanceTooltip — hover / focus tooltip that surfaces the backend's
 * relevance score and rationale for a selected country. Rendered inside
 * CountryPickChip; keeps the chip small while letting the analyst confirm
 * WHY a country is in the top-5.
 *
 * Presentation-only — no portal; relies on the parent chip being
 * position: relative so the absolute-positioned bubble anchors correctly.
 */

import type { ReactNode } from 'react';

export interface RelevanceTooltipProps {
  score: number;
  rationale: string;
  children: ReactNode;
}

function scoreColor(score: number): string {
  if (score >= 0.85) return 'text-cyber';
  if (score >= 0.6) return 'text-info';
  if (score >= 0.4) return 'text-economic';
  return 'text-on-surface-variant';
}

export function RelevanceTooltip({ score, rationale, children }: RelevanceTooltipProps) {
  return (
    <span className="group relative inline-flex">
      {children}
      {/*
        Positioning: anchor the tooltip to the chip's LEFT edge and let it
        extend rightward. Previous `left-1/2 -translate-x-1/2` centered it
        and caused left-edge clipping when the chip sat near the sidebar's
        left boundary (the parent has overflow-y-auto, which turns
        overflow-x into auto/hidden per CSS spec — so the tooltip is bounded
        by the sidebar's ~320px width).
        The max-width clamp keeps the tooltip within the viewport when the
        chip is near the right edge too. We accept a slight overlap with
        neighbor chips — z-20 keeps the tooltip on top.
      */}
      <span
        role="tooltip"
        className={[
          'absolute bottom-full left-0 mb-1.5',
          'z-20 w-56 max-w-[calc(100vw-2rem)] pointer-events-none',
          'bg-surface-container-high border border-outline-variant',
          'px-2.5 py-2 space-y-1',
          'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100',
          'transition-opacity duration-100',
        ].join(' ')}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="font-mono text-[10px] uppercase tracking-widest text-on-surface-variant">
            Relevance
          </span>
          <span className={['font-mono text-[10px] font-bold', scoreColor(score)].join(' ')}>
            {(score * 100).toFixed(0)}%
          </span>
        </div>
        <p className="font-mono text-[10px] leading-snug text-on-surface">{rationale}</p>
      </span>
    </span>
  );
}
