import { useEffect, useRef } from 'react'

export interface ProgressEntry {
  text: string
  type: 'step' | 'error' | 'done'
  time: string
}

interface Props {
  visible: boolean
  isRunning: boolean
  progress: ProgressEntry[]
}

export default function ProgressPanel({ visible, isRunning, progress }: Props) {
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [progress])

  if (!visible) return null

  return (
    <div className="progress-panel active">
      <h3>
        {isRunning && <span className="spinner" />}
        Analysis Progress
      </h3>
      <div className="progress-log" ref={logRef}>
        {progress.map((entry, i) => (
          <div key={i} className={entry.type}>
            [{entry.time}] {entry.text}
          </div>
        ))}
      </div>
    </div>
  )
}
