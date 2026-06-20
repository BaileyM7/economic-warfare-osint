import { useState } from 'react';
import {
  addWatchlistItem,
  type RiskFeedItem,
  type WatchlistCategory,
  type WatchlistEntityKind,
} from '../api';
import { cardProvenance } from '../lib/cardProvenance';

/** Map a feed item back to the watchlist shape it would have had if the user
 *  had typed it themselves. Returns null when the card type doesn't make
 *  sense as a watch-list entry (e.g. raw OFAC SDN cards — those are part of
 *  the global ranked surface, not user-curated entities to track). */
function deriveWatchlistFromCard(item: RiskFeedItem): {
  label: string;
  query: string;
  entity_kind: WatchlistEntityKind;
  category: WatchlistCategory;
} | null {
  const id = item.id || '';
  const cat = item.category as WatchlistCategory;

  // Only watchlist-provenance cards map to a watch-list entry; global
  // cards (OFAC/CSL) come from the unconditional sanctions surface.
  if (cardProvenance(id) !== 'watchlist') return null;

  if (id.startsWith('yf-')) {
    // id shape: yf-<TICKER>-<YYYYMMDD>
    const parts = id.split('-');
    const ticker = parts.length >= 2 ? parts[1] : '';
    if (!ticker) return null;
    return {
      label: item.entity || ticker,
      query: ticker,
      entity_kind: 'ticker',
      category: 'markets',
    };
  }

  if (id.startsWith('gdelt-region-')) {
    return {
      label: item.entity,
      query: item.entity,
      entity_kind: 'gdelt_region',
      category: 'markets',
    };
  }

  if (id.startsWith('gdelt-ent-')) {
    return {
      label: item.entity,
      query: item.entity,
      entity_kind: 'gdelt_query',
      category: cat,
    };
  }

  return null;
}

interface Props {
  item: RiskFeedItem;
  onAdded?: () => void;
}

export default function AddToWatchlistButton({ item, onAdded }: Props) {
  const [state, setState] = useState<'idle' | 'busy' | 'added' | 'error'>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const derived = deriveWatchlistFromCard(item);
  if (!derived) return null; // hide on OFAC/CSL cards

  async function handleClick() {
    if (!derived) return;
    setState('busy');
    setErrorMsg(null);
    try {
      await addWatchlistItem(derived);
      setState('added');
      onAdded?.();
      setTimeout(() => setState('idle'), 2500);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
      setState('error');
      setTimeout(() => setState('idle'), 3000);
    }
  }

  let label = '+ Watchlist';
  let icon = 'bookmark_add';
  let extraClass = '';
  if (state === 'busy') {
    label = 'Adding…';
    icon = 'progress_activity';
  } else if (state === 'added') {
    label = 'Added';
    icon = 'check_circle';
    extraClass = 'text-secondary border-secondary/40';
  } else if (state === 'error') {
    // Cap-reached errors come through as 400s with the limit message in `detail`.
    // Surface a shorter inline label and rely on the title (tooltip) for the full
    // backend message.
    label = errorMsg && /limit reached/i.test(errorMsg) ? 'Cap reached' : 'Failed';
    icon = 'error';
    extraClass = 'text-error border-error/40';
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={state === 'busy' || state === 'added'}
      title={errorMsg ?? 'Add this entity to your watch-list'}
      className={`text-[11px] text-outline hover:bg-outline/10 px-2 py-1 rounded border border-outline/30 transition-colors disabled:cursor-default flex items-center gap-1 ${extraClass}`}
    >
      <span
        className={`material-symbols-outlined text-xs ${state === 'busy' ? 'animate-spin' : ''}`}
      >
        {icon}
      </span>
      {label}
    </button>
  );
}
