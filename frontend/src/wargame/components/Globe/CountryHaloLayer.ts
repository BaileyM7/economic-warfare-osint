/**
 * CountryHaloLayer — custom Deck.gl ScatterplotLayer subclass that draws
 * a soft glow halo per country node.
 *
 * The halo radius pulses slightly when the country is active.
 * It renders BELOW the main ScatterplotLayer nodes.
 */

import { ScatterplotLayer } from '@deck.gl/layers';
import type { HaloDatum } from './useGlobeData';

const BASE_RADIUS = 120_000; // meters
const ACTIVE_EXTRA = 80_000; // extra meters when activityLevel > 0

export function makeCountryHaloLayer(
  data: HaloDatum[],
  animOffset: number, // seconds, for pulsing
) {
  return new ScatterplotLayer<HaloDatum>({
    id: 'country-halo',
    data,
    getPosition: (d) => d.coordinates,
    getRadius: (d) => {
      const pulse = d.activityLevel > 0
        ? Math.sin(animOffset * 2 + d.coordinates[0]) * 0.15 + 0.85
        : 1;
      return (BASE_RADIUS + d.activityLevel * ACTIVE_EXTRA) * pulse;
    },
    getFillColor: (d) => {
      const intensity = d.isSelected ? 0.4 : d.activityLevel * 0.25 + 0.08;
      // Cyber-cyan halo by default; selected country gets brighter
      return [91, 201, 255, Math.round(intensity * 255)];
    },
    stroked: false,
    filled: true,
    radiusUnits: 'meters',
    pickable: false,
    updateTriggers: {
      getRadius: [animOffset],
      getFillColor: [animOffset],
    },
  });
}
