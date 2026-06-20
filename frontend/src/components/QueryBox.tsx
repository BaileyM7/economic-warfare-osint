import { useState } from 'react'
import type { HealthResponse } from '../types'

const KNOWN_MAP: Record<string, string> = {
  'alibaba': 'BABA', 'baba': 'BABA',
  'smic': '0981.HK',
  'tsmc': 'TSM', 'tsm': 'TSM', 'taiwan semiconductor': 'TSM',
  'china mobile': '0941.HK',
  'hikvision': '002415.SZ',
  'xiaomi': '1810.HK',
  'zte': '0763.HK',
  'baidu': 'BIDU', 'bidu': 'BIDU',
  'nio': 'NIO',
  'asml': 'ASML',
  'intel': 'INTC', 'intc': 'INTC',
  'micron': 'MU',
  'huawei': 'BABA',
  'tencent': 'TME', 'tme': 'TME',
  'bilibili': 'BILI', 'bili': 'BILI',
  'pdd': 'PDD', 'pinduoduo': 'PDD',
  'kweb': 'KWEB',
  'full truck': 'YMM', 'ymm': 'YMM',
}

export function extractTicker(input: string): string {
  const lower = input.toLowerCase().trim()
  for (const [name, ticker] of Object.entries(KNOWN_MAP)) {
    if (lower.includes(name)) return ticker
  }
  const match = input.match(/\b([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\b/)
  return match ? match[1] : input.trim().toUpperCase()
}

export type EntityType = 'company' | 'person' | 'sector' | 'vessel'
// The search modes: a free-form "ask anything" (orchestrator swarm) plus the
// four typed entity lookups. 'ask' has no entity field — just a question.
type Tab = 'ask' | EntityType

const TYPE_OPTIONS: { value: EntityType; label: string; icon: string; placeholder: string; example: string }[] = [
  { value: 'company', label: 'Company', icon: 'corporate_fare',  placeholder: 'Company name or ticker',     example: 'Alibaba (BABA)' },
  { value: 'person',  label: 'Person',  icon: 'person',          placeholder: 'Full name',                   example: 'Viktor Vekselberg' },
  { value: 'sector',  label: 'Sector',  icon: 'category',        placeholder: 'Industry or commodity sector', example: 'Semiconductor' },
  { value: 'vessel',  label: 'Vessel',  icon: 'directions_boat', placeholder: 'Vessel name, IMO, or MMSI',   example: 'Ever Given' },
]

// Tab bar: "Ask Anything" leads (the general swarm search), then the typed lookups.
const TABS: { value: Tab; label: string; icon: string }[] = [
  { value: 'ask', label: 'Ask Anything', icon: 'auto_awesome' },
  ...TYPE_OPTIONS.map((o) => ({ value: o.value as Tab, label: o.label, icon: o.icon })),
]

// One-click starter prompts for the Ask-Anything box — onboarding + reliable demo paths.
const EXAMPLE_QUERIES = [
  'What happens to global semiconductor supply if we sanction Fujian Jinhua?',
  'Who ultimately owns Nuctech, and what are its sanctions exposures?',
  'How exposed is the drone supply chain to a DJI export ban?',
  'Map Rosatom’s subsidiaries and their Western trade links.',
]

interface Props {
  loading: boolean
  health: HealthResponse | null
  onAnalyze: (entityType: EntityType, entity: string, question: string) => void
  onDeepAnalyze: (question: string) => void
  onClear: () => void
}

export default function QueryBox({ loading, health, onAnalyze, onDeepAnalyze, onClear }: Props) {
  const [tab, setTab] = useState<Tab>('ask')
  const [entity, setEntity] = useState('')
  const [question, setQuestion] = useState('')

  const isAsk = tab === 'ask'
  const active = TYPE_OPTIONS.find((o) => o.value === tab)

  function handleAnalyze() {
    if (isAsk || !entity.trim()) return
    onAnalyze(tab as EntityType, entity.trim(), question.trim())
  }

  function handleDeep() {
    // Deep analysis works on the full free-form question. If empty, fall back to entity.
    const text = question.trim() || entity.trim()
    if (!text) return
    onDeepAnalyze(text)
  }

  function handleClear() {
    setEntity('')
    setQuestion('')
    onClear()
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      if ((e.ctrlKey || e.metaKey)) {
        e.preventDefault()
        handleDeep()
      } else if (!(e.target instanceof HTMLTextAreaElement)) {
        // Plain Enter on the entity input submits; in textarea Enter should add a newline
        e.preventDefault()
        handleAnalyze()
      }
    }
  }

  return (
    <div className="mb-6 space-y-3">
      {/* Entity type selector */}
      <div className="flex flex-wrap gap-2 items-center">
        {TABS.map((opt) => {
          const isActive = opt.value === tab
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => setTab(opt.value)}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-bold uppercase tracking-wider transition-all flex items-center gap-1.5 border ${
                isActive
                  ? 'bg-primary-container text-on-primary-container border-primary-container'
                  : 'bg-surface-container border-outline-variant/30 text-on-surface-variant hover:bg-surface-container-high'
              }`}
            >
              <span className="material-symbols-outlined text-sm">{opt.icon}</span>
              {opt.label}
            </button>
          )
        })}
        {health && (
          <span className={`ml-auto text-[9px] font-mono px-2 py-0.5 rounded whitespace-nowrap ${
            health.status === 'ok'
              ? 'text-secondary bg-secondary/10 border border-secondary/20'
              : 'text-error bg-error/10 border border-error/20'
          }`}>
            {health.status === 'ok' ? 'API Connected' : health.issues.join(', ')}
          </span>
        )}
      </div>

      {/* Ask Anything — one free-form question, answered by the orchestrator swarm */}
      {isAsk && (
        <div className="bg-surface-container-low rounded-lg p-4 border border-outline-variant/10 space-y-3">
          <div>
            <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
              Ask Anything <span className="text-outline normal-case font-normal tracking-normal">— a free-form question, answered live by the agent swarm</span>
            </label>
            <textarea
              rows={3}
              autoFocus
              className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-on-surface font-body text-sm placeholder:text-outline focus:ring-0 focus:outline-none resize-none"
              placeholder="e.g. What happens to global semiconductor supply if we sanction Fujian Jinhua?"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={handleKeyDown}
            />
          </div>
          <div className="flex flex-wrap gap-1.5 items-center">
            <span className="text-[10px] font-headline uppercase tracking-wider text-outline mr-0.5">Try</span>
            {EXAMPLE_QUERIES.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => setQuestion(q)}
                title={q}
                className="text-[11px] text-on-surface-variant bg-surface-container hover:bg-surface-container-high border border-outline-variant/30 rounded-full px-2.5 py-1 transition-all"
              >
                {q.length > 48 ? q.slice(0, 46) + '…' : q}
              </button>
            ))}
          </div>
          <div className="flex gap-2 pt-1">
            <button
              className="bg-accent text-white px-6 py-2 rounded-lg font-bold text-sm flex items-center gap-2 hover:bg-accent-hover transition-all disabled:opacity-50"
              disabled={loading || !question.trim()}
              onClick={handleDeep}
              title="Run the multi-agent deep analysis (Ctrl+Enter)"
            >
              <span className="material-symbols-outlined text-sm">hub</span>
              Run Deep Analysis
            </button>
            <button
              className="bg-surface-container border border-outline-variant/30 text-on-surface-variant px-4 py-2 rounded-lg text-sm hover:bg-surface-bright transition-all ml-auto"
              onClick={handleClear}
            >
              Clear
            </button>
          </div>
          <p className="text-[10px] text-outline flex items-center gap-1.5 pt-0.5">
            <span className="material-symbols-outlined text-xs">schedule</span>
            Queries ~15 live intelligence sources in parallel — typically takes 3–5 minutes.
          </p>
        </div>
      )}

      {/* Two-input form (typed entity lookups) */}
      {!isAsk && active && (
      <div className="bg-surface-container-low rounded-lg p-4 border border-outline-variant/10 space-y-3">
        <div>
          <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
            {active.label} <span className="text-outline normal-case font-normal tracking-normal">— e.g. {active.example}</span>
          </label>
          <div className="flex items-center gap-3 bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2">
            <span className="material-symbols-outlined text-outline">{active.icon}</span>
            <input
              type="text"
              autoFocus
              className="bg-transparent border-none focus:ring-0 focus:outline-none text-on-surface w-full font-body text-sm placeholder:text-outline"
              placeholder={active.placeholder}
              value={entity}
              onChange={(e) => setEntity(e.target.value)}
              onKeyDown={handleKeyDown}
            />
          </div>
        </div>

        <div>
          <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
            Analyst Question <span className="text-outline normal-case font-normal tracking-normal">(optional — shapes COA recommendations)</span>
          </label>
          <textarea
            rows={2}
            className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-on-surface font-body text-sm placeholder:text-outline focus:ring-0 focus:outline-none resize-none"
            placeholder={`What should we do about ${active.label.toLowerCase()} given current geopolitical context?`}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        </div>

        <div className="flex gap-2 pt-1">
          <button
            className="bg-accent text-white px-6 py-2 rounded-lg font-bold text-sm flex items-center gap-2 hover:bg-accent-hover transition-all disabled:opacity-50"
            disabled={loading || !entity.trim()}
            onClick={handleAnalyze}
          >
            <span className="material-symbols-outlined text-sm">bolt</span>
            Analyze
          </button>
          <button
            className="border border-primary text-primary px-6 py-2 rounded-lg font-bold text-sm flex items-center gap-2 hover:bg-primary/10 transition-all disabled:opacity-50"
            disabled={loading || !(question.trim() || entity.trim())}
            onClick={handleDeep}
            title="Cross-domain analysis using the full question (Ctrl+Enter)"
          >
            <span className="material-symbols-outlined text-sm">biotech</span>
            Deep Analysis
          </button>
          <button
            className="bg-surface-container border border-outline-variant/30 text-on-surface-variant px-4 py-2 rounded-lg text-sm hover:bg-surface-bright transition-all ml-auto"
            onClick={handleClear}
          >
            Clear
          </button>
        </div>
        <p className="text-[10px] text-outline flex items-center gap-1.5 pt-0.5">
          <span className="material-symbols-outlined text-xs">schedule</span>
          <span>
            <span className="font-semibold text-on-surface-variant">Analyze</span> is a fast single lookup ·{' '}
            <span className="font-semibold text-on-surface-variant">Deep Analysis</span> runs the full agent swarm (~3–5 min).
          </span>
        </p>
      </div>
      )}
    </div>
  )
}
