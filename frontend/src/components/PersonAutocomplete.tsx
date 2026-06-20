import { useCallback, useEffect, useRef, useState } from 'react'
import { searchPersons } from '../api'
import type { PersonCandidate } from '../types'

interface Props {
  value: string
  onChange: (next: string) => void
  onSelect: (candidate: PersonCandidate) => void
  onEnterWithNoSelection: () => void
  placeholder?: string
  autoFocus?: boolean
}

const DEBOUNCE_MS = 250
const MIN_QUERY_LEN = 2

export default function PersonAutocomplete({
  value,
  onChange,
  onSelect,
  onEnterWithNoSelection,
  placeholder = 'Full name',
  autoFocus = true,
}: Props) {
  const [candidates, setCandidates] = useState<PersonCandidate[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const queryRef = useRef(value)
  queryRef.current = value

  // Debounced fetch
  useEffect(() => {
    const trimmed = value.trim()
    if (trimmed.length < MIN_QUERY_LEN) {
      setCandidates([])
      setLoading(false)
      setError(null)
      return
    }

    const handle = setTimeout(async () => {
      if (abortRef.current) abortRef.current.abort()
      const ac = new AbortController()
      abortRef.current = ac
      setLoading(true)
      setError(null)
      try {
        const results = await searchPersons(trimmed, 10, ac.signal)
        // Guard against stale responses arriving after the input has moved on.
        if (queryRef.current.trim() !== trimmed) return
        setCandidates(results)
        setHighlight(0)
        setOpen(true)
      } catch (e) {
        if ((e as Error).name === 'AbortError') return
        setError((e as Error).message || 'Search failed')
        setCandidates([])
      } finally {
        setLoading(false)
      }
    }, DEBOUNCE_MS)

    return () => clearTimeout(handle)
  }, [value])

  // Close on click outside
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!wrapperRef.current) return
      if (!wrapperRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  const handleSelect = useCallback(
    (c: PersonCandidate) => {
      onChange(c.name)
      onSelect(c)
      setOpen(false)
    },
    [onChange, onSelect],
  )

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || candidates.length === 0) {
      if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault()
        onEnterWithNoSelection()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlight((h) => Math.min(h + 1, candidates.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault()
      const picked = candidates[highlight]
      if (picked) handleSelect(picked)
      else onEnterWithNoSelection()
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div ref={wrapperRef} className="relative w-full">
      <div className="flex items-center gap-3 bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2">
        <span className="material-symbols-outlined text-outline">person_search</span>
        <input
          ref={inputRef}
          type="text"
          autoFocus={autoFocus}
          className="bg-transparent border-none focus:ring-0 focus:outline-none text-on-surface w-full font-body text-sm placeholder:text-outline"
          placeholder={placeholder}
          value={value}
          onChange={(e) => {
            onChange(e.target.value)
            setOpen(true)
          }}
          onFocus={() => {
            if (candidates.length > 0) setOpen(true)
          }}
          onKeyDown={handleKeyDown}
          role="combobox"
          aria-expanded={open}
          aria-controls="person-autocomplete-listbox"
          aria-autocomplete="list"
        />
        {loading && (
          <span className="material-symbols-outlined text-outline animate-spin text-base">
            progress_activity
          </span>
        )}
      </div>

      {open && (loading || error || candidates.length > 0 || value.trim().length >= MIN_QUERY_LEN) && (
        <div
          id="person-autocomplete-listbox"
          role="listbox"
          className="absolute z-50 mt-1 w-full bg-surface-container border border-outline-variant/30 rounded-lg shadow-lg overflow-hidden max-h-96 overflow-y-auto"
        >
          {error && (
            <div className="px-3 py-2 text-xs text-error">{error}</div>
          )}
          {!error && !loading && candidates.length === 0 && value.trim().length >= MIN_QUERY_LEN && (
            <div className="px-3 py-2 text-xs text-outline italic">No matches.</div>
          )}
          {candidates.map((c, i) => (
            <CandidateRow
              key={`${c.name}-${i}`}
              candidate={c}
              active={i === highlight}
              onHover={() => setHighlight(i)}
              onClick={() => handleSelect(c)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

interface RowProps {
  candidate: PersonCandidate
  active: boolean
  onHover: () => void
  onClick: () => void
}

function CandidateRow({ candidate, active, onHover, onClick }: RowProps) {
  const { name, sanctioned, sanction_programs, primary_affiliation, country, sources } = candidate
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      onMouseEnter={onHover}
      onClick={onClick}
      className={`w-full text-left px-3 py-2 flex items-start gap-3 border-b border-outline-variant/10 last:border-b-0 transition-colors ${
        active ? 'bg-surface-container-high' : 'bg-transparent hover:bg-surface-container-high/60'
      }`}
    >
      {/* Sanctions indicator */}
      <span
        className={`mt-0.5 w-2 h-2 rounded-full flex-shrink-0 ${
          sanctioned ? 'bg-error' : 'bg-outline/40'
        }`}
        aria-label={sanctioned ? 'Sanctioned' : 'No sanctions match'}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-sm font-medium text-on-surface truncate">{name}</span>
          {sanctioned && (
            <span className="px-1.5 py-0 rounded text-[10px] font-bold uppercase tracking-wider bg-error/15 text-error border border-error/30 whitespace-nowrap">
              {sanction_programs[0] || 'Sanctioned'}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[11px] text-outline">
          {primary_affiliation && (
            <span className="truncate max-w-[60%]">{primary_affiliation}</span>
          )}
          {country && <span className="font-mono">{country.toUpperCase()}</span>}
          <span className="ml-auto flex gap-1 flex-shrink-0">
            {sources.map((s) => (
              <span
                key={s}
                className="px-1.5 py-0 rounded text-[9px] font-semibold uppercase tracking-wider bg-surface-container-lowest text-outline border border-outline-variant/20"
                title={s}
              >
                {s === 'opensanctions' ? 'OS' : s === 'opencorporates' ? 'OC' : s.slice(0, 2)}
              </span>
            ))}
          </span>
        </div>
      </div>
    </button>
  )
}
