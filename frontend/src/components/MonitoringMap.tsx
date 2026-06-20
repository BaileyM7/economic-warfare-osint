import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { MapMarker } from '../types'

interface Props {
  markers: MapMarker[]
}

const TYPE_COLORS: Record<string, string> = {
  coa_target: '#4386c3',
  monitoring_zone: '#efb16a',
  vessel: '#d23c3a',
}

const DEFAULT_COLOR = '#ffffff'

export default function MonitoringMap({ markers }: Props) {
  const mapRef = useRef<HTMLDivElement>(null)
  const mapInstance = useRef<L.Map | null>(null)

  useEffect(() => {
    if (!mapRef.current) return

    // Clean up previous map
    if (mapInstance.current) {
      mapInstance.current.remove()
      mapInstance.current = null
    }

    const map = L.map(mapRef.current, {
      zoomControl: true,
      attributionControl: true,
      center: [10, 115],
      zoom: 4,
    })
    mapInstance.current = map

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OpenStreetMap &copy; CARTO',
      maxZoom: 19,
    }).addTo(map)

    markers.forEach((marker) => {
      const color = TYPE_COLORS[marker.type] || DEFAULT_COLOR

      L.circleMarker([marker.lat, marker.lon], {
        radius: 6,
        color,
        fillColor: color,
        fillOpacity: 0.8,
        weight: 2,
      })
        .bindTooltip(
          `<strong>${marker.label}</strong><br/>${marker.type}`,
          { direction: 'top', offset: [0, -8] },
        )
        .addTo(map)
    })

    return () => {
      if (mapInstance.current) {
        mapInstance.current.remove()
        mapInstance.current = null
      }
    }
  }, [markers])

  return (
    <div
      ref={mapRef}
      style={{ minHeight: '300px', width: '100%', height: '100%' }}
    />
  )
}
