import { useEffect, useRef, useState, useCallback } from 'react'
import type { KPIData, ActivityEntry } from '../types'
import { getToken } from '../api'

export function useMonitoringSocket() {
  const [kpis, setKpis] = useState<KPIData | null>(null)
  const [latestActivity, setLatestActivity] = useState<ActivityEntry | null>(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<number>()

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const token = getToken()
    const ws = new WebSocket(
      `${protocol}//${host}/ws/monitoring${token ? `?token=${encodeURIComponent(token)}` : ''}`
    )
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => {
      setConnected(false)
      // Reconnect after 5 seconds
      reconnectTimer.current = window.setTimeout(connect, 5000)
    }
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'kpi_update') {
          setKpis(data.kpis)
          if (data.latest_activity) setLatestActivity(data.latest_activity)
        }
      } catch { /* ignore parse errors */ }
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { kpis, latestActivity, connected }
}
