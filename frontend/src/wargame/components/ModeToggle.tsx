
/**
 * ModeToggle — segmented toggle between Preset and Free-form scenario
 * composition. Visual: two side-by-side cells with the active one lit in
 * cyber-accent; matches the stacked-hud aesthetic of the surrounding
 * ScenarioComposer column.
 */

import type { ComposerMode } from '@/lib/types/scenario';

export interface ModeToggleProps {
  mode: ComposerMode;
  onChange: (mode: ComposerMode) => void;
  disabled?: boolean;
}

const OPTIONS: { value: ComposerMode; label: string }[] = [
  { value: 'preset', label: 'Preset' },
  { value: 'freeform', label: 'Free-form' },
];

export function ModeToggle({ mode, onChange, disabled = false }: ModeToggleProps) {
  return (
    <div
      role="tablist"
      aria-label="Scenario mode"
      className="flex border border-outline-variant/40"
    >
      {OPTIONS.map((opt) => {
        const active = opt.value === mode;
        return (
          <button
            key={opt.value}
            role="tab"
            type="button"
            aria-selected={active}
            disabled={disabled}
            onClick={() => onChange(opt.value)}
            className={[
              'flex-1 py-1.5 font-mono text-[10px] uppercase tracking-widest',
              'transition-colors',
              active
                ? 'bg-cyber/15 text-cyber'
                : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-low/40',
              'disabled:opacity-40 disabled:cursor-not-allowed',
            ].join(' ')}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
