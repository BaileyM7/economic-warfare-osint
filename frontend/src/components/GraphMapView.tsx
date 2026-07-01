import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { GraphNode, GraphEdge } from '../types'
import { resolveCentroid } from '../lib/countryCentroids'

// Phase 5 (#38): geographic view mode. Places entities at their country on a dark
// world map and draws relationships as arcs — the literally-spatial lens for the
// same graph. Reuses Leaflet (already a dependency; the Monitoring map uses it).

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  onNodeClick?: (nodeId: string) => void
}

const SANCTIONED_COLORS = new Set(['#F85149', '#d23c3a', '#ff5a58'])

function parseCountry(title?: string): string | null {
  if (!title) return null
  const line = title.split('\n')[1]
  if (!line) return null
  const parts = line.split('·')
  return parts.length > 1 ? parts[1].trim() : null
}

// Quadratic-bezier arc between two lat/lon points, sampled into a polyline.
function arcPoints(a: [number, number], b: [number, number], k = 0.18): [number, number][] {
  const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
  const dLat = b[0] - a[0]
  const dLon = b[1] - a[1]
  const ctrl = [mid[0] - dLon * k, mid[1] + dLat * k]
  const pts: [number, number][] = []
  for (let t = 0; t <= 1.0001; t += 0.05) {
    const u = 1 - t
    pts.push([
      u * u * a[0] + 2 * u * t * ctrl[0] + t * t * b[0],
      u * u * a[1] + 2 * u * t * ctrl[1] + t * t * b[1],
    ])
  }
  return pts
}

export default function GraphMapView({ nodes, edges, onNodeClick }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const clickRef = useRef(onNodeClick)
  clickRef.current = onNodeClick
  const [unlocated, setUnlocated] = useState<string[]>([])

  useEffect(() => {
    if (!containerRef.current) return
    const map = L.map(containerRef.current, {
      zoomControl: true,
      attributionControl: false,
      worldCopyJump: false,
    })
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 8,
    }).addTo(map)
    mapRef.current = map

    // group nodes by country centroid, jittering when several share a country
    const groups = new Map<string, GraphNode[]>()
    const missing: GraphNode[] = []
    for (const n of nodes) {
      const c = resolveCentroid(parseCountry(n.title))
      if (!c) {
        missing.push(n)
        continue
      }
      const key = `${c.lat},${c.lon}`
      const arr = groups.get(key) ?? []
      arr.push(n)
      groups.set(key, arr)
    }
    const pos = new Map<string, [number, number]>()
    groups.forEach((arr) => {
      arr.forEach((n, i) => {
        const c = resolveCentroid(parseCountry(n.title))!
        const ang = (2 * Math.PI * i) / arr.length
        const r = arr.length > 1 ? 5.5 : 0
        pos.set(n.id, [c.lat + r * Math.sin(ang), c.lon + r * Math.cos(ang)])
      })
    })
    setUnlocated(missing.map((n) => n.label))

    // arcs
    edges.forEach((e) => {
      const a = pos.get(e.from)
      const b = pos.get(e.to)
      if (!a || !b) return
      L.polyline(arcPoints(a, b), {
        color: e.dashes ? '#a371f7' : '#4386c3',
        weight: e.width ?? 2,
        opacity: 0.6,
        dashArray: e.dashes ? '6 6' : undefined,
      }).addTo(map)
    })

    // markers
    nodes.forEach((n) => {
      const p = pos.get(n.id)
      if (!p) return
      const sanctioned = n.riskLevel === 'HIGH' || (n.color ? SANCTIONED_COLORS.has(n.color) : false)
      const m = L.circleMarker(p, {
        radius: 7 + (n.value ?? 1) * 1.6,
        fillColor: n.color || '#8899bb',
        fillOpacity: 0.92,
        color: sanctioned ? '#ff3b3b' : '#0a1636',
        weight: sanctioned ? 3 : 1.5,
      }).addTo(map)
      m.bindTooltip(n.label, {
        permanent: true,
        direction: 'right',
        className: 'graph-map-label',
        offset: [8, 0],
      })
      m.on('click', () => clickRef.current?.(n.id))
    })

    const placed = [...pos.values()]
    if (placed.length) map.fitBounds(placed as L.LatLngBoundsExpression, { padding: [55, 55] })
    else map.setView([25, 20], 2)

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [nodes, edges])

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <style>{`.graph-map-label{background:rgba(6,16,51,.72);border:none;color:#dbe6fb;font-size:11px;font-weight:600;box-shadow:none;padding:1px 5px;}.graph-map-label::before{display:none;} .leaflet-container{background:#0a1020;border-radius:8px;}`}</style>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      {unlocated.length > 0 && (
        <div className="absolute bottom-2 left-2 z-[500] text-[11px] text-outline bg-surface-container-lowest/85 px-2.5 py-1 rounded border border-outline-variant/20 max-w-[50%]">
          {unlocated.length} without a country: {unlocated.slice(0, 4).join(', ')}
          {unlocated.length > 4 ? '…' : ''}
        </div>
      )}
    </div>
  )
}
