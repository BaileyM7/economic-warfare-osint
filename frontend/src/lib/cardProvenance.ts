export type CardProvenance = 'global' | 'watchlist';

const GLOBAL_PREFIXES = ['ofac-', 'csl-'] as const;
const WATCHLIST_PREFIXES = ['yf-', 'gdelt-ent-', 'gdelt-region-'] as const;

export function cardProvenance(itemId: string): CardProvenance | null {
  if (GLOBAL_PREFIXES.some((p) => itemId.startsWith(p))) return 'global';
  if (WATCHLIST_PREFIXES.some((p) => itemId.startsWith(p))) return 'watchlist';
  return null;
}
