
/**
 * CountryPickChip — one country in the AnalysisConfirmation selected list.
 *
 * Shows flag + ISO3 + relevance tooltip (on hover/focus) + remove button.
 * When `protected` is true the remove button is hidden — actor and target
 * of the seed events cannot be removed without breaking the simulation.
 */

import { X } from 'lucide-react';
import { FlagIcon } from './FlagIcon';
import { RelevanceTooltip } from './RelevanceTooltip';
import { getCountryName } from '@/lib/geo';

export interface CountryPickChipProps {
  iso3: string;
  score: number;
  rationale: string;
  onRemove?: () => void;
  /** If true, hide the remove button (e.g. for actor/target of seed events). */
  isProtected?: boolean;
}

export function CountryPickChip({
  iso3,
  score,
  rationale,
  onRemove,
  isProtected = false,
}: CountryPickChipProps) {
  const name = getCountryName(iso3);
  return (
    <span
      className={[
        'inline-flex items-center gap-1.5',
        'px-2 py-1 bg-surface-container-low/60',
        'border border-outline-variant/40',
        'font-mono text-[11px] text-on-surface',
      ].join(' ')}
      data-testid={`country-chip-${iso3}`}
    >
      <RelevanceTooltip score={score} rationale={rationale}>
        <span className="inline-flex items-center gap-1.5 cursor-help" tabIndex={0}>
          <FlagIcon iso3={iso3} className="w-4 h-3 shrink-0" />
          <span className="font-bold">{iso3}</span>
          <span className="text-on-surface-variant text-[10px]">
            {(score * 100).toFixed(0)}
          </span>
        </span>
      </RelevanceTooltip>
      {!isProtected && onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="text-on-surface-variant hover:text-kinetic transition-colors"
          aria-label={`Remove ${name}`}
        >
          <X size={10} />
        </button>
      )}
    </span>
  );
}
