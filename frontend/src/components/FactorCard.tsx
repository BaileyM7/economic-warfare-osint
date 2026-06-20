import { useState } from 'react'
import type { RiskFactor, RiskSeverity } from '../types'

const SEVERITY_STYLES: Record<
  RiskSeverity,
  { chip: string; bar: string; label: string; icon: string }
> = {
  none: {
    chip: 'bg-secondary/15 text-secondary border-secondary/30',
    bar: 'bg-secondary/40',
    label: 'No issue',
    icon: 'check_circle',
  },
  suggested: {
    chip: 'bg-primary/15 text-primary border-primary/30',
    bar: 'bg-primary/60',
    label: 'Suggested review',
    icon: 'info',
  },
  expected: {
    chip: 'bg-tertiary/15 text-tertiary border-tertiary/30',
    bar: 'bg-tertiary/70',
    label: 'Expected finding',
    icon: 'warning',
  },
  discouraged: {
    chip: 'bg-error/15 text-error border-error/30',
    bar: 'bg-error/70',
    label: 'Discouraged',
    icon: 'block',
  },
  prohibited: {
    chip: 'bg-error/20 text-error border-error/50',
    bar: 'bg-error',
    label: 'Prohibited',
    icon: 'gpp_bad',
  },
}

interface Props {
  factor: RiskFactor
}

export default function FactorCard({ factor }: Props) {
  const [expanded, setExpanded] = useState(false)
  const style = SEVERITY_STYLES[factor.severity] ?? SEVERITY_STYLES.none
  const hasEvidence = factor.evidence.length > 0

  return (
    <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-4">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`material-symbols-outlined text-base ${style.chip.split(' ')[1]}`}>
            {style.icon}
          </span>
          <h4 className="text-sm font-semibold text-on-surface truncate">{factor.title}</h4>
        </div>
        <span
          className={`px-2 py-0.5 rounded-full text-[11px] font-semibold border whitespace-nowrap ${style.chip}`}
        >
          {style.label}
        </span>
      </div>

      {/* Score bar */}
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-2xl font-bold text-on-surface tabular-nums">{factor.score}</span>
        <span className="text-[11px] text-outline uppercase tracking-wider">/ 100</span>
      </div>
      <div className="w-full h-1.5 bg-surface-container-lowest rounded-full overflow-hidden mb-3">
        <div
          className={`h-full ${style.bar} transition-all`}
          style={{ width: `${Math.max(0, Math.min(100, factor.score))}%` }}
        />
      </div>

      <p className="text-xs text-on-surface-variant leading-relaxed mb-2">{factor.summary}</p>

      {hasEvidence && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-[11px] text-primary hover:underline flex items-center gap-1"
        >
          <span className="material-symbols-outlined text-xs">
            {expanded ? 'expand_less' : 'expand_more'}
          </span>
          {expanded ? 'Hide' : 'Show'} evidence ({factor.evidence.length})
        </button>
      )}

      {expanded && hasEvidence && (
        <ul className="mt-2 space-y-1.5 border-t border-outline-variant/10 pt-2">
          {factor.evidence.map((ev, i) => (
            <li key={i} className="text-[11px] text-on-surface-variant leading-snug">
              <span className="text-outline font-mono mr-1.5">[{ev.source}]</span>
              {ev.description}
              {ev.date && <span className="text-outline ml-1.5">— {ev.date}</span>}
              {typeof ev.tone === 'number' && (
                <span
                  className={`ml-1.5 font-mono ${ev.tone < -2 ? 'text-error' : 'text-outline'}`}
                >
                  tone {ev.tone.toFixed(1)}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
