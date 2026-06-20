
/**
 * SelectedCountryList — the editable list of top-5 countries shown in the
 * free-form confirmation card. User can remove unprotected chips and add
 * any country from the 35-country pool via a search dropdown.
 *
 * Protected ISOs (actor + target of seed events) cannot be removed
 * because the simulator needs them to execute the seed action at turn 0.
 */

import { useMemo, useState } from 'react';
import { Plus, Search } from 'lucide-react';
import { CountryPickChip } from './CountryPickChip';
import type { SelectedCountry } from '@/lib/types/scenario';
import { ALL_POOL_ISO3, getCountryName } from '@/lib/geo';

export interface SelectedCountryListProps {
  countries: SelectedCountry[];
  /** ISOs that cannot be removed (usually actor + target of seed events). */
  protectedIsos?: string[];
  onChange: (countries: SelectedCountry[]) => void;
  maxCountries?: number;
}

export function SelectedCountryList({
  countries,
  protectedIsos = [],
  onChange,
  maxCountries = 5,
}: SelectedCountryListProps) {
  const [addOpen, setAddOpen] = useState(false);
  const [query, setQuery] = useState('');

  const existingIsos = useMemo(() => new Set(countries.map((c) => c.iso3)), [countries]);

  const candidates = useMemo(() => {
    const q = query.trim().toUpperCase();
    return ALL_POOL_ISO3.filter((iso) => {
      if (existingIsos.has(iso)) return false;
      if (!q) return true;
      return iso.includes(q) || getCountryName(iso).toUpperCase().includes(q);
    }).slice(0, 8);
  }, [query, existingIsos]);

  const handleRemove = (iso3: string) => {
    onChange(countries.filter((c) => c.iso3 !== iso3));
  };

  const handleAdd = (iso3: string) => {
    // User-added countries get a neutral 0.5 score and a human-obvious rationale
    // so downstream consumers (tooltip, telemetry) can distinguish manual picks
    // from backend-scored entries.
    onChange([
      ...countries,
      { iso3, relevance_score: 0.5, rationale: 'Manually added by user.' },
    ]);
    setQuery('');
    setAddOpen(false);
  };

  const canAdd = countries.length < maxCountries;
  const protectedSet = new Set(protectedIsos);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-widest text-on-surface-variant">
          Participants · {countries.length}/{maxCountries}
        </span>
        {canAdd && !addOpen && (
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="font-mono text-[10px] uppercase tracking-widest text-cyber/80 hover:text-cyber inline-flex items-center gap-1"
            aria-label="Add country"
          >
            <Plus size={10} /> Add
          </button>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5" data-testid="selected-country-list">
        {countries.map((c) => (
          <CountryPickChip
            key={c.iso3}
            iso3={c.iso3}
            score={c.relevance_score}
            rationale={c.rationale}
            onRemove={() => handleRemove(c.iso3)}
            isProtected={protectedSet.has(c.iso3)}
          />
        ))}
      </div>

      {addOpen && canAdd && (
        <div className="space-y-1.5 border border-dashed border-cyber/40 p-2 bg-surface-container-lowest">
          <div className="flex items-center gap-1.5 border-b border-outline-variant/40 pb-1">
            <Search size={12} className="text-on-surface-variant" />
            <input
              type="text"
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search ISO3 or country name…"
              className="flex-1 bg-transparent font-mono text-[11px] text-on-surface focus:outline-none placeholder:text-on-surface-variant/50"
            />
            <button
              type="button"
              onClick={() => {
                setAddOpen(false);
                setQuery('');
              }}
              className="font-mono text-[10px] uppercase text-on-surface-variant hover:text-on-surface"
            >
              Cancel
            </button>
          </div>
          {candidates.length === 0 ? (
            <p className="font-mono text-[10px] text-on-surface-variant py-1">
              No matches.
            </p>
          ) : (
            <ul className="max-h-40 overflow-y-auto">
              {candidates.map((iso) => (
                <li key={iso}>
                  <button
                    type="button"
                    onClick={() => handleAdd(iso)}
                    className={[
                      'w-full text-left px-1.5 py-1 font-mono text-[11px]',
                      'text-on-surface hover:bg-cyber/10 transition-colors',
                      'flex items-center gap-2',
                    ].join(' ')}
                  >
                    <span className="font-bold text-cyber/80 w-8">{iso}</span>
                    <span className="text-on-surface-variant">{getCountryName(iso)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
