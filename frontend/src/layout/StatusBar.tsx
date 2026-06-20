import { useEffect, useState } from 'react'
import { fetchHealth } from '../api'
import type { HealthResponse } from '../types'

export default function StatusBar() {
  const [health, setHealth] = useState<HealthResponse | null>(null)

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => {})
    const id = setInterval(() => {
      fetchHealth().then(setHealth).catch(() => setHealth(null))
    }, 30_000)
    return () => clearInterval(id)
  }, [])

  const isOk = health?.status === 'ok'
  const statusText = health ? (isOk ? 'SYSTEM READY' : `DEGRADED: ${health.issues[0]}`) : 'CONNECTING...'
  const statusColor = health ? (isOk ? 'text-secondary' : 'text-error') : 'text-outline'
  const dotColor = health ? (isOk ? 'bg-secondary' : 'bg-error') : 'bg-outline'

  return (
    <footer className="fixed bottom-0 left-0 w-full h-6 bg-surface-container-lowest border-t border-outline-variant/15 flex items-center justify-between px-4 z-50">
      <div className="flex gap-4 items-center h-full ml-[240px]">
        <div className={`text-[9px] font-mono ${statusColor} flex items-center gap-1`}>
          <span className={`w-1.5 h-1.5 ${dotColor} rounded-full`} /> {statusText}
        </div>
        <div className="text-[9px] font-mono text-outline border-l border-outline-variant/30 pl-4">
          COMMS: {health ? 'UP' : '--'} | MODEL: {health?.model?.split('-').slice(0, 2).join('-') ?? '--'}
        </div>
      </div>
      <div className="flex items-center gap-4 h-full">
        <div className="text-[9px] font-mono text-on-surface-variant font-bold uppercase tracking-widest">
          DEMO
        </div>
      </div>
    </footer>
  )
}
