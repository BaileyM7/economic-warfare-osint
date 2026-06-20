import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { RoutePoint } from '../types'

interface Props {
  points: RoutePoint[]
  vesselName?: string
}

export default function AISRouteMap({ points, vesselName }: Props) {
  const mapRef = useRef<HTMLDivElement>(null)
  const mapInstance = useRef<L.Map | null>(null)

  useEffect(() => {
    if (!mapRef.current || !points.length) return

    // Clean up previous map
    if (mapInstance.current) {
      mapInstance.current.remove()
      mapInstance.current = null
    }

    const map = L.map(mapRef.current, { zoomControl: true, attributionControl: true })
    mapInstance.current = map

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OpenStreetMap &copy; CARTO',
      maxZoom: 19,
    }).addTo(map)

    const sorted = [...points]
      .filter(p => p.lat && p.lon)
      .sort((a, b) => (a.ts || 0) - (b.ts || 0))

    if (!sorted.length) return

    const latLngs: L.LatLngExpression[] = sorted.map(p => [p.lat, p.lon])

    // Route polyline
    L.polyline(latLngs, {
      color: '#4386c3',
      weight: 2,
      opacity: 0.8,
    }).addTo(map)

    // Start marker (green)
    const first = sorted[0]
    L.circleMarker([first.lat, first.lon], {
      radius: 6,
      color: '#4386c3',
      fillColor: '#4386c3',
      fillOpacity: 1,
    })
      .bindTooltip(
        `Start${first.ts ? ': ' + new Date(first.ts * 1000).toLocaleDateString() : ''}`,
        { permanent: false },
      )
      .addTo(map)

    // End marker (red) — current position
    const last = sorted[sorted.length - 1]
    L.circleMarker([last.lat, last.lon], {
      radius: 7,
      color: '#d23c3a',
      fillColor: '#d23c3a',
      fillOpacity: 1,
    })
      .bindTooltip(
        `${vesselName || 'Current'}${last.speed != null ? ' — ' + last.speed + ' kn' : ''}${last.ts ? '\n' + new Date(last.ts * 1000).toLocaleDateString() : ''}`,
        { permanent: false },
      )
      .addTo(map)

    // Fit bounds with padding
    map.fitBounds(L.latLngBounds(latLngs).pad(0.3), { maxZoom: 8 })

    return () => {
      if (mapInstance.current) {
        mapInstance.current.remove()
        mapInstance.current = null
      }
    }
  }, [points, vesselName])

  return (
    <div
      ref={mapRef}
      style={{
        height: '380px',
        borderRadius: '8px',
        border: '1px solid #1f3864',
        background: '#061033',
      }}
    />
  )
}
