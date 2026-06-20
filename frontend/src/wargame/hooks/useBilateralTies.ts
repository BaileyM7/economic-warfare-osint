/**
 * useBilateralTies — derive accumulated sanctions/agreements from the
 * event stream.
 *
 * Mirrors the backend logic in ``src/ai/sim/world.py::apply`` (search for
 * ``rel.active_sanctions`` / ``rel.active_agreements``) but runs locally
 * on the frontend so the flat MapView can show persistent bilateral ties
 * without a new API endpoint.  The derivation is cumulative across every
 * visible event; scrubber-awareness is a follow-up (see plan's "out of
 * scope" section).
 *
 * Returns a stable, memoized ``BilateralTie[]`` keyed by sorted ISO3
 * pair + kind, so ordering from the store doesn't flap the LineLayer.
 */

import { useMemo } from 'react';
import { useSimStore } from '@/lib/store/simStore';

export type BilateralTieKind = 'sanction' | 'agreement';

export interface BilateralTie {
  /** Sorted-lexicographic ISO3 pair — stable key regardless of direction. */
  pair: [string, string];
  /** The direction the instrument runs (from → to), preserved for drawing. */
  fromIso3: string;
  toIso3: string;
  kind: BilateralTieKind;
  /** How many overlapping instruments of this kind — drives line width. */
  count: number;
}

/**
 * Returns every ``(pair, kind)`` tie the current event history implies.
 * The event list comes from ``useSimStore.events`` and is re-derived only
 * when the event count changes — re-running on every render would be
 * wasteful when the store frequently updates unrelated fields.
 */
export function useBilateralTies(): BilateralTie[] {
  const events = useSimStore((s) => s.events);
  const eventCount = events.length;

  return useMemo(() => {
    // key = `${from}->${to}:${kind}` so same-kind repeats increment count;
    // different kinds (same pair) stay as separate ties for colour reasons.
    const ties = new Map<string, BilateralTie>();

    for (const ev of events) {
      if (!ev.target_country) continue;
      const kind = _classify(ev.action_type, ev.domain);
      if (!kind) continue;

      const from = ev.actor_country;
      const to = ev.target_country;
      const pair: [string, string] =
        from < to ? [from, to] : [to, from];
      const key = `${from}->${to}:${kind}`;
      const existing = ties.get(key);
      if (existing) {
        existing.count += 1;
      } else {
        ties.set(key, { pair, fromIso3: from, toIso3: to, kind, count: 1 });
      }
    }

    return [...ties.values()];
    // `events` identity changes every time the store merges a new event,
    // but we only care about the count for re-derivation.  Ignore the
    // exhaustive-deps rule deliberately — see backend mirror comment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventCount]);
}

/**
 * Classify a sim event as contributing to a sanctions or agreements tie.
 * Mirrors world.py:320-330; keep the substring rules aligned or we'll
 * diverge from what the backend considers a tie.
 */
function _classify(
  actionType: string,
  domain: string,
): BilateralTieKind | null {
  const at = actionType.toLowerCase();
  if (domain === 'economic' && at.includes('sanction')) return 'sanction';
  if (domain === 'diplomatic' && at.includes('agreement')) return 'agreement';
  return null;
}
