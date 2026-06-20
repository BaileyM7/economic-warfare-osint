import { useEffect, useRef } from 'react'
import type { ProgressEntry } from '../types'

interface Props {
  entries: ProgressEntry[]
  loading: boolean
}

export default function ProgressPanel({ entries, loading }: Props) {
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [entries])

  return (
    <div className="bg-surface-container rounded-xl p-4 mb-6 border border-outline-variant/10">
      <h3 className="text-xs font-headline font-bold uppercase tracking-widest text-on-surface-variant mb-3 flex items-center gap-2">
        {loading && (
          <span className="inline-block w-3 h-3 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        )}
        Analysis Progress
      </h3>
      <div ref={logRef} className="max-h-48 overflow-y-auto space-y-1 font-mono text-[11px]">
        {entries.map((entry, i) => (
          <div
            key={i}
            className={
              entry.type === 'error' ? 'text-error' :
              entry.type === 'done' ? 'text-secondary' :
              'text-on-surface-variant'
            }
          >
            {entry.time && <span className="text-tertiary mr-2">[{entry.time}]</span>}
            {entry.msg}
          </div>
        ))}
      </div>
    </div>
  )
}
