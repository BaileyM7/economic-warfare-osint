/**
 * useWorldLayers — shared Deck.gl layer data hook for both Globe and MapView.
 *
 * Accepts viewMode to adjust radius scaling between the two projections.
 * Returns layers array plus interaction callbacks ready to wire into <DeckGL>.
 */

import { useEffect, useMemo, useState, useCallback } from 'react';
import {
  ScatterplotLayer,
  ArcLayer,
  TextLayer,
  GeoJsonLayer,
  IconLayer,
} from '@deck.gl/layers';
import type { PickingInfo } from '@deck.gl/core';
import type { Feature, FeatureCollection, Geometry } from 'geojson';
// world-atlas ships without TS types; cast to any at the topo boundary
import countries110m from 'world-atlas/countries-110m.json';
import { feature } from 'topojson-client';
import { useSimStore } from '@/lib/store/simStore';
import { useGlobeData, type ArcDatum } from './useGlobeData';
import { makeCountryHaloLayer } from './CountryHaloLayer';
import { makeBilateralTiesLayers } from './BilateralTiesLayer';
import { useBilateralTies } from '@/hooks/useBilateralTies';
import { PulseArcExtension } from './pulseArcExtension';
import type { SimEvent } from '@/lib/types/sim-event';

// ── Arrow icon atlas ──────────────────────────────────────────────────────
// Canvas-drawn right-pointing triangle arrow. Colored at render time via
// IconLayer's getColor (mask: true), so this atlas is pure alpha/white.
const ARROW_ICON_SIZE = 64;
const ARROW_CANVAS: HTMLCanvasElement | null = (() => {
  if (typeof document === 'undefined') return null;
  const c = document.createElement('canvas');
  c.width = ARROW_ICON_SIZE;
  c.height = ARROW_ICON_SIZE;
  const ctx = c.getContext('2d');
  if (!ctx) return null;
  ctx.fillStyle = 'white';
  ctx.beginPath();
  // Arrowhead, pointing east (+x). Slightly concave back so it reads as an
  // arrowhead rather than a triangle-fill.
  ctx.moveTo(8, 10);
  ctx.lineTo(56, 32);
  ctx.lineTo(8, 54);
  ctx.lineTo(22, 32);
  ctx.closePath();
  ctx.fill();
  return c;
})();
const ARROW_ICON_MAPPING = {
  arrow: {
    x: 0,
    y: 0,
    width: ARROW_ICON_SIZE,
    height: ARROW_ICON_SIZE,
    mask: true,
    anchorX: ARROW_ICON_SIZE / 2,
    anchorY: ARROW_ICON_SIZE / 2,
  },
};

// ── Arrow flow along arcs ────────────────────────────────────────────────
// How many arrows stream along each arc at once. 4 reads well at normal zoom.
const ARROWS_PER_ARC = 4;
// Fraction of arc traversed per second. 0.025 = one arrow takes ~40 s end-to-end.
// Slow enough to read as a traveling procession, not a blur.
const ARROW_SPEED = 0.025;
// Bearing is computed by looking this far ahead in t-space.
const ARROW_BEARING_DT = 0.015;
// Module-load epoch used to keep the animation time small (avoids float
// precision loss when Date.now()/1000 ~ 1.78e9 gets multiplied by speed).
const EPOCH_MS = Date.now();

interface ArrowFlowPoint {
  id: string;
  position: [number, number];
  angle: number;
  color: [number, number, number, number];
  width: number;
  event: SimEvent;
}

/** Convert lon/lat (deg) → unit-sphere XYZ. */
function lonLatToXyz(lon: number, lat: number): [number, number, number] {
  const lr = (lon * Math.PI) / 180;
  const pr = (lat * Math.PI) / 180;
  const cp = Math.cos(pr);
  return [cp * Math.cos(lr), cp * Math.sin(lr), Math.sin(pr)];
}
function xyzToLonLat(x: number, y: number, z: number): [number, number] {
  const r = Math.sqrt(x * x + y * y + z * z) || 1;
  return [(Math.atan2(y, x) * 180) / Math.PI, (Math.asin(z / r) * 180) / Math.PI];
}
/** Great-circle slerp on unit sphere. */
function slerp(
  src: [number, number],
  tgt: [number, number],
  t: number,
): [number, number] {
  const a = lonLatToXyz(src[0], src[1]);
  const b = lonLatToXyz(tgt[0], tgt[1]);
  const dot = Math.max(-1, Math.min(1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]));
  const theta = Math.acos(dot);
  if (theta < 1e-4) return [...src];
  const st = Math.sin(theta);
  const wa = Math.sin((1 - t) * theta) / st;
  const wb = Math.sin(t * theta) / st;
  return xyzToLonLat(
    wa * a[0] + wb * b[0],
    wa * a[1] + wb * b[1],
    wa * a[2] + wb * b[2],
  );
}
/** Flat Mercator linear interpolation. */
function lerp2(
  src: [number, number],
  tgt: [number, number],
  t: number,
): [number, number] {
  return [src[0] + (tgt[0] - src[0]) * t, src[1] + (tgt[1] - src[1]) * t];
}

/**
 * Drives arrow-flow computation at ~20 fps via a lightweight interval tick.
 * Returns fresh arrow positions each tick so the IconLayer animates smoothly.
 */
function useArrowFlow(
  arcData: ReturnType<typeof useGlobeData>['arcData'],
  viewMode: 'globe' | 'map',
): ArrowFlowPoint[] {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 50); // 20 fps
    return () => clearInterval(id);
  }, []);

  return useMemo(() => {
    // Use elapsed-since-epoch so the number stays small (float precision).
    const tSec = (now - EPOCH_MS) / 1000;
    const interp = viewMode === 'globe' ? slerp : lerp2;
    const out: ArrowFlowPoint[] = [];
    for (const arc of arcData) {
      for (let i = 0; i < ARROWS_PER_ARC; i++) {
        const u = (tSec * ARROW_SPEED + i / ARROWS_PER_ARC) % 1;
        const pos = interp(arc.sourcePosition, arc.targetPosition, u);
        const ahead = interp(
          arc.sourcePosition,
          arc.targetPosition,
          Math.min(1, u + ARROW_BEARING_DT),
        );
        // 180° flip: deck.gl's IconLayer `getAngle` with `billboard: false`
        // on GlobeView rotates the sprite in a tangent-plane frame whose
        // y-axis runs OPPOSITE to the motion direction we want. Reversing
        // the diff (pos - ahead instead of ahead - pos) flips by 180.
        const angle =
          (Math.atan2(pos[1] - ahead[1], pos[0] - ahead[0]) * 180) / Math.PI;
        out.push({
          id: `${arc.id}-${i}`,
          position: pos,
          angle,
          color: arc.color,
          width: arc.width,
          event: arc.event,
        });
      }
    }
    return out;
  }, [arcData, viewMode, now]);
}

// Parse the TopoJSON → GeoJSON once, at module load. ~100KB of countries.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const _topo = countries110m as any;
const WORLD_GEOJSON: FeatureCollection<Geometry> = feature(
  _topo,
  _topo.objects['countries'],
) as unknown as FeatureCollection<Geometry>;

// Continent fill: brand deep navy (#0b2451)
const BASEMAP_FILL: [number, number, number, number] = [11, 36, 81, 255];
// Country outline: faint white at ~40% alpha
const BASEMAP_STROKE: [number, number, number, number] = [255, 255, 255, 100];

export interface WorldLayersOptions {
  viewMode: 'globe' | 'map';
  onCountryClick: (iso3: string) => void;
  onEventClick: (event: SimEvent) => void;
  /** Stable extension instance — callers should useMemo this once. */
  pulseExtension: PulseArcExtension;
  /** Current animation time offset in seconds (driven by rAF). */
  animOffset?: number;
}

export interface WorldLayersResult {
  layers: ReturnType<typeof buildLayers>;
  cursor: string;
  handleClick: (info: PickingInfo) => void;
}

/**
 * Per-view tuning constants.  Globe and MapView share the same layer
 * pipeline but need different visual sizing — on the flat Mercator
 * projection at the default zoom, globe-sized halos swallow their
 * countries, labels overlap the scatter nodes, and the default arc
 * paraboloid (height=1) bulges into a ridiculous dome because there's no
 * sphere to wrap it around.  These constants scale each affected layer.
 */
interface ViewTuning {
  /** Multiplier on scatter + halo radius. */
  radiusScale: number;
  /** Multiplier on ArcLayer getHeight — flat wants flatter arcs. */
  arcHeightScale: number;
  /** Country-label vertical pixel offset from the scatter centroid. */
  textOffsetY: number;
  /** Country-label font size in pixels. */
  textSize: number;
}

const VIEW_TUNING: Record<'globe' | 'map', ViewTuning> = {
  globe: {
    radiusScale: 1.0,
    arcHeightScale: 1.0,
    textOffsetY: 28,
    textSize: 11,
  },
  map: {
    // Halos are sized in METERS on both views; the flat map's default zoom
    // makes 120km halos cover the whole country so we shrink them.
    radiusScale: 0.72,
    // 0.25 produces a gentle paraboloid — noticeable but not a dome.
    arcHeightScale: 0.25,
    // On flat, 28px offset puts the label on top of a neighbour's halo at
    // the slice's density (JPN/KOR overlap).  42px pushes below cleanly.
    textOffsetY: 42,
    textSize: 12,
  },
};

function buildLayers(
  viewMode: 'globe' | 'map',
  scatterData: ReturnType<typeof useGlobeData>['scatterData'],
  arcData: ReturnType<typeof useGlobeData>['arcData'],
  haloData: ReturnType<typeof useGlobeData>['haloData'],
  arrowFlow: ArrowFlowPoint[],
  pulseExtension: PulseArcExtension,
  animOffset: number,
  bilateralTieLayers: ReturnType<typeof makeBilateralTiesLayers>,
) {
  const tuning = VIEW_TUNING[viewMode];
  const rScale = tuning.radiusScale;

  // Map-only: deterministic lateral offset perpendicular to each arc's
  // source→target line. Turns same-endpoint arc piles into parallel tracks.
  // See the long-form comment at the ArcLayer below.
  const lateralOffset = (d: ArcDatum): [number, number] => {
    if (viewMode !== 'map') return [0, 0];
    let h = 5381;
    for (let i = 0; i < d.id.length; i++) h = ((h << 5) + h) ^ d.id.charCodeAt(i);
    const hashNorm = ((h >>> 0) % 1000) / 1000 - 0.5; // [-0.5, 0.5]
    const magnitude = d.pairIndex * 0.8 + hashNorm * 1.6; // degrees
    const [slon, slat] = d.sourcePosition;
    const [tlon, tlat] = d.targetPosition;
    const dx = tlon - slon;
    const dy = tlat - slat;
    const len = Math.sqrt(dx * dx + dy * dy) || 1;
    return [(-dy / len) * magnitude, (dx / len) * magnitude];
  };

  return [
    // Layer -1: World basemap (filled continents + thin outlines). Required for
    // GlobeView — without a surface layer the sphere is invisible; only the
    // floating nodes show. For MapView this also gives the map a body.
    new GeoJsonLayer<Feature<Geometry>>({
      id: 'world-basemap',
      data: WORLD_GEOJSON,
      stroked: true,
      filled: true,
      getFillColor: BASEMAP_FILL,
      getLineColor: BASEMAP_STROKE,
      lineWidthMinPixels: 1,
      lineWidthMaxPixels: 1,
      pickable: false,
    }),

    // Layer -0.5: Bilateral ties (map-only).  Drawn between basemap and
    // halos so persistent sanction/agreement lines sit behind every other
    // interactive element — they're background context, not foreground
    // signal.  Empty array on globe (see useWorldLayers hook body).
    ...bilateralTieLayers,

    // Layer 0: Country halos (below nodes)
    makeCountryHaloLayer(haloData, animOffset),

    // Layer 1: Country scatter nodes
    new ScatterplotLayer({
      id: 'country-nodes',
      data: scatterData,
      getPosition: (d) => d.coordinates,
      getRadius: (d) => (60_000 + d.activityLevel * 80_000) * rScale,
      getFillColor: (d) =>
        d.isSelected
          ? ([67, 134, 195, 255] as [number, number, number, number])
          : ([67, 134, 195, Math.round(180 + d.activityLevel * 75)] as [number, number, number, number]),
      getLineColor: [255, 255, 255, 60] as [number, number, number, number],
      stroked: true,
      lineWidthMinPixels: 1,
      radiusUnits: 'meters',
      pickable: true,
      updateTriggers: {
        getRadius: [scatterData, viewMode],
        getFillColor: [scatterData],
      },
    }),

    // Layer 2: Growing arcs — PulseArcExtension reveals each arc from origin
    // to destination over ~1.5 s, then leaves it statically drawn.
    // Same-pair arcs get staggered heights via `getHeight` so they fan apart
    // instead of rendering on top of each other.
    // Layer 2: Growing arcs with map-only lateral offset.
    // On the flat map, arcs between shared endpoints (USA→CHN, USA→JPN,
    // USA→KOR…) all converge to the same points and — with height variation
    // alone — collapse into an indistinguishable pile. Shifting each arc's
    // endpoints perpendicular to its source→target line by a small
    // deterministic amount turns that pile into distinct parallel tracks.
    // Magnitude combines d.pairIndex (same-pair fan-out) with hash(event.id)
    // (different-pair separation). On the globe the offset is zero.
    new ArcLayer({
      id: 'event-arcs',
      data: arcData,
      getSourcePosition: (d) => {
        const [ox, oy] = lateralOffset(d);
        return [d.sourcePosition[0] + ox, d.sourcePosition[1] + oy];
      },
      getTargetPosition: (d) => {
        const [ox, oy] = lateralOffset(d);
        return [d.targetPosition[0] + ox, d.targetPosition[1] + oy];
      },
      getSourceColor: (d) => d.color,
      getTargetColor: (d) => d.color,
      // Noticeably thicker so the arcs read on a projector AND the picking
      // hitbox is generous — easy to click even on a fast-moving globe.
      getWidth: (d) => 2.5 + d.width,
      // Per-view scale: map wants a gentler paraboloid so arcs don't bulge
      // into a dome against the flat basemap; globe keeps the full height.
      getHeight: (d) => {
        const base = d.height * tuning.arcHeightScale;
        if (viewMode !== 'map') return base;
        // Small per-arc jitter on top of the pair-index stagger, so even
        // single-pair-arc overlays differ in apex height.
        let h = 5381;
        for (let i = 0; i < d.id.length; i++) h = ((h << 5) + h) ^ d.id.charCodeAt(i);
        const jitter = ((h >>> 0) % 1000) / 1000;
        return base + jitter * 0.35;
      },
      getArcStartTime: (d: ArcDatum) => d.birthTime,
      widthMinPixels: 4,
      widthMaxPixels: 10,
      // Great-circle paths go over the 3D sphere — ideal on the globe. On a
      // flat Mercator map, USA↔Asia great-circles pass over the pole, which
      // projects to arcs that shoot off the top of the canvas. Flat mode uses
      // a plain 2D parabola instead so arcs always stay within the visible map.
      greatCircle: viewMode === 'globe',
      pickable: true,
      autoHighlight: true,
      highlightColor: [255, 255, 255, 160],
      extensions: [pulseExtension],
      updateTriggers: {
        getSourcePosition: [arcData, viewMode],
        getTargetPosition: [arcData, viewMode],
        getHeight: [arcData, viewMode],
        getArcStartTime: [arcData],
      },
    }),

    // Layer 2b: Arrow flow — N arrowheads slowly streaming along each arc
    // from source → target. Visual cue for direction-of-action. Computed in
    // JS each tick (20fps) via slerp for globe / lerp for map; cheap for
    // ~12 arcs × 4 arrows = 48 points per frame.
    new IconLayer({
      id: 'event-arrow-flow',
      data: arrowFlow,
      iconAtlas: ARROW_CANVAS as unknown as string, // Deck.gl accepts canvas
      iconMapping: ARROW_ICON_MAPPING,
      getIcon: () => 'arrow',
      sizeUnits: 'pixels',
      getSize: (d: ArrowFlowPoint) => 12 + d.width * 1.1,
      getPosition: (d: ArrowFlowPoint) => d.position,
      getColor: (d: ArrowFlowPoint) => d.color,
      getAngle: (d: ArrowFlowPoint) => d.angle,
      billboard: false,
      pickable: false,
      updateTriggers: {
        getPosition: [arrowFlow],
        getAngle: [arrowFlow],
      },
    }),

    // Layer 3: Country ISO-3 labels
    new TextLayer({
      id: 'country-labels',
      data: scatterData,
      getPosition: (d) => d.coordinates,
      getText: (d) => d.iso3,
      getSize: tuning.textSize,
      getColor: [191, 200, 208, 180] as [number, number, number, number],
      getPixelOffset: [0, tuning.textOffsetY] as [number, number],
      fontFamily: 'monospace',
      fontWeight: 'bold',
      characterSet: 'auto',
      pickable: false,
      updateTriggers: {
        getPixelOffset: [viewMode],
        getSize: [viewMode],
      },
    }),
  ];
}

export function useWorldLayers({
  viewMode,
  onCountryClick,
  onEventClick,
  pulseExtension,
  animOffset = 0,
}: WorldLayersOptions): WorldLayersResult {
  const { scatterData, arcData, haloData } = useGlobeData();
  const arrowFlow = useArrowFlow(arcData, viewMode);
  const ties = useBilateralTies();

  // Bilateral ties only render on the flat map — they tangle with arcs on
  // the globe without visual payoff.  Compute the layer list here (not in
  // buildLayers) so the hook stays the single source of per-view gating.
  const bilateralTieLayers = useMemo(
    () => (viewMode === 'map' ? makeBilateralTiesLayers(ties) : []),
    [viewMode, ties],
  );

  const layers = useMemo(
    () =>
      buildLayers(
        viewMode,
        scatterData,
        arcData,
        haloData,
        arrowFlow,
        pulseExtension,
        animOffset,
        bilateralTieLayers,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      viewMode,
      scatterData,
      arcData,
      haloData,
      arrowFlow,
      pulseExtension,
      animOffset,
      bilateralTieLayers,
    ],
  );

  const handleClick = useCallback(
    (info: PickingInfo) => {
      if (!info.object) {
        // Clear selection on empty-space click
        useSimStore.getState().setSelectedCountry(null);
        useSimStore.getState().setSelectedEvent(null);
        return;
      }
      const obj = info.object as Record<string, unknown>;
      if ('iso3' in obj) {
        onCountryClick(obj.iso3 as string);
        return;
      }
      if ('event' in obj) {
        onEventClick(obj.event as SimEvent);
      }
    },
    [onCountryClick, onEventClick],
  );

  return { layers, cursor: 'default', handleClick };
}

// Re-export buildLayers return type helper
export type LayerList = ReturnType<typeof buildLayers>;
