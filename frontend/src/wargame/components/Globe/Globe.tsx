
/**
 * Globe.tsx — Deck.gl hero globe.
 *
 * Behavior:
 *   - Controlled viewState (so we can auto-rotate and fly-to).
 *   - Controller: left-drag rotates, shift-drag pitches, scroll-wheel zooms.
 *   - Auto-rotate spins the sphere while the user is idle AND no country is
 *     selected. Any viewState change (drag / zoom / fly-to) resets a 4-second
 *     idle timer after which auto-rotate resumes.
 *   - When `selectedCountry` flips to an ISO3 code, we fly the view to its
 *     centroid over ~700 ms so the drawer never covers the country.
 */

import { useRef, useEffect, useState, useCallback, useMemo } from 'react';
import DeckGL from '@deck.gl/react';
import { _GlobeView as GlobeView, FlyToInterpolator } from '@deck.gl/core';
import type { PickingInfo } from '@deck.gl/core';
import type { SimEvent } from '@/lib/types/sim-event';
import { useSimStore } from '@/lib/store/simStore';
import { getCentroid } from '@/lib/geo';
import { useWorldLayers } from './useWorldLayers';
import { PulseArcExtension } from './pulseArcExtension';

// Closer default framing — 0.5 showed a tiny globe; 2 feels demo-ready.
const INITIAL_VIEW_STATE = {
  longitude: 120,
  latitude: 20,
  zoom: 2,
  minZoom: 0.5,
  maxZoom: 5,
};

type ViewState = typeof INITIAL_VIEW_STATE & {
  transitionDuration?: number;
  transitionInterpolator?: FlyToInterpolator;
};

const AUTO_ROTATE_DEG_PER_FRAME = 0.04;
// How long after the last user interaction before auto-rotate resumes.
const IDLE_MS_BEFORE_AUTOROTATE = 4000;

export interface GlobeProps {
  onCountryClick: (iso3: string) => void;
  onEventClick: (event: SimEvent) => void;
}

export function Globe({ onCountryClick, onEventClick }: GlobeProps) {
  const [viewState, setViewState] = useState<ViewState>(INITIAL_VIEW_STATE);

  // Auto-rotate is gated by a ref + timer so it's immune to stale closures.
  // `paused` is true while the user is interacting OR a country is selected.
  const pausedRef = useRef(false);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rafRef = useRef<number | null>(null);

  const selectedCountry = useSimStore((s) => s.selectedCountry);

  const pulseExtension = useMemo(() => new PulseArcExtension(), []);

  const { layers } = useWorldLayers({
    viewMode: 'globe',
    onCountryClick,
    onEventClick,
    pulseExtension,
  });

  // ────────────────────────────────────────────────────────────────────
  // Pause / resume auto-rotate
  // ────────────────────────────────────────────────────────────────────
  const pauseAutoRotate = useCallback(() => {
    pausedRef.current = true;
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    // Only schedule a resume if no country is currently selected.
    if (!useSimStore.getState().selectedCountry) {
      idleTimerRef.current = setTimeout(() => {
        pausedRef.current = false;
      }, IDLE_MS_BEFORE_AUTOROTATE);
    }
  }, []);

  // When a country is selected, hold the pause. When it clears, start the
  // idle timer so rotation resumes after the normal grace period.
  useEffect(() => {
    if (selectedCountry) {
      pausedRef.current = true;
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    } else {
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
      idleTimerRef.current = setTimeout(() => {
        pausedRef.current = false;
      }, IDLE_MS_BEFORE_AUTOROTATE);
    }
  }, [selectedCountry]);

  // ────────────────────────────────────────────────────────────────────
  // Fly-to centroid on country select
  // ────────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!selectedCountry) return;
    const c = getCentroid(selectedCountry);
    if (!c) return;
    setViewState((vs) => ({
      ...vs,
      longitude: c.lon,
      latitude: c.lat,
      zoom: Math.max(vs.zoom, 2.2),
      transitionDuration: 700,
      transitionInterpolator: new FlyToInterpolator({ speed: 1.6 }),
    }));
  }, [selectedCountry]);

  // ────────────────────────────────────────────────────────────────────
  // Auto-rotate RAF loop (mount once, never re-creates)
  // ────────────────────────────────────────────────────────────────────
  useEffect(() => {
    const frame = () => {
      if (!pausedRef.current) {
        setViewState((vs) => ({
          ...vs,
          longitude: ((vs.longitude + AUTO_ROTATE_DEG_PER_FRAME + 540) % 360) - 180,
          // Strip any lingering transition props so auto-rotate advances
          // immediately (no smoothing that fights with the loop).
          transitionDuration: 0,
          transitionInterpolator: undefined,
        }));
      }
      rafRef.current = requestAnimationFrame(frame);
    };
    rafRef.current = requestAnimationFrame(frame);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    };
  }, []);

  // ────────────────────────────────────────────────────────────────────
  // Click handling (node → country, arc → event)
  // ────────────────────────────────────────────────────────────────────
  const handleClick = useCallback(
    (info: PickingInfo) => {
      if (!info.object) return;
      if ('iso3' in (info.object as Record<string, unknown>)) {
        onCountryClick((info.object as { iso3: string }).iso3);
        return;
      }
      if ('event' in (info.object as Record<string, unknown>)) {
        onEventClick((info.object as { event: SimEvent }).event);
      }
    },
    [onCountryClick, onEventClick],
  );

  return (
    <DeckGL
      views={new GlobeView({ id: 'globe' })}
      viewState={viewState}
      onViewStateChange={({ viewState: vs }) => {
        // Any controller-driven change (drag / zoom / pitch) counts as an
        // interaction. Mirror the state and pause auto-rotate; the 4 s idle
        // timer inside `pauseAutoRotate` will re-enable it.
        setViewState(vs as ViewState);
        pauseAutoRotate();
      }}
      layers={layers}
      onClick={handleClick}
      controller={{
        dragPan: true,
        dragRotate: true,
        scrollZoom: { speed: 0.01, smooth: true },
        doubleClickZoom: true,
        touchZoom: true,
        touchRotate: true,
        keyboard: true,
        inertia: 250,
      }}
      style={{ width: '100%', height: '100%' }}
      // Deck.gl 9 narrowed `Parameters` to luma.gl GPU params; clearColor still
      // works at runtime via the WebGL device but is no longer in the typed
      // surface. Cast so the canvas keeps its #061033 background fill.
      parameters={{ clearColor: [0.024, 0.063, 0.2, 1] } as Record<string, unknown>}
    />
  );
}
