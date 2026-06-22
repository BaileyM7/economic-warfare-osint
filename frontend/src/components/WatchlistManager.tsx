import { useEffect, useMemo, useState } from 'react';
import {
  fetchWatchlist,
  addWatchlistItem,
  deleteWatchlistItem,
  fetchWatchlistSuggestions,
  resolveWatchlistEntity,
  type WatchlistCategory,
  type WatchlistEntityKind,
  type WatchlistItem,
  type WatchlistResolveResponse,
  type WatchlistSuggestion,
} from '../api';

const MAX_ACTIVE = 10;

const CATEGORY_LABELS: Record<WatchlistCategory, string> = {
  company_sanctions: 'Company Sanctions',
  people_sanctions: 'People Sanctions',
  markets: 'Markets',
};

const CATEGORY_ICONS: Record<WatchlistCategory, string> = {
  company_sanctions: 'business',
  people_sanctions: 'person',
  markets: 'show_chart',
};

const KIND_LABELS: Record<WatchlistEntityKind, string> = {
  ticker: 'Ticker',
  gdelt_query: 'News query',
  gdelt_region: 'Region/topic',
  sanctions_keyword: 'CSL keyword',
};

interface Props {
  /** Notifies the parent so it can trigger a refresh after watch-list changes. */
  onChanged?: () => void;
}

function suggestionKey(s: WatchlistSuggestion): string {
  return `${s.entity_kind}::${s.query}::${s.category}`;
}

interface CollapsibleSectionProps {
  title: string;
  icon: string;
  defaultOpen?: boolean;
  meta?: React.ReactNode;
  children: React.ReactNode;
}

function CollapsibleSection({
  title,
  icon,
  defaultOpen = true,
  meta,
  children,
}: CollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="bg-surface-container-low border border-outline-variant/10 rounded-lg">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 hover:bg-surface-container transition-colors rounded-lg"
      >
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-base text-primary">{icon}</span>
          <span className="text-sm font-semibold uppercase tracking-wider text-on-surface">
            {title}
          </span>
          {meta}
        </div>
        <span className="material-symbols-outlined text-on-surface-variant">
          {open ? 'expand_less' : 'expand_more'}
        </span>
      </button>
      {open && <div className="px-4 pb-4">{children}</div>}
    </section>
  );
}

export default function WatchlistManager({ onChanged }: Props) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [suggestions, setSuggestions] = useState<WatchlistSuggestion[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Multi-select state for the suggestions tile.
  const [selectedSuggestions, setSelectedSuggestions] = useState<Set<string>>(new Set());
  const [committing, setCommitting] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [wl, sug] = await Promise.all([fetchWatchlist(), fetchWatchlistSuggestions()]);
      setItems(wl.items);
      setSuggestions(sug.suggestions);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const grouped = useMemo<Record<WatchlistCategory, WatchlistItem[]>>(() => {
    const out: Record<WatchlistCategory, WatchlistItem[]> = {
      company_sanctions: [],
      people_sanctions: [],
      markets: [],
    };
    for (const it of items) {
      if (out[it.category as WatchlistCategory]) out[it.category as WatchlistCategory].push(it);
    }
    return out;
  }, [items]);

  const activeKeys = useMemo(() => {
    const set = new Set<string>();
    for (const it of items) {
      if (it.active) set.add(`${it.entity_kind}::${it.query}::${it.category}`);
    }
    return set;
  }, [items]);

  const activeCount = items.filter((i) => i.active).length;
  const remaining = Math.max(0, MAX_ACTIVE - activeCount);
  const atCap = activeCount >= MAX_ACTIVE;

  // Selection helpers
  function toggleSuggestion(key: string) {
    setSelectedSuggestions((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function clearSelection() {
    setSelectedSuggestions(new Set());
  }

  const overCapBy = Math.max(0, activeCount + selectedSuggestions.size - MAX_ACTIVE);

  async function handleCommitSelection() {
    if (selectedSuggestions.size === 0 || overCapBy > 0) return;
    setCommitting(true);
    setError(null);
    const toAdd = suggestions.filter((s) => selectedSuggestions.has(suggestionKey(s)));
    const failures: string[] = [];
    for (const s of toAdd) {
      try {
        const created = await addWatchlistItem({
          label: s.label,
          query: s.query,
          entity_kind: s.entity_kind,
          category: s.category,
        });
        setItems((prev) => [...prev.filter((p) => p.id !== created.id), created]);
      } catch (err) {
        failures.push(`${s.label}: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    setCommitting(false);
    clearSelection();
    if (failures.length) {
      setError(`Some items failed:\n- ${failures.join('\n- ')}`);
    }
    onChanged?.();
  }

  async function handleDelete(id: string) {
    setBusyId(id);
    try {
      await deleteWatchlistItem(id);
      setItems((prev) => prev.filter((p) => p.id !== id));
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  async function handleAddOne(payload: {
    label: string;
    query: string;
    entity_kind: WatchlistEntityKind;
    category: WatchlistCategory;
  }) {
    try {
      const created = await addWatchlistItem(payload);
      setItems((prev) => [...prev.filter((p) => p.id !== created.id), created]);
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="mb-6 space-y-4">
      {error && (
        <div className="bg-error/10 border border-error/30 text-error rounded-md px-3 py-2 text-xs whitespace-pre-line">
          {error}
        </div>
      )}

      {atCap && (
        <div className="bg-tertiary/10 border border-tertiary/30 text-tertiary rounded-md px-3 py-2 text-xs">
          You've reached the {MAX_ACTIVE}-item limit. Remove an item below to free up a slot.
        </div>
      )}

      <CollapsibleSection
        title="Manage Watch-list"
        icon="tune"
        defaultOpen={true}
        meta={
          <span className={`text-[11px] font-mono ${atCap ? 'text-error' : 'text-outline'}`}>
            {activeCount} / {MAX_ACTIVE}
          </span>
        }
      >
        {loading ? (
          <div className="text-on-surface-variant text-sm">Loading…</div>
        ) : items.length === 0 ? (
          <div className="text-xs text-on-surface-variant italic">
            Your watch-list is empty. Add an entity below or pick from suggestions.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {(Object.keys(CATEGORY_LABELS) as WatchlistCategory[]).map((cat) => (
              <div key={cat} className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2 mb-1">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-on-surface flex items-center gap-1">
                    <span className="material-symbols-outlined text-sm text-primary">
                      {CATEGORY_ICONS[cat]}
                    </span>
                    {CATEGORY_LABELS[cat]}
                  </h3>
                  <span className="text-[10px] text-outline">{grouped[cat].length}</span>
                </div>

                <ul className="space-y-1">
                  {grouped[cat].map((it) => (
                    <li
                      key={it.id}
                      className="flex items-start justify-between gap-2 text-xs bg-surface-container rounded px-2 py-1.5"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="font-semibold text-on-surface truncate">{it.label}</div>
                        <div className="text-[10px] text-outline truncate">
                          {KIND_LABELS[it.entity_kind]} · {it.query}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleDelete(it.id)}
                        disabled={busyId === it.id}
                        className="text-outline hover:text-error transition-colors disabled:opacity-40"
                        title="Remove"
                      >
                        <span className="material-symbols-outlined text-base">delete</span>
                      </button>
                    </li>
                  ))}
                  {grouped[cat].length === 0 && (
                    <li className="text-[11px] text-outline italic">none</li>
                  )}
                </ul>
              </div>
            ))}
          </div>
        )}
      </CollapsibleSection>

      <CollapsibleSection title="Suggested starter items" icon="auto_awesome" defaultOpen={false}>
        <SuggestionsPicker
          suggestions={suggestions}
          activeKeys={activeKeys}
          selected={selectedSuggestions}
          onToggle={toggleSuggestion}
          onCommit={handleCommitSelection}
          onClear={clearSelection}
          committing={committing}
          remaining={remaining}
          overCapBy={overCapBy}
        />
      </CollapsibleSection>

      <CollapsibleSection title="Track an entity by name" icon="search" defaultOpen={true}>
        <EntityResolveBox onConfirm={handleAddOne} disabled={atCap} remaining={remaining} />
      </CollapsibleSection>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Entity resolve box
// ---------------------------------------------------------------------------

interface ResolveBoxProps {
  onConfirm: (payload: {
    label: string;
    query: string;
    entity_kind: WatchlistEntityKind;
    category: WatchlistCategory;
  }) => Promise<void>;
  disabled: boolean;
  remaining: number;
}

function EntityResolveBox({ onConfirm, disabled, remaining }: ResolveBoxProps) {
  const [name, setName] = useState('');
  const [resolving, setResolving] = useState(false);
  const [result, setResult] = useState<WatchlistResolveResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  async function handleResolve(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setResolving(true);
    setError(null);
    setResult(null);
    try {
      const r = await resolveWatchlistEntity(name.trim());
      setResult(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setResolving(false);
    }
  }

  async function handleConfirm() {
    if (!result) return;
    setConfirming(true);
    try {
      await onConfirm(result.suggestion);
      setName('');
      setResult(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setConfirming(false);
    }
  }

  return (
    <div>
      <p className="text-xs text-on-surface-variant mb-3">
        Type a company, person, region, or ticker. We'll cross-check OFAC, Trade.gov, market data,
        and news to confirm what you mean before adding it.
      </p>
      <form onSubmit={handleResolve} className="flex items-center gap-2 mb-3">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder='e.g. "Huawei", "Lockheed Martin", "Strait of Hormuz", "LMT"'
          disabled={resolving || disabled}
          className="flex-1 bg-surface-container-low border border-outline-variant/20 rounded px-3 py-1.5 text-sm disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={resolving || !name.trim() || disabled}
          className="text-[11px] font-semibold text-on-primary bg-primary px-3 py-1.5 rounded disabled:opacity-50 flex items-center gap-1"
        >
          {resolving && (
            <span className="material-symbols-outlined text-xs animate-spin">
              progress_activity
            </span>
          )}
          {resolving ? 'Looking up…' : 'Look up'}
        </button>
      </form>

      {disabled && (
        <p className="text-[11px] text-tertiary italic">
          You're at the watch-list limit ({remaining} slots free). Remove items above to add more.
        </p>
      )}

      {error && (
        <div className="bg-error/10 border border-error/30 text-error rounded-md px-3 py-2 text-xs">
          {error}
        </div>
      )}

      {result && (
        <ResolveResultCard
          result={result}
          confirming={confirming}
          onConfirm={handleConfirm}
          onCancel={() => setResult(null)}
        />
      )}
    </div>
  );
}

interface ResolveResultCardProps {
  result: WatchlistResolveResponse;
  confirming: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

function ResolveResultCard({ result, confirming, onConfirm, onCancel }: ResolveResultCardProps) {
  const { suggestion, evidence, resolved, confidence, hint } = result;
  const confChip =
    confidence === 'high'
      ? 'bg-secondary/15 text-secondary border-secondary/30'
      : confidence === 'medium'
        ? 'bg-primary/15 text-primary border-primary/30'
        : 'bg-tertiary/15 text-tertiary border-tertiary/30';

  return (
    <div className="bg-surface-container-low border border-outline-variant/15 rounded-md p-3 mt-2 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-on-surface">{suggestion.label}</div>
          <div className="text-[11px] text-outline">
            Will track as <span className="font-mono">{KIND_LABELS[suggestion.entity_kind]}</span>{' '}
            in <span className="font-mono">{CATEGORY_LABELS[suggestion.category]}</span>
          </div>
        </div>
        {resolved && confidence ? (
          <span
            className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border ${confChip}`}
          >
            {confidence} confidence
          </span>
        ) : (
          <span className="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border bg-error/10 text-error border-error/30">
            unconfirmed
          </span>
        )}
      </div>

      {hint && <div className="text-[11px] text-on-surface-variant italic">{hint}</div>}

      {evidence.length > 0 && (
        <ul className="text-[11px] text-on-surface-variant space-y-0.5">
          {evidence.map((ev, i) => (
            <li key={i} className="flex items-start gap-1.5">
              <span className="material-symbols-outlined text-xs mt-0.5">verified</span>
              <span>{evidenceLabel(ev)}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2 pt-1">
        <button
          type="button"
          onClick={onConfirm}
          disabled={confirming}
          className="text-[11px] font-semibold text-on-primary bg-primary px-3 py-1.5 rounded disabled:opacity-50 flex items-center gap-1"
        >
          {confirming && (
            <span className="material-symbols-outlined text-xs animate-spin">
              progress_activity
            </span>
          )}
          {confirming ? 'Adding…' : `Track ${suggestion.label}`}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-[11px] text-outline hover:text-on-surface px-2 py-1.5"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function evidenceLabel(ev: { kind: string; [k: string]: unknown }): string {
  switch (ev.kind) {
    case 'yfinance': {
      const sector = (ev.sector as string | undefined) ?? '';
      const ticker = (ev.ticker as string | undefined) ?? '';
      return `Yahoo Finance: ${ticker}${sector ? ` · ${sector}` : ''}`;
    }
    case 'ofac_sdn': {
      const type = (ev.type as string | undefined) ?? '';
      const matchedName = (ev.name as string | undefined) ?? '';
      const strong = ev.strong_match === true;
      // Strong = a real sanctions hit; show the canonical name. A weak/fuzzy hit
      // was discarded, so render a calm "no direct match" instead of the
      // confusing partial name + score, which read to users as a false alarm.
      return strong ? `OFAC SDN match (${type}): ${matchedName}` : 'OFAC SDN — no direct match';
    }
    case 'csl': {
      const source = (ev.source as string | undefined) ?? 'CSL';
      return `Trade.gov ${source} match`;
    }
    case 'gdelt': {
      const articles = (ev.articles as { date: string | null }[] | undefined) ?? [];
      return `GDELT: ${articles.length} recent article${articles.length === 1 ? '' : 's'}`;
    }
    case 'sayari': {
      // Surface Sayari's resolution detail (type · country · risk flags) instead
      // of a bare "sayari" — this is the richer entity intelligence the analyst
      // is meant to see (Mike feedback #2).
      const type = (ev.type as string | undefined) ?? '';
      const country = (ev.country as string | undefined) ?? '';
      const bits = [type, country ? countryName(country) : ''].filter(Boolean);
      if (ev.sanctioned === true) bits.push('sanctioned');
      if (ev.pep === true) bits.push('PEP');
      return `Sayari Graph${bits.length ? ` — ${bits.join(' · ')}` : ' resolved'}`;
    }
    default:
      return ev.kind;
  }
}

// Sayari returns ISO alpha-3 country codes; map the common ones to readable
// names (falls back to the raw code for anything not listed).
const COUNTRY_NAMES: Record<string, string> = {
  IND: 'India',
  CHN: 'China',
  HKG: 'Hong Kong',
  ARE: 'UAE',
  CYM: 'Cayman Islands',
  VGB: 'British Virgin Islands',
  RUS: 'Russia',
  USA: 'United States',
  GBR: 'United Kingdom',
  SGP: 'Singapore',
  IRN: 'Iran',
  PRK: 'North Korea',
  TUR: 'Turkey',
  DEU: 'Germany',
  FRA: 'France',
  NLD: 'Netherlands',
  CHE: 'Switzerland',
  JPN: 'Japan',
  KOR: 'South Korea',
  TWN: 'Taiwan',
  PAN: 'Panama',
  SAU: 'Saudi Arabia',
  MYS: 'Malaysia',
  VNM: 'Vietnam',
};

function countryName(code: string): string {
  return COUNTRY_NAMES[code.toUpperCase()] ?? code;
}

// ---------------------------------------------------------------------------
// Suggestions tile with multi-select + confirm bar
// ---------------------------------------------------------------------------

interface PickerProps {
  suggestions: WatchlistSuggestion[];
  activeKeys: Set<string>;
  selected: Set<string>;
  onToggle: (key: string) => void;
  onCommit: () => void;
  onClear: () => void;
  committing: boolean;
  remaining: number;
  overCapBy: number;
}

function SuggestionsPicker({
  suggestions,
  activeKeys,
  selected,
  onToggle,
  onCommit,
  onClear,
  committing,
  remaining,
  overCapBy,
}: PickerProps) {
  const grouped = useMemo<Record<WatchlistCategory, WatchlistSuggestion[]>>(() => {
    const out: Record<WatchlistCategory, WatchlistSuggestion[]> = {
      company_sanctions: [],
      people_sanctions: [],
      markets: [],
    };
    for (const s of suggestions) {
      if (out[s.category]) out[s.category].push(s);
    }
    return out;
  }, [suggestions]);

  return (
    <div>
      <p className="text-xs text-on-surface-variant mb-3">
        Pick one or more starter entities below, then confirm to add them all at once.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {(Object.keys(CATEGORY_LABELS) as WatchlistCategory[]).map((cat) => (
          <div key={cat} className="flex flex-col gap-1.5">
            <h4 className="text-[10px] uppercase tracking-widest text-outline flex items-center gap-1">
              <span className="material-symbols-outlined text-xs">{CATEGORY_ICONS[cat]}</span>
              {CATEGORY_LABELS[cat]}
            </h4>
            <ul className="space-y-1">
              {grouped[cat].map((s) => {
                const key = suggestionKey(s);
                const isActive = activeKeys.has(key);
                const isSelected = selected.has(key);
                return (
                  <li key={key}>
                    <button
                      type="button"
                      disabled={isActive}
                      onClick={() => onToggle(key)}
                      className={`w-full text-left text-[11px] rounded px-2 py-1.5 border transition-colors flex items-start gap-2 ${
                        isActive
                          ? 'bg-secondary/5 text-outline border-outline/20 cursor-default'
                          : isSelected
                            ? 'bg-primary/15 text-on-surface border-primary/40'
                            : 'bg-surface-container-low text-on-surface border-outline-variant/15 hover:border-primary/30'
                      }`}
                      title={isActive ? 'Already on your watch-list' : undefined}
                    >
                      <span
                        className={`material-symbols-outlined text-base mt-[1px] ${
                          isActive ? 'text-secondary' : isSelected ? 'text-primary' : 'text-outline'
                        }`}
                      >
                        {isActive
                          ? 'check_circle'
                          : isSelected
                            ? 'check_box'
                            : 'check_box_outline_blank'}
                      </span>
                      <span className="flex-1">
                        <span className="font-semibold block leading-tight">{s.label}</span>
                        <span className="block text-[10px] text-outline">
                          {KIND_LABELS[s.entity_kind]}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
              {grouped[cat].length === 0 && (
                <li className="text-[10px] text-outline italic">no suggestions</li>
              )}
            </ul>
          </div>
        ))}
      </div>

      {selected.size > 0 && (
        <div className="mt-3 pt-3 border-t border-outline-variant/15 flex items-center justify-between gap-3">
          <div className="text-xs text-on-surface-variant">
            <span className="font-semibold text-on-surface">{selected.size} selected</span>
            {' · '}
            <span className={overCapBy > 0 ? 'text-error' : 'text-outline'}>
              {overCapBy > 0
                ? `${overCapBy} over the ${MAX_ACTIVE}-item cap`
                : `${remaining - selected.size} slot${remaining - selected.size === 1 ? '' : 's'} will remain`}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClear}
              disabled={committing}
              className="text-[11px] text-outline hover:text-on-surface px-2 py-1 rounded"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={onCommit}
              disabled={committing || overCapBy > 0}
              className="text-[11px] font-semibold text-on-primary bg-primary px-3 py-1.5 rounded disabled:opacity-50 flex items-center gap-1"
            >
              {committing && (
                <span className="material-symbols-outlined text-xs animate-spin">
                  progress_activity
                </span>
              )}
              {committing ? 'Adding…' : `Confirm (${selected.size})`}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
