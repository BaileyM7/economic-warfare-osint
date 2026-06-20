/**
 * useGlobeData — derives Deck.gl layer data from the Zustand store.
 *
 * Produces:
 *   – scatterData: country scatter points with activity radius
 *   – arcData: recent sim events as arcs with domain color + age fade
 *   – haloData: country halo points for CountryHaloLayer
 */

import { useMemo, useRef } from 'react';
import { useSimStore } from '@/lib/store/simStore';
import { getDomainRgba } from '@/lib/domain';
import { ALL_POOL_ISO3, getCentroid } from '@/lib/geo';
import type { SimEvent } from '@/lib/types/sim-event';

// Every country in the canonical pool (src/shared/seeds/countries.yaml) gets a
// dot + label + halo so the user can click any of them to see the country's
// profile in the AgentDrawer. Previously only the 10-country Taiwan slice was
// rendered, which hid the expanded pool used by the free-form scenario mode.
const GLOBE_COUNTRIES = ALL_POOL_ISO3;

// Arc age fade — turn-relative, not wall-clock.  Wall-clock fading means a
// 3-minute sim run (pacing=2s × 7 agents × 3 turns) has T1 arcs already
// half-faded by the time T3 renders, and replaying a completed sim shows
// every arc faded because the timestamps are old.  Using turn distance
// instead means the "newest" turn always pops and older turns recede by
// a constant amount per turn — behavior matches what the user expects.
const ARC_FADE_TURNS = 4; // arcs from turn N-4 fade to minimum alpha
const ARC_MIN_ALPHA_RATIO = 0.35; // floor so old arcs stay visible, just dimmer

// Per-pair vertical stagger. Each additional arc between the same
// (actor, target) pair bulges this much higher than the previous, so same-pair
// arcs fan apart instead of rendering on top of each other.
const PAIR_HEIGHT_STEP = 0.35;

// Soft cap on the birth-time map. With the 40-event visible window this stays
// small in practice; above the cap we prune ids no longer present in the
// current visible set so long-running sessions don't grow unbounded.
const BIRTH_MAP_PRUNE_AT = 500;

export interface ScatterPoint {
  iso3: string;
  coordinates: [number, number]; // [lon, lat]
  activityLevel: number;         // 0..1, drives radius
  isSelected: boolean;
}

export interface ArcDatum {
  id: string;
  sourcePosition: [number, number]; // [lon, lat]
  targetPosition: [number, number];
  color: [number, number, number, number]; // RGBA with age-fade alpha
  width: number;
  /** Seconds since page load when this arc was first seen — drives origin→target grow animation. */
  birthTime: number;
  /** Per-pair index (0, 1, 2…) used to stagger heights of same-pair arcs. */
  pairIndex: number;
  /** Multiplier for ArcLayer getHeight; same-pair arcs get taller bulges. */
  height: number;
  event: SimEvent;
}

export interface HaloDatum {
  iso3: string;
  coordinates: [number, number];
  activityLevel: number;
  isSelected: boolean;
}

export function useGlobeData() {
  const selectedCountry = useSimStore((s) => s.selectedCountry);
  const getVisibleEvents = useSimStore((s) => s.visibleEvents);

  const visibleEvents = getVisibleEvents();

  // Stable per-event birth time so scrubbing an arc out of view and back in
  // does not restart its grow animation. Keyed by event.id; value is seconds
  // since page load (performance.now/1000).
  const birthTimesRef = useRef<Map<string, number>>(new Map());

  // Compute per-country activity level (recent events in last 5 turns)
  const activityMap = useMemo(() => {
    const map: Record<string, number> = {};
    const maxTurn = visibleEvents.reduce((m, e) => Math.max(m, e.turn), 0);
    for (const ev of visibleEvents) {
      const age = maxTurn - ev.turn;
      if (age > 5) continue;
      const boost = 1 - age / 5;
      map[ev.actor_country] = (map[ev.actor_country] ?? 0) + boost * 0.2;
      if (ev.target_country) {
        map[ev.target_country] =
          (map[ev.target_country] ?? 0) + boost * 0.1;
      }
    }
    // Clamp to [0, 1]
    for (const k of Object.keys(map)) {
      map[k] = Math.min(1, map[k]);
    }
    return map;
  }, [visibleEvents]);

  const scatterData: ScatterPoint[] = useMemo(
    () =>
      GLOBE_COUNTRIES.map((iso3): ScatterPoint | null => {
        const centroid = getCentroid(iso3);
        if (!centroid) return null;
        return {
          iso3,
          coordinates: [centroid.lon, centroid.lat],
          activityLevel: activityMap[iso3] ?? 0,
          isSelected: selectedCountry === iso3,
        };
      }).filter((x): x is ScatterPoint => x !== null),
    [activityMap, selectedCountry],
  );

  const arcData: ArcDatum[] = useMemo(() => {
    // Only show the last 40 events to keep WebGL buffer reasonable
    const recent = visibleEvents.slice(-40);
    // Age-fade anchor: the highest turn number present in the visible set.
    // Live-stream case → equals the current turn.  Replay case → equals
    // the scrubber's turn.  Either way the newest shown arcs are at full
    // alpha and older ones recede by constant per-turn steps.
    let newestTurn = 0;
    for (const ev of recent) if (ev.turn > newestTurn) newestTurn = ev.turn;

    const births = birthTimesRef.current;
    const nowSec = performance.now() / 1000;

    // Count how many arcs already exist per (actor → target) pair so later
    // arcs in the same pair get a higher pairIndex (and taller bulge).
    const pairCount = new Map<string, number>();

    const result: ArcDatum[] = [];
    for (const ev of recent) {
      if (!ev.target_country) continue;
      const src = getCentroid(ev.actor_country);
      const tgt = getCentroid(ev.target_country);
      if (!src || !tgt) continue;

      // Turn distance → fade fraction.  Clamped to [0, 1] so an arc from
      // a turn very far in the past sits at the minimum alpha, not zero.
      const turnsOld = Math.max(0, newestTurn - ev.turn);
      const ageFraction = Math.min(1, turnsOld / ARC_FADE_TURNS);
      const alphaFactor = 1 - ageFraction * (1 - ARC_MIN_ALPHA_RATIO);
      const alpha = Math.round(220 * alphaFactor);

      let birthTime = births.get(ev.id);
      if (birthTime === undefined) {
        birthTime = nowSec;
        births.set(ev.id, birthTime);
      }

      const pairKey = `${ev.actor_country}->${ev.target_country}`;
      const pairIndex = pairCount.get(pairKey) ?? 0;
      pairCount.set(pairKey, pairIndex + 1);

      const [r, g, b] = getDomainRgba(ev.domain);
      result.push({
        id: ev.id,
        sourcePosition: [src.lon, src.lat],
        targetPosition: [tgt.lon, tgt.lat],
        color: [r, g, b, alpha],
        width: 1 + ev.escalation_rung * 0.5,
        birthTime,
        pairIndex,
        height: 1 + pairIndex * PAIR_HEIGHT_STEP,
        event: ev,
      });
    }

    // Prune birth-time map if it has grown beyond the soft cap — keep only
    // ids still referenced by the current visible set.
    if (births.size > BIRTH_MAP_PRUNE_AT) {
      const keep = new Set(visibleEvents.map((e) => e.id));
      for (const id of births.keys()) {
        if (!keep.has(id)) births.delete(id);
      }
    }

    return result;
  }, [visibleEvents]);

  const haloData: HaloDatum[] = useMemo(
    () =>
      GLOBE_COUNTRIES.map((iso3): HaloDatum | null => {
        const centroid = getCentroid(iso3);
        if (!centroid) return null;
        return {
          iso3,
          coordinates: [centroid.lon, centroid.lat],
          activityLevel: activityMap[iso3] ?? 0,
          isSelected: selectedCountry === iso3,
        };
      }).filter((x): x is HaloDatum => x !== null),
    [activityMap, selectedCountry],
  );

  return { scatterData, arcData, haloData };
}
