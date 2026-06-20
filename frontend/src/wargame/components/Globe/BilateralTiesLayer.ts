/**
 * BilateralTiesLayer — Deck.gl LineLayer factory for persistent
 * sanctions / agreements between countries.
 *
 * Called only from the flat MapView layer stack.  On the globe these
 * lines tangle with the event arcs and don't read cleanly; on flat
 * Mercator they sit behind the arcs and give the user a quick read of
 * "which bilateral relationships matter here even without a live event."
 *
 * Visual language:
 *   - Sanctions  → orange (#efb16a), thicker per overlap
 *   - Agreements → info light-blue (#a9d8fb) to avoid colliding with the
 *     economic-arc orange palette
 *   - Both drawn as plain solid lines — no pulse.  The motion cue is
 *     reserved for event arcs so static ties don't compete for attention.
 */

import { LineLayer } from '@deck.gl/layers';
import type { BilateralTie } from '@/hooks/useBilateralTies';
import { getCentroid } from '@/lib/geo';

/** RGBA colours — tuned to sit under the event-arc palette without clashing. */
const TIE_COLOR_SANCTION: [number, number, number, number] = [239, 177, 106, 130];
const TIE_COLOR_AGREEMENT: [number, number, number, number] = [169, 216, 251, 130];

interface TieDatum {
  id: string;
  sourcePosition: [number, number];
  targetPosition: [number, number];
  color: [number, number, number, number];
  width: number;
}

function _tieToDatum(tie: BilateralTie): TieDatum | null {
  const src = getCentroid(tie.fromIso3);
  const tgt = getCentroid(tie.toIso3);
  if (!src || !tgt) return null;
  return {
    id: `${tie.pair[0]}-${tie.pair[1]}:${tie.kind}`,
    sourcePosition: [src.lon, src.lat],
    targetPosition: [tgt.lon, tgt.lat],
    color: tie.kind === 'sanction' ? TIE_COLOR_SANCTION : TIE_COLOR_AGREEMENT,
    // One instrument = 1.5px, each additional overlap adds 0.75px.  Capped
    // at 6px so a country with many sanctions doesn't drown the arcs.
    width: Math.min(6, 1.5 + (tie.count - 1) * 0.75),
  };
}

/**
 * Build the tie LineLayer(s).  Returns an array (not a single layer) so
 * callers can spread it into a layer list without special-casing nulls,
 * and so we can split sanctions / agreements into separate layers if a
 * future tweak needs to toggle them independently.
 */
export function makeBilateralTiesLayers(ties: BilateralTie[]): LineLayer<TieDatum>[] {
  const data = ties
    .map(_tieToDatum)
    .filter((d): d is TieDatum => d !== null);
  if (data.length === 0) return [];

  return [
    new LineLayer<TieDatum>({
      id: 'bilateral-ties',
      data,
      getSourcePosition: (d) => d.sourcePosition,
      getTargetPosition: (d) => d.targetPosition,
      getColor: (d) => d.color,
      getWidth: (d) => d.width,
      widthUnits: 'pixels',
      // Ties are background context — do NOT make them pickable; event
      // arcs must remain the only clickable lines in this layer family.
      pickable: false,
      // Deck.gl default draws lines in pixel space without projection
      // curvature, which is what we want on flat Mercator.
    }),
  ];
}
