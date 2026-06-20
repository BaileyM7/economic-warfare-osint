/**
 * WorldView — renders the Deck.gl 3D globe.
 *
 * The Mapbox-based 2D MapView was removed during the Emissary port (no Mapbox
 * token required). If 2D is needed later, re-add MapView.tsx and reinstate
 * the view-mode toggle in simStore.
 */

import { Globe } from './Globe';
import type { SimEvent } from '@/lib/types/sim-event';

export interface WorldViewProps {
  onCountryClick: (iso3: string) => void;
  onEventClick: (event: SimEvent) => void;
}

export function WorldView({ onCountryClick, onEventClick }: WorldViewProps) {
  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <Globe onCountryClick={onCountryClick} onEventClick={onEventClick} />
    </div>
  );
}
